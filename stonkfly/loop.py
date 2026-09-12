"""One observation per round: see the board, pick tiles, deploy, wait, settle."""

import hashlib
import http.client
import json
import os
import time
import urllib.error
from pathlib import Path

from PIL import Image

from .config import D
from .display import board_frame
from .errors import Transient
from .guard import Veto
from .reinforcement import reinforcement

SETTLE_MARGIN = 3.0
# The outside world not answering is never a reason to halt: wait and try again.
TRANSIENT = (
    Transient,
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
    json.JSONDecodeError,
)
RETRY_BASE_S = 5.0
RETRY_MAX_S = 60.0


class GameLoop:
    def __init__(
        self, settings, ledger, api, player, controller, guard, out,
        clock=time.time, sleep=time.sleep, settle_margin=SETTLE_MARGIN, publisher=None,
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
        self.publisher = publisher
        self.failures = 0

    def stopped(self):
        return (self.out / "STOP").exists() or bool(self.l.get("halted"))

    def heartbeat(self, phase):
        self.l.put("status", {"phase": phase, "heartbeat": self.clock(), "running": True})

    def publish(self):
        if self.publisher is not None:
            try:
                self.publisher(self.out)
                self.l.put("publish_error", None)
            except Exception as e:  # Publication is observability, never control flow.
                self.l.put("publish_error", f"{type(e).__name__}: {str(e)[:120]}")

    def wait_for_next_round(self, board):
        self.heartbeat("waiting for the round to settle")
        remaining = board.seconds_remaining(self.clock())
        if board.pending_activation or remaining is None or remaining > 300:
            remaining = 5.0
        until = self.clock() + max(remaining, 0) + self.settle_margin
        while self.clock() < until and not (self.out / "STOP").exists():
            self.sleep(min(1.0, max(until - self.clock(), 0.05)))

    def step(self):
        """Returns the event row when an observation happened, else None.

        Transient failures (API, RPC, gateway pages) back off and retry without
        halting; anything else propagates and halts the run for review.
        """
        try:
            row = self._step()
        except TRANSIENT as e:
            self.failures += 1
            delay = min(RETRY_MAX_S, RETRY_BASE_S * 2 ** min(self.failures - 1, 4))
            self.l.put("status", {
                "phase": f"retrying after {type(e).__name__}",
                "heartbeat": self.clock(),
                "running": True,
                "failures": self.failures,
            })
            print(json.dumps({"retry": type(e).__name__, "attempt": self.failures, "in_seconds": delay}), flush=True)
            until = self.clock() + delay
            while self.clock() < until and not (self.out / "STOP").exists():
                self.sleep(min(1.0, max(until - self.clock(), 0.05)))
            return None
        self.failures = 0
        return row

    def _step(self):
        self.heartbeat("reading the board")
        self.player.reconcile()
        board = self.api.board()
        now = self.clock()
        outcomes = self.player.resolve(board)
        if outcomes:
            self.l.put("stimulus", reinforcement(outcomes))
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
        if reason == "Already deployed this round":
            self.wait_for_next_round(board)
            return None
        if reason:
            # Pending activation, closing round or a settling round: poll briefly.
            self.sleep(5.0)
            return None
        kind = self.l.get("stimulus") or "none"
        frame = board_frame(board, now)
        self.heartbeat("simulating neurons")
        neural = self.controller.observe(frame, kind)
        self.l.put("stimulus", "none")
        slot = self.l.get("tick") % 2
        checkpoint = self.out / f"brain-{slot}.npz"
        self.controller.save(checkpoint)
        info = {"file": checkpoint.name, "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        observation = {
            "neural": neural,
            "round_id": board.round_id,
            "stimulus": kind,
            "outcomes": outcomes,
            "readout_state": self.controller.readout.state(),
            "board": board.summary(),
        }
        self.l.commit_tick(equity, info, observation)
        execution = {"status": "VETO", "reason": "unset"}
        available = self.player.available(board)
        try:
            plan = self.guard.plan(board, neural["tiles"], equity, available, self.clock())
            # Neural integration takes wall time; re-read the board before sending.
            self.guard.before_submit(plan, self.api.board())
            self.heartbeat("deploying")
            execution = self.player.deploy(plan, self.clock())
        except Veto as e:
            execution = {"status": "VETO", "reason": str(e)}
        row = {
            "tick": self.l.get("tick"),
            "wall_time": now,
            "round_id": board.round_id,
            "mode": self.player.mode,
            "equity_usdc": str(equity),
            "available_usdc": str(available),
            "in_play_usdc": str(sum(D(r["plan"]["stake_usd"]) for r in self.l.unsettled())),
            "stimulus": kind,
            "outcomes": outcomes,
            "neural": neural,
            "execution": execution,
            "board": observation["board"],
        }
        with (self.out / "events.jsonl").open("a") as f:
            f.write(json.dumps(row, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        Image.fromarray(frame).save(self.out / "latest-input.png")
        (self.out / "latest.json").write_text(json.dumps(row, indent=2) + "\n")
        self.publish()
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
        try:
            while (not steps or count < steps) and not self.stopped():
                if self.step() is not None:
                    count += 1
        finally:
            self.l.put("status", {"phase": "stopped", "heartbeat": self.clock(), "running": False})
            self.publish()
        return count
