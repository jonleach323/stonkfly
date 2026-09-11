from .config import D


def reinforcement(pnl, deadband):
    """Sign of the settled round P&L (USDC value of refund plus BTC won minus stake).
    This is an engineered stimulus to identified dopamine cells, not a statement
    that a fly understands money or the game.
    """
    delta = D(pnl)
    threshold = D(deadband)
    kind = (
        "reward"
        if delta >= threshold
        else "aversive"
        if delta <= -threshold
        else "none"
    )
    return kind, delta
