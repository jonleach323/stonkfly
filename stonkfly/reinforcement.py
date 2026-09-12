"""The reinforcement signal: did the fly hit the winning tile?

A hit (the winning tile was among the picks) schedules the reward pulse into
the PAM11 dopamine cells; a miss schedules the aversive pulse into the PPL101
cells. Money is deliberately not the signal: fees make even a hit a small
loss in USDC, so a P&L rule punished every round. This is an engineered
stimulus to identified dopamine cells, not a statement that a fly understands
the game.
"""


def reinforcement(outcomes):
    """'reward' if any settled round was a hit, 'aversive' if rounds settled and none hit, else 'none'."""
    settled = [o for o in outcomes or [] if o.get("won") is not None]
    if not settled:
        return "none"
    return "reward" if any(o["won"] for o in settled) else "aversive"
