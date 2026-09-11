"""Paper loop against synthetic rounds with the stub brain; no network, no wallet."""

import json

import pytest

from stonkfly.config import Settings
from stonkfly.guard import Guard
from stonkfly.ledger import Ledger
from stonkfly.loop import GameLoop
from stonkfly.neural.controller import StubController
from stonkfly.satrush.api import FixtureApi
from stonkfly.satrush.player import PaperPlayer


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "paper")
    api = FixtureApi(period=60.0, seed=1, clock=clock)
    player = PaperPlayer(settings, ledger, api)
    loop = GameLoop(
        settings, ledger, api, player, StubController(settings), Guard(settings, ledger, tmp_path / "STOP"),
        tmp_path, clock=clock, sleep=clock.sleep, settle_margin=1.0,
    )
    yield loop, ledger, clock, tmp_path
    ledger.close()


def test_one_deploy_per_round_then_settlement(world):
    loop, ledger, clock, tmp = world
    assert loop.run(steps=3) == 3
    rows = [json.loads(line) for line in (tmp / "events.jsonl").read_text().splitlines()]
    assert [r["round_id"] for r in rows] == [1000, 1001, 1002]
    assert all(r["execution"]["status"] == "PAPER" for r in rows)
    assert rows[1]["outcomes"][0]["round_id"] == 1000
    assert rows[1]["stimulus"] in ("reward", "aversive", "none")
    assert rows[1]["neural"]["stimulus"] == rows[1]["stimulus"]
    assert ledger.get("tick") == 3 and (tmp / "latest-input.png").exists()
    # Cash moves by exactly the settled outcomes; the open deploy is debited.
    settled = [r for r in ledger.with_status(["SETTLED"])]
    assert len(settled) == 2 and ledger.deployment(1002)["status"] == "PAPER"
    provenance_free = json.loads((tmp / "latest.json").read_text())
    assert "wallet" not in provenance_free


def test_stop_file_and_loss_stop(world):
    loop, ledger, clock, tmp = world
    (tmp / "STOP").touch()
    assert loop.run(steps=2) == 0
    (tmp / "STOP").unlink()
    ledger.put("cash", "79.5")
    assert loop.run(steps=2) == 0
    assert ledger.get("halted") == "Loss stop reached"


def test_closing_round_is_skipped(world):
    loop, ledger, clock, tmp = world
    clock.now = 1_000.0 + 58.0  # 6-7 slots left of 200
    assert loop.step() is None
    assert ledger.deployment(1000) is None
    assert clock.now == pytest.approx(1_063.0)  # brief poll, not a full-round wait
    assert loop.step() is not None and ledger.deployment(1001)["status"] == "PAPER"
    assert clock.now > 1_120.0  # after deploying, waited for that round to end


def test_transient_api_failure_retries_without_halting(world):
    import urllib.error

    loop, ledger, clock, tmp = world
    real = loop.api.board
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise urllib.error.URLError("api down")
        return real()

    loop.api.board = flaky
    t0 = clock.now
    assert loop.step() is None and ledger.get("halted") is None
    assert ledger.get("status")["phase"] == "retrying after URLError" and loop.failures == 1
    assert clock.now - t0 == pytest.approx(5.0, abs=1.1)  # first back-off
    assert loop.step() is None and loop.failures == 2
    assert loop.step() is not None and loop.failures == 0
    assert ledger.deployment(1000)["status"] == "PAPER"  # the round the third step played


def test_non_transient_errors_still_propagate(world):
    loop, ledger, clock, tmp = world
    loop.api.board = lambda: (_ for _ in ()).throw(RuntimeError("ledger corrupt"))
    with pytest.raises(RuntimeError):
        loop.step()
