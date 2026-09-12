"""Settlement arithmetic of the SatRush round game, reproduced from public data.

Each round has 21 tiles. A deploy of `amount` micro-USDC is reduced by the
round fee and split evenly across the selected tiles. Stakes on non-winning
tiles are refunded minus a 500 bps haircut. The winning tile's stakes plus the
haircut form a pot that is converted to BTC and shared pro rata among the
miners who selected the winning tile. Integer floors follow observed
settlements (see tests/fixtures/round-55575.json). RUSH token mints and
hashrate are ignored here; they are reported from the API when available.
"""

from decimal import Decimal

from ..config import D

TILES = 21
HAIRCUT_BPS = 500
BPS = 10_000
SATS_PER_BTC = 100_000_000


def tiles_from_mask(mask):
    if not isinstance(mask, int) or mask < 0 or mask >= 1 << TILES:
        raise ValueError("Selection mask must fit 21 bits")
    return [i + 1 for i in range(TILES) if mask >> i & 1]


def mask_from_tiles(tiles):
    tiles = sorted(set(int(t) for t in tiles))
    if not tiles or tiles[0] < 1 or tiles[-1] > TILES:
        raise ValueError("Tiles must be 1..21 and nonempty")
    mask = 0
    for t in tiles:
        mask |= 1 << (t - 1)
    return mask


def round_fee_bps(config):
    """Total deploy fee: every per-type fee in the public config, summed."""
    keys = [
        "strike_fee_bps",
        "epoch_fee_bps",
        "one_btc_fee_bps",
        "protocol_fee_bps",
        "buybacks_fee_bps",
    ]
    total = sum(int(config.get(k) or 0) for k in keys)
    if not 0 < total < BPS - HAIRCUT_BPS:
        raise ValueError("Implausible round fee")
    return total


def observed_fee_bps(round_json, fallback):
    """Fee actually charged in a finished round: gross deploys versus stakes."""
    try:
        gross = int(round_json["total_gross_deployed_usd"])
        net = int(round_json["total_deployed_usd"])
    except (KeyError, TypeError, ValueError):
        return fallback
    if gross <= 0 or not 0 <= net <= gross:
        return fallback
    fee = round((gross - net) * BPS / gross)
    return fee if 0 < fee < BPS - HAIRCUT_BPS else fallback


def pot_sats(round_json):
    for key in ("total_pot_btc", "deployed_btc_amount"):
        if round_json.get(key) is not None:
            return int(round_json[key])
    raise ValueError("Round carries no BTC pot")


def winning_tile_stake(round_json):
    """Total stake the real miners had on the winning tile."""
    value = round_json.get("deployed_usd_on_winning_tile_amount")
    if value is not None and int(value) > 0:
        return int(value)
    tiles = round_json.get("tile_stakes")
    if isinstance(tiles, list) and len(tiles) == TILES:
        return int(tiles[int(round_json["winning_tile"])]["stake"])
    deployments = round_json.get("deployments")
    if isinstance(deployments, list) and deployments:
        return sum(int(d.get("winning_usd_stake_amount") or 0) for d in deployments)
    raise ValueError("Round carries no winning-tile stake")


def stake_after_fee(amount, fee_bps):
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Deploy amount must be positive")
    return amount - (amount * fee_bps) // BPS


def per_tile_stake(stake, n_tiles):
    return int(stake) // int(n_tiles)


def refund_per_tile(tile_stake, fee_bps):
    net = BPS - fee_bps
    return (int(tile_stake) * (net - HAIRCUT_BPS)) // net


def implied_btc_price(round_json, fallback=None):
    """USD per BTC implied by a finished round's pot, else the fallback."""
    try:
        combined = D(round_json["total_pot_combined_usd"])
        usd = D(round_json["total_pot_usd"]) / D(10**6)
        sats = D(round_json["total_pot_btc"])
    except (KeyError, TypeError, ValueError):
        return None if fallback is None else D(fallback)
    if sats <= 0:
        return None if fallback is None else D(fallback)
    price = (combined - usd) / (sats / D(SATS_PER_BTC))
    return price if price > 0 else (None if fallback is None else D(fallback))


def simulate(amount, mask, round_json, fee_bps, btc_price):
    """Hypothetical settlement of one extra deploy against a finished real round.

    The deploy was not in the real pool, so the pot and winning-tile stake are
    enlarged by this deploy's own contribution. This is an approximation for
    paper play, not a chain result.
    """
    tiles = tiles_from_mask(mask)
    if round_json.get("winning_tile") is None:
        raise ValueError("Round is not finished")
    winning = int(round_json["winning_tile"]) + 1
    stake = stake_after_fee(amount, fee_bps)
    tile_stake = per_tile_stake(stake, len(tiles))
    refund_tile = refund_per_tile(tile_stake, fee_bps)
    won = winning in tiles
    losing = len(tiles) - (1 if won else 0)
    refund = refund_tile * losing
    price = D(btc_price)
    if price <= 0:
        raise ValueError("BTC price required")
    contribution_usd = losing * (tile_stake - refund_tile) + (tile_stake if won else 0)
    contribution_sats = int(D(contribution_usd) / D(10**6) / price * SATS_PER_BTC)
    pot = pot_sats(round_json) + contribution_sats
    winning_total = winning_tile_stake(round_json) + tile_stake
    sats = (tile_stake * pot) // winning_total if won else 0
    # A Sat Strike round pays its bonus (USDC and BTC from the strike vault)
    # to the winning tile pro rata, exactly like the pot (verified on round 56305).
    strike = bool(round_json.get("is_sat_strike"))
    strike_usd = 0
    strike_sats = 0
    if strike and won:
        strike_usd = (tile_stake * int(round_json.get("strike_bonus_usd") or 0)) // winning_total
        strike_sats = (tile_stake * int(round_json.get("strike_bonus_btc") or 0)) // winning_total
        sats += strike_sats
    sats_usd = D(sats) / D(SATS_PER_BTC) * price
    pnl = D(refund) / D(10**6) + D(strike_usd) / D(10**6) + sats_usd - D(amount) / D(10**6)
    return {
        "won": won,
        "winning_tile": winning,
        "tiles": tiles,
        "stake_usd": str(D(stake) / D(10**6)),
        "refund_usd": str(D(refund) / D(10**6)),
        "sats": sats,
        "sats_usd": str(sats_usd.quantize(Decimal("0.000001"))),
        "strike": strike,
        "strike_usd": str(D(strike_usd) / D(10**6)),
        "strike_sats": strike_sats,
        "btc_price": format(price, "f"),
        "pnl_usd": str(pnl.quantize(Decimal("0.000001"))),
        "simulated": True,
    }


def outcome_from_record(record, btc_price, round_json=None):
    """Round P&L from an API deployment record (real on-chain settlement).

    `usd_earned` and `btc_earned` already include any Sat Strike bonus; the
    round detail, when given, tags the round as a strike.
    """
    if record.get("settled_at") is None or record.get("is_won") is None:
        return None
    deployed = D(record["deployed_usd_amount"]) / D(10**6)
    refund = D(record.get("usd_earned") or 0) / D(10**6)
    sats = int(record.get("btc_earned") or 0)
    sats_usd = (
        D(record["btc_earned_usd"])
        if record.get("btc_earned_usd") is not None
        else D(sats) / D(SATS_PER_BTC) * D(btc_price)
    )
    token_usd = D(record.get("token_earned_usd") or 0)
    pnl = refund + sats_usd + token_usd - deployed
    return {
        "won": bool(record["is_won"]),
        "tiles": tiles_from_mask(int(record["selected_tiles"])),
        "deployed_usd": str(deployed),
        "refund_usd": str(refund),
        "sats": sats,
        "sats_usd": str(sats_usd.quantize(Decimal("0.000001"))),
        "token_usd": str(token_usd),
        "hashrate": int(record.get("hashrate_earned") or 0),
        "strike": bool((round_json or {}).get("is_sat_strike")),
        "pnl_usd": str(pnl.quantize(Decimal("0.000001"))),
        "simulated": False,
    }
