"""Fixed limits for the SatRush experiment. All money values are Decimal strings."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal

TILES = 21


def D(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not money")
    x = Decimal(str(value))
    if not x.is_finite():
        raise ValueError("Nonfinite quantity")
    return x


def down(value, step):
    return (D(value) / D(step)).to_integral_value(rounding=ROUND_DOWN) * D(step)


@dataclass(frozen=True)
class Settings:
    network: str = "mainnet"
    capital: str = "100"  # Maximum USDC the dedicated wallet may hold at start.
    stake: str = "1"  # USDC deployed per round, split evenly across chosen tiles.
    loss_stop: str = "20"
    daily_deploys: int = 300
    min_slots_remaining: int = 40  # Never deploy into a round about to close.
    max_board_age: float = 20.0  # Seconds; the board snapshot must be fresh.
    neural_ms: float = 840  # Neural time budget per observation: up to 21 picks of step_ms.
    step_ms: float = 40  # Neural time per pick: one tile is chosen (or the fly stops) every step.
    neural_bin_ms: float = 10
    pulse_ms: float = 200
    pulse_current: float = 20
    reward_deadband: str = "0.01"
    min_tiles: int = 1
    max_tiles: int = 21
    priority_fee_microlamports: int = 0
    learning: bool = True

    def __post_init__(self):
        if self.network not in ("mainnet", "devnet"):
            raise ValueError("network must be mainnet or devnet")
        if not 0 < D(self.capital) <= 100:
            raise ValueError("Maximum capital 100 USDC")
        if not 1 <= D(self.stake) <= min(D(self.capital), D(10)):
            raise ValueError("Stake must be between the 1 USDC program minimum and 10 USDC")
        if D(self.stake) != down(self.stake, "0.000001"):
            raise ValueError("Stake precision is 6 decimals")
        if not 0 < D(self.loss_stop) <= D(self.capital):
            raise ValueError("Invalid loss stop")
        if type(self.daily_deploys) is not int or not 1 <= self.daily_deploys <= 1440:
            raise ValueError("Daily deploys: 1-1440 (rounds last about a minute)")
        if type(self.min_slots_remaining) is not int or not 5 <= self.min_slots_remaining <= 150:
            raise ValueError("min_slots_remaining: 5-150 slots")
        if not math.isfinite(self.max_board_age) or self.max_board_age <= 0:
            raise ValueError("Positive board age limit required")
        if D(self.reward_deadband) <= 0:
            raise ValueError("Positive reinforcement deadband required")
        if not (
            type(self.min_tiles) is int
            and type(self.max_tiles) is int
            and 1 <= self.min_tiles <= self.max_tiles <= TILES
        ):
            raise ValueError("Tile bounds must satisfy 1 <= min <= max <= 21")
        if type(self.priority_fee_microlamports) is not int or not (
            0 <= self.priority_fee_microlamports <= 100_000
        ):
            raise ValueError("Priority fee: 0-100000 microlamports per compute unit")
        for x in [self.neural_ms, self.step_ms, self.neural_bin_ms, self.pulse_ms, self.pulse_current]:
            if not math.isfinite(x) or x <= 0:
                raise ValueError("Positive finite parameter required")
        if self.neural_bin_ms > 10 or self.pulse_ms > self.neural_ms:
            raise ValueError("Use <=10 ms neural bins; pulse must fit a decision window")
        if self.step_ms < self.neural_bin_ms or self.step_ms > self.neural_ms:
            raise ValueError("A pick step must be at least one neural bin and at most the observation budget")
        if any(
            abs(x * 10 - round(x * 10)) > 1e-7
            for x in [self.neural_ms, self.step_ms, self.neural_bin_ms, self.pulse_ms]
        ):
            raise ValueError("Neural intervals must be multiples of 0.1 ms")

    @property
    def max_steps(self):
        """Pick steps that fit the observation budget, at most one per tile."""
        return max(1, min(TILES, int(self.neural_ms / self.step_ms + 1e-9)))

    def signature(self):
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True).encode()
        ).hexdigest()
