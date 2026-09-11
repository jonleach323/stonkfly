import json
from pathlib import Path

import pytest

from stonkfly.satrush.api import FixtureApi, SatRushApi, parse_board, utc_seconds

BOARD = json.loads((Path(__file__).parent / "fixtures/board.json").read_text())


def test_parse_public_board():
    board = parse_board(BOARD["data"], fetched_at=0.0)
    assert board.round_id == 55576 and board.round_state == "active"
    assert board.slots_remaining == 39 and not board.pending_activation
    assert len(board.tile_stakes) == 21 and board.tile_stakes[0] == (20154744, 70)
    assert board.previous_winners[0] == (55575, 21)
    assert board.prices["btc"] > 0
    assert abs(board.round_ends_at - utc_seconds(BOARD["data"]["round_ends_at"])) < 1e-6
    assert board.round_ends_at - board.round_started_at == pytest.approx(63, abs=2)


def test_parse_rejects_bad_boards():
    with pytest.raises(ValueError):
        parse_board({"data": {}})
    with pytest.raises(ValueError):
        parse_board({**BOARD["data"], "active_round": {"tile_stakes": []}})


def test_api_client_uses_envelope():
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": BOARD["data"]} if "board" in url else {"data": {"id": 1}}

    api = SatRushApi("https://example.test/api/", fetch=fetch)
    assert api.board().round_id == 55576
    api.round(5)
    api.user_deployments("wallet", 3)
    assert calls[1] == "https://example.test/api/v1/rounds/5"
    assert calls[2] == "https://example.test/api/v1/users/wallet/deployments?wins_only=0&limit=3"
    with pytest.raises(RuntimeError):
        SatRushApi("https://example.test/api", fetch=lambda url: "<html>").board()


def test_fixture_rounds_are_deterministic():
    now = [1000.0]
    a = FixtureApi(period=2.0, seed=3, clock=lambda: now[0])
    b = FixtureApi(period=2.0, seed=3, clock=lambda: now[0])
    first = a.board().round_id
    now[0] += 4.5
    assert a.board().round_id == first + 2
    assert a.round(first)["winning_tile"] == b.round(first)["winning_tile"]
    assert a.round(first + 2)["winning_tile"] is None
    assert a.board().previous_winners[0][0] == first + 1
