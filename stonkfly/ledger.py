"""Durable accounting of deploy intents and outcomes. Money values are Decimal strings."""

import contextlib
import json
import sqlite3
from pathlib import Path

from .config import D

OPEN = ("PREPARED", "SENT", "UNKNOWN")


class Ledger:
    def __init__(self, path, settings, mode):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS deployments (round_id INTEGER PRIMARY KEY,status TEXT NOT NULL,created REAL NOT NULL,plan TEXT NOT NULL,signature TEXT,outcome TEXT)"
        )
        if self.get("settings") is None:
            with self.transaction():
                for k, v in {
                    "settings": settings.signature(),
                    "mode": mode,
                    "cash": settings.capital,
                    "initial_cash": settings.capital,
                    "anchor": settings.capital,
                    "tick": 0,
                    "checkpoint": None,
                    "halted": None,
                    "last_attempt": 0,
                    "stimulus": "none",
                    "wallet": None,
                }.items():
                    self.put(k, v)
        elif self.get("settings") != settings.signature() or self.get("mode") != mode:
            raise RuntimeError("Run settings/mode mismatch; use a separate run directory")

    @staticmethod
    def never_played(path):
        """True when the ledger at path holds no round and no tick: nothing
        financial happened in it, so archiving it loses no state."""
        path = Path(path)
        if not path.is_file():
            return True
        db = sqlite3.connect(path)
        try:
            tick = db.execute("SELECT value FROM meta WHERE key='tick'").fetchone()
            rows = db.execute("SELECT count(*) FROM deployments").fetchone()[0]
        except sqlite3.OperationalError:
            return False
        finally:
            db.close()
        return rows == 0 and (tick is None or json.loads(tick[0]) == 0)

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            (key, json.dumps(value, allow_nan=False)),
        )

    @property
    def cash(self):
        return D(self.get("cash"))

    def halt(self, reason):
        self.put("halted", reason)

    def _row(self, r):
        return {
            "round_id": r[0],
            "status": r[1],
            "created": r[2],
            "plan": json.loads(r[3]),
            "signature": r[4],
            "outcome": json.loads(r[5]) if r[5] else None,
        }

    def deployment(self, round_id):
        r = self.db.execute(
            "SELECT round_id,status,created,plan,signature,outcome FROM deployments WHERE round_id=?",
            (round_id,),
        ).fetchone()
        return self._row(r) if r else None

    def with_status(self, statuses):
        marks = ",".join("?" * len(statuses))
        rows = self.db.execute(
            f"SELECT round_id,status,created,plan,signature,outcome FROM deployments WHERE status IN ({marks}) ORDER BY round_id",
            tuple(statuses),
        ).fetchall()
        return [self._row(r) for r in rows]

    def pending(self):
        return self.with_status(OPEN)

    def unsettled(self):
        return self.with_status(("CONFIRMED", "PAPER"))

    def reserve(self, plan, now, debit=False):
        with self.transaction():
            if self.pending():
                raise RuntimeError("An unresolved deploy exists")
            if self.deployment(plan["round_id"]):
                raise RuntimeError("Round already has a deploy intent")
            if debit:
                cash = self.cash - D(plan["stake_usd"])
                if cash < 0:
                    raise RuntimeError("Paper cash exhausted")
                self.put("cash", str(cash))
            self.db.execute(
                "INSERT INTO deployments(round_id,status,created,plan) VALUES (?,?,?,?)",
                (plan["round_id"], "PREPARED", now, json.dumps(plan)),
            )
            self.put("last_attempt", now)
        return plan

    def mark(self, round_id, status, signature=None):
        self.db.execute(
            "UPDATE deployments SET status=?,signature=COALESCE(?,signature) WHERE round_id=?",
            (status, signature, round_id),
        )

    def refund(self, round_id):
        """A deploy that provably never landed: return paper cash, keep the row."""
        with self.transaction():
            row = self.deployment(round_id)
            if not row or row["status"] not in OPEN:
                raise RuntimeError("Only open intents can be refunded")
            if self.get("mode") == "paper":
                self.put("cash", str(self.cash + D(row["plan"]["stake_usd"])))
            self.mark(round_id, "FAILED")

    def deploys_today(self, now):
        return self.db.execute(
            "SELECT COUNT(*) FROM deployments WHERE created>=? AND status!='FAILED'",
            (now - now % 86400,),
        ).fetchone()[0]

    def settle(self, round_id, outcome, credit=False):
        with self.transaction():
            row = self.deployment(round_id)
            if not row:
                raise RuntimeError("Unknown deployment")
            if row["status"] == "SETTLED":
                if row["outcome"] != outcome:
                    raise RuntimeError("Settlement changed after finalization")
                return
            if row["status"] not in ("CONFIRMED", "PAPER"):
                raise RuntimeError("Cannot settle an unconfirmed deploy")
            if credit:
                back = D(outcome["refund_usd"]) + D(outcome["sats_usd"])
                if back < 0:
                    raise ValueError("Negative settlement")
                self.put("cash", str(self.cash + back))
            self.db.execute(
                "UPDATE deployments SET status='SETTLED',outcome=? WHERE round_id=?",
                (json.dumps(outcome), round_id),
            )

    def commit_tick(self, anchor, checkpoint, observation=None):
        with self.transaction():
            self.put("anchor", str(anchor))
            self.put("checkpoint", checkpoint)
            self.put("tick", self.get("tick") + 1)
            if observation is not None:
                self.put("observation", observation)

    def close(self):
        self.db.close()
