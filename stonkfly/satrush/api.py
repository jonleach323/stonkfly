"""Read-only SatRush public API. The fixture is an explicit synthetic stand-in."""

import json
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from ..errors import Transient
from .rules import TILES

USER_AGENT = "stonkfly/0.2 (+https://github.com/jonleach323/stonkfly)"

ENDPOINTS = {
    "mainnet": {"api": "https://api.satrush.io/api", "rpc": "https://rpc.satrush.io"},
    "devnet": {
        "api": "https://api-devnet.satrush.io/api",
        "rpc": "https://rpc-devnet.satrush.io",
    },
}
U64_MAX = 2**64 - 1


def utc_seconds(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / (1000 if value > 1e12 else 1)
    text = str(value).replace("Z", "+00:00")
    # Trim sub-microsecond digits that fromisoformat rejects.
    if "." in text:
        head, tail = text.split(".", 1)
        digits = ""
        rest = tail
        while rest and rest[0].isdigit():
            digits += rest[0]
            rest = rest[1:]
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest}"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("API timestamp requires a timezone")
    return parsed.timestamp()


@dataclass(frozen=True)
class Board:
    round_id: int
    round_state: str | None
    start_slot: int
    end_slot: int
    current_slot: int
    slot_ms: float
    round_started_at: float | None
    round_ends_at: float | None
    tile_stakes: tuple  # 21 (stake micro-USD, deploy count) pairs
    miners_count: int
    deployed_usd: int
    previous_round: dict | None
    previous_winners: tuple  # ((round_id, tile 1-based), ...) newest first
    prices: dict
    fetched_at: float
    raw: dict = field(repr=False, compare=False, default_factory=dict)

    @property
    def pending_activation(self):
        return self.start_slot == U64_MAX

    @property
    def slots_remaining(self):
        return self.end_slot - self.current_slot

    def seconds_remaining(self, now=None):
        now = time.time() if now is None else now
        if self.round_ends_at is not None:
            return self.round_ends_at - now
        return self.slots_remaining * self.slot_ms / 1000 - (now - self.fetched_at)

    def summary(self):
        """JSON-safe public view of the board, as shown to the retina."""
        previous = self.previous_round or {}
        return {
            "round_id": self.round_id,
            "state": self.round_state,
            "pending_activation": self.pending_activation,
            "started_at": self.round_started_at,
            "ends_at": self.round_ends_at,
            "slots_remaining": None if self.pending_activation else self.slots_remaining,
            "slot_ms": self.slot_ms,
            "pot_usd": self.deployed_usd / 1e6,
            "miners": self.miners_count,
            "tile_stakes": [{"stake_usd": s / 1e6, "miners": c} for s, c in self.tile_stakes],
            "previous_winners": [{"round_id": r, "tile": t} for r, t in self.previous_winners],
            "previous_round": {
                "round_id": previous.get("id"),
                "winning_tile": None if previous.get("winning_tile") is None else int(previous["winning_tile"]) + 1,
                "pot_sats": int(previous.get("deployed_btc_amount") or 0) or None,
                "winners": previous.get("winners_count"),
                "miners": previous.get("miners_count"),
            } if previous else None,
            "prices": {k: v for k, v in self.prices.items() if isinstance(v, (int, float))},
            "fetched_at": self.fetched_at,
        }


def parse_board(data, fetched_at=None):
    if not isinstance(data, dict) or not isinstance(data.get("round_id"), int):
        raise ValueError("Board response has no round_id")
    active = data.get("active_round") or {}
    stakes = active.get("tile_stakes") or []
    if len(stakes) != TILES:
        raise ValueError("Board must have 21 tiles")
    tiles = tuple((int(t["stake"]), int(t.get("deploy_count") or 0)) for t in stakes)
    previous = []
    for r in data.get("previous_rounds") or []:
        if isinstance(r, dict) and isinstance(r.get("round_id"), int):
            tile = r.get("winning_tile")
            if isinstance(tile, int):
                previous.append((r["round_id"], tile + 1))
    previous.sort(key=lambda x: -x[0])
    state = active.get("state")
    return Board(
        round_id=data["round_id"],
        round_state=state if isinstance(state, str) else None,
        start_slot=int(data["start_slot"]),
        end_slot=int(data["end_slot"]),
        current_slot=int(data["current_slot"]),
        slot_ms=float(data.get("slot_duration_ms") or 400),
        round_started_at=utc_seconds(data.get("round_started_at")),
        round_ends_at=utc_seconds(data.get("round_ends_at")),
        tile_stakes=tiles,
        miners_count=int(active.get("miners_count") or 0),
        deployed_usd=int(active.get("deployed_pending_usd_amount") or 0)
        + int(active.get("deployed_usd_amount") or 0),
        previous_round=data.get("previous_round"),
        previous_winners=tuple(previous),
        prices=dict(data.get("prices") or {}),
        fetched_at=time.time() if fetched_at is None else fetched_at,
        raw=data,
    )


def http_json(url, timeout=20):
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body)


class ApiError(Transient):
    """The public API answered with something other than the expected document."""


class SatRushApi:
    def __init__(self, base_url, fetch=http_json):
        self.base_url = base_url.rstrip("/")
        self.fetch = fetch

    def _get(self, path, **query):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        payload = self.fetch(url)
        if not isinstance(payload, dict) or "data" not in payload:
            raise ApiError("SatRush API answered without a data envelope")
        return payload["data"]

    def board(self):
        try:
            return parse_board(self._get("/v1/board"))
        except (ValueError, TypeError, KeyError) as e:
            raise ApiError(f"Malformed board: {type(e).__name__}") from e

    def config(self):
        return self._get("/v1/config")

    def round(self, round_id):
        return self._get(f"/v1/rounds/{int(round_id)}")

    def rounds(self, limit=20, before=None):
        query = {"limit": int(limit)}
        if before is not None:
            query["before"] = int(before)
        return self._get("/v1/rounds", **query)

    def user(self, wallet):
        return self._get(f"/v1/users/{wallet}")

    def user_deployments(self, wallet, limit=10):
        return self._get(
            f"/v1/users/{wallet}/deployments", wins_only=0, limit=int(limit)
        )


class FixtureApi:
    """Deterministic synthetic rounds for offline paper runs and tests.

    Rounds are `period` seconds long; the winning tile comes from a seeded RNG.
    Pots are synthetic. Nothing here is real market data or a real game state.
    """

    def __init__(self, period=2.0, seed=7, clock=time.time, fee_bps=600, epoch=None):
        self.period = float(period)
        self.rng = random.Random(seed)
        self.clock = clock
        self.fee_bps = fee_bps
        self.epoch = clock() if epoch is None else float(epoch)
        self.results = {}
        self.first_round = 1000
        self._stakes = [(2_000_000 + self.rng.randrange(0, 500_000), 5) for _ in range(TILES)]

    def _round_index(self, now):
        return int((now - self.epoch) // self.period)

    def _finish(self, round_id):
        if round_id not in self.results:
            # Draw rounds in order so results stay reproducible.
            for rid in range(self.first_round, round_id + 1):
                if rid not in self.results:
                    winner = self.rng.randrange(TILES)
                    stakes = [
                        (2_000_000 + self.rng.randrange(0, 500_000), 5) for _ in range(TILES)
                    ]
                    total = sum(s for s, _ in stakes)
                    haircut = sum((s * 500) // 10_000 for i, (s, _) in enumerate(stakes) if i != winner)
                    pot_usd = stakes[winner][0] + haircut
                    price = 75_000
                    pot_sats = pot_usd * 100_000_000 // (price * 1_000_000)
                    self.results[rid] = {
                        "id": rid,
                        "state": "finished",
                        "winning_tile": winner,
                        "tile_stakes": [{"stake": str(s), "deploy_count": c} for s, c in stakes],
                        "deployed_usd_on_winning_tile_amount": str(stakes[winner][0]),
                        "total_deployed_usd": str(total),
                        "total_pot_usd": str(total),
                        "total_pot_btc": str(pot_sats),
                        "total_pot_combined_usd": total / 1e6 + pot_sats / 1e8 * price,
                    }
        return self.results[round_id]

    def config(self):
        return {
            "usd_mint": "FixtureUSD111111111111111111111111111111111",
            "btc_mint": "FixtureBTC111111111111111111111111111111111",
            "strike_fee_bps": 208,
            "epoch_fee_bps": 194,
            "one_btc_fee_bps": 48,
            "protocol_fee_bps": 100,
            "buybacks_fee_bps": 50,
            "min_deploy_usd_amount": "1000000",
        }

    def board(self):
        now = self.clock()
        index = self._round_index(now)
        round_id = self.first_round + index
        started = self.epoch + index * self.period
        previous = self._finish(round_id - 1) if round_id > self.first_round else None
        winners = [
            (rid, self.results[rid]["winning_tile"] + 1)
            for rid in range(round_id - 1, max(self.first_round - 1, round_id - 6), -1)
            if rid in self.results
        ]
        slots = 200
        current = int(slots * (now - started) / self.period)
        data = {
            "round_id": round_id,
            "start_slot": str(1_000_000 + index * slots),
            "end_slot": str(1_000_000 + (index + 1) * slots),
            "current_slot": str(1_000_000 + index * slots + current),
            "slot_duration_ms": self.period * 1000 / slots,
            "round_started_at": started,
            "round_ends_at": started + self.period,
            "active_round": {
                "state": "active",
                "miners_count": 5,
                "deployed_pending_usd_amount": str(sum(s for s, _ in self._stakes)),
                "deployed_usd_amount": "0",
                "tile_stakes": [{"stake": str(s), "deploy_count": c} for s, c in self._stakes],
            },
            "previous_round": previous,
            "previous_rounds": [{"round_id": r, "winning_tile": t - 1} for r, t in winners],
            "prices": {"btc": 75_000.0},
        }
        return parse_board(data, fetched_at=now)

    def round(self, round_id):
        now = self.clock()
        if round_id >= self.first_round + self._round_index(now):
            return {"id": round_id, "state": "active", "winning_tile": None}
        return self._finish(round_id)
