"""Payout arithmetic checked against a real settled public round."""

import json
from pathlib import Path

import pytest

from stonkfly.satrush.rules import (
    implied_btc_price,
    mask_from_tiles,
    observed_fee_bps,
    outcome_from_record,
    per_tile_stake,
    pot_sats,
    refund_per_tile,
    round_fee_bps,
    simulate,
    tiles_from_mask,
    winning_tile_stake,
)

ROUND = json.loads((Path(__file__).parent / "fixtures/round-55575.json").read_text())["data"]
CONFIG = {"strike_fee_bps": 208, "epoch_fee_bps": 194, "one_btc_fee_bps": 48, "protocol_fee_bps": 100, "buybacks_fee_bps": 50}


def test_mask_round_trip():
    assert tiles_from_mask(949118) == [2, 3, 4, 5, 6, 7, 9, 10, 12, 13, 14, 15, 18, 19, 20]
    assert mask_from_tiles([2, 3, 4, 5, 6, 7, 9, 10, 12, 13, 14, 15, 18, 19, 20]) == 949118
    assert mask_from_tiles(range(1, 22)) == (1 << 21) - 1
    with pytest.raises(ValueError):
        mask_from_tiles([0])
    with pytest.raises(ValueError):
        mask_from_tiles([22])
    with pytest.raises(ValueError):
        tiles_from_mask(1 << 21)


def test_fee_and_refund_match_observed_settlement():
    fee = round_fee_bps(CONFIG)
    assert fee == 600 == observed_fee_bps(ROUND, 0)
    assert round_fee_bps({k: v for k, v in CONFIG.items() if k != "buybacks_fee_bps"}) == 550
    assert winning_tile_stake(ROUND) == 20019728 and pot_sats(ROUND) == 57742
    assert winning_tile_stake({**ROUND, "deployed_usd_on_winning_tile_amount": None}) == 20019728
    assert winning_tile_stake({**ROUND, "deployed_usd_on_winning_tile_amount": None, "tile_stakes": None}) == sum(
        int(d["winning_usd_stake_amount"]) for d in ROUND["deployments"]
    )
    with pytest.raises(ValueError):
        winning_tile_stake({"winning_tile": 1})
    for record in ROUND["deployments"]:
        stake = int(record["total_usd_stake_amount"])
        tiles = tiles_from_mask(record["selected_tiles"])
        losing = len(tiles) - (1 if record["is_won"] else 0)
        tile_stake = per_tile_stake(stake, len(tiles))
        assert refund_per_tile(tile_stake, fee) * losing == int(record["usd_earned"])
        if record["is_won"]:
            assert int(record["winning_usd_stake_amount"]) == tile_stake
            # Pro-rata BTC share over the winning tile's total stake.
            expected = tile_stake * int(ROUND["total_pot_btc"]) // 20019728
            assert expected == int(record["btc_earned"])


def test_implied_price_and_record_outcome():
    price = implied_btc_price(ROUND)
    assert 76_000 < price < 78_000
    winner = next(r for r in ROUND["deployments"] if r["is_won"])
    out = outcome_from_record(winner, price)
    assert out["won"] and out["sats"] == int(winner["btc_earned"])
    # Even a full-board winner loses money after fees: no free lunch.
    assert float(out["pnl_usd"]) < 0
    loser = next(r for r in ROUND["deployments"] if not r["is_won"])
    assert float(outcome_from_record(loser, price)["pnl_usd"]) < 0
    assert outcome_from_record({**winner, "settled_at": None}, price) is None


def test_simulated_extra_deploy():
    price = implied_btc_price(ROUND)
    win = simulate(1_000_000, mask_from_tiles([21]), ROUND, 600, price)
    lose = simulate(1_000_000, mask_from_tiles([1]), ROUND, 600, price)
    assert win["won"] and not lose["won"]
    assert lose["refund_usd"] == "0.89" and lose["sats"] == 0
    assert win["refund_usd"] == "0"
    # Roughly the pot's share for 0.94 USDC of a 20.96 USDC winning tile.
    assert 2500 < win["sats"] < 2800
    assert 0.9 < float(win["pnl_usd"]) < 1.3 and float(lose["pnl_usd"]) < 0
    with pytest.raises(ValueError):
        simulate(1_000_000, 1, {**ROUND, "winning_tile": None}, 600, price)
