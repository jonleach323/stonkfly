"""Execution limits can veto a neural tile choice, never substitute one."""

import time

from .config import D
from .satrush.rules import mask_from_tiles


class Veto(Exception):
    pass


class Guard:
    def __init__(self, settings, ledger, stop_file):
        self.s = settings
        self.l = ledger
        self.stop_file = stop_file

    def check(self, board, equity, now=None):
        now = time.time() if now is None else now
        if self.stop_file.exists():
            raise Veto("STOP file present")
        if self.l.get("halted"):
            raise Veto(self.l.get("halted"))
        if self.l.pending():
            raise Veto("Deploy outcome unresolved")
        if not -0.5 <= now - board.fetched_at <= self.s.max_board_age:
            raise Veto("Stale board snapshot")
        if D(equity) <= D(self.l.get("initial_cash")) - D(self.s.loss_stop):
            self.l.halt("Loss stop reached")
            raise Veto("Loss stop reached")

    def playable(self, board):
        """Why the current round cannot take a deploy right now, or None."""
        if board.pending_activation:
            return "Round pending activation"
        if board.round_state not in (None, "active"):
            return f"Round state {board.round_state}"
        if board.slots_remaining < self.s.min_slots_remaining:
            return "Too few slots remaining"
        if self.l.deployment(board.round_id):
            return "Already deployed this round"
        return None

    def plan(self, board, tiles, equity, available, now=None):
        now = time.time() if now is None else now
        self.check(board, equity, now)
        reason = self.playable(board)
        if reason:
            raise Veto(reason)
        if self.l.deploys_today(now) >= self.s.daily_deploys:
            raise Veto("Daily deploy limit")
        tiles = sorted(set(int(t) for t in tiles))
        if not self.s.min_tiles <= len(tiles) <= self.s.max_tiles:
            raise Veto("Tile count outside configured bounds")
        stake = D(self.s.stake)
        if stake > D(available):
            raise Veto("Insufficient USDC for the configured stake")
        return {
            "round_id": board.round_id,
            "tiles": tiles,
            "mask": mask_from_tiles(tiles),
            "amount": int(stake * 10**6),
            "stake_usd": str(stake),
            "board_fetched_at": board.fetched_at,
            "slots_remaining": board.slots_remaining,
            "settings": self.s.signature(),
        }

    def before_submit(self, plan, fresh_board):
        if self.stop_file.exists() or self.l.get("halted"):
            raise Veto("Execution stopped")
        if fresh_board.round_id != plan["round_id"]:
            raise Veto("Round changed after neural observation")
        if fresh_board.slots_remaining < self.s.min_slots_remaining:
            raise Veto("Round closing before submission")
        if self.l.deployment(plan["round_id"]):
            raise Veto("Round already has a deploy intent")
