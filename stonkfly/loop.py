"""One observation per round: see the board, pick tiles, deploy, wait, settle."""

import hashlib
import json
import os
import time
from pathlib import Path

from PIL import Image

from .display import board_frame
from .guard import Veto
from .reinforcement import reinforcement

SETTLE_MARGIN = 3.0


class GameLoop:
    def __init__(
        self, settings, ledger, api, player, controller, guard, out,
        clock=time.time, sleep=time.sleep, settle_margin=SETTLE_MARGIN,
    ):
        self.s = settings
        self.l = ledger
        self.api = api
        self.player = player
        self.controller = controller
        self.guard = guard
        self.out = Path(out)
        self.clock = clock
        self.sleep = sleep
        self.settle_margin = settle_margin

    def stopped(self):
        return (self.out / "STOP").exists() or bool(self.l.get("halted"))

    def wait_for_next_round(self, board):
        remaining = board.seconds_remaining(self.clock())
        until = self.clock() + max(min(remaining or 0, 120), 0) + self.settle_margin
        while self.clock() < until and not (self.out / "STOP").exists():
            self.sleep(min(1.0, max(until - self.clock(), 0.05)))

    def step(self):
        """Returns the event row when an observation happened, else None."""
        self.player.reconcile()
        board = self.api.board()
        now = self.clock()
        outcomes = self.player.resolve(board)
        if outcomes:
            pnl = sum(float(o["pnl_usd"]) for o in outcomes)
            kind, _ = reinforcement(str(round(pnl, 6)), self.s.reward_deadband)
            self.l.put("stimulus", kind)
            self.l.put("last_outcomes", outcomes)
        equity = self.player.equity(board)
        try:
            self.guard.check(board, equity, now)
        except Veto as e:
            if "Loss stop" in str(e) or "STOP" in str(e) or self.l.get("halted"):
                return None
            self.wait_for_next_round(board)
            return None
        reason = self.guard.playable(board)
        if reason:
            self.wait_for_next_round(board)
            return None
        kind = self.l.get("stimulus") or "none"
        frame = board_frame(board, now)
        neural = self.controller.observe(frame, kind)
        self.l.put("stimulus", "none")
        slot = self.l.get("tick") % 2
        checkpoint = self.out / f"brain-{slot}.npz"
        self.controller.save(checkpoint)
        info = {"file": checkpoint.name, "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        observation = {"neural": neural, "round_id": board.round_id, "stimulus": kind, "outcomes": outcomes}
        self.l.commit_tick(equity, info, observation)
        execution = {"status": "VETO", "reason": "unset"}
        try:
            plan = self.guard.plan(board, neural["tiles"], equity, self.player.available(board), self.clock())
            # Neural integration takes wall time; re-read the board before sending.
            self.guard.before_submit(plan, self.api.board())
            execution = self.player.deploy(plan, self.clock())
        except Veto as e:
            execution = {"status": "VETO", "reason": str(e)}
        row = {
            "tick": self.l.get("tick"),
            "wall_time": now,
            "round_id": board.round_id,
            "mode": self.player.mode,
            "equity_usdc": str(equity),
            "stimulus": kind,
            "outcomes": outcomes,
            "neural": neural,
            "execution": execution,
        }
        with (self.out / "events.jsonl").open("a") as f:
            f.write(json.dumps(row, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        Image.fromarray(frame).save(self.out / "latest-input.png")
        (self.out / "latest.json").write_text(json.dumps(row, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "tick": row["tick"],
                    "round": board.round_id,
                    "tiles": neural["tiles"],
                    "execution": execution["status"],
                    "equity": str(equity),
                    "stimulus": kind,
                    "plastic_edges_changed": neural["memory"]["changed_edges"],
                }
            ),
            flush=True,
        )
        self.wait_for_next_round(board)
        return row

    def run(self, steps=0):
        count = 0
        while (not steps or count < steps) and not self.stopped():
            if self.step() is not None:
                count += 1
        return count
