import hashlib
import json

import pytest

from stonkfly.provenance import protocol_signature, reconcile, source_hashes


class MemoryLedger:
    def __init__(self):
        self.meta = {}

    def get(self, key):
        return self.meta.get(key)

    def put(self, key, value):
        self.meta[key] = json.loads(json.dumps(value))


def provenance(source, stake="1"):
    return {"settings": {"stake": stake}, "readout": {"model": "v3"}, "mode": "paper", "source_sha256": source}


def test_first_start_records_protocol_and_source(tmp_path):
    ledger = MemoryLedger()
    assert reconcile(ledger, tmp_path, provenance({"a.py": "1"}), clock=lambda: 5.0) == []
    assert ledger.get("provenance_sha256") == protocol_signature(provenance({"anything": "else"}))
    assert ledger.get("source_history") == [{"sha256": hashlib.sha256(json.dumps({"a.py": "1"}, sort_keys=True).encode()).hexdigest(), "at": 5.0, "files": {"a.py": "1"}, "changed": []}]


def test_source_change_continues_and_is_logged(tmp_path):
    ledger = MemoryLedger()
    reconcile(ledger, tmp_path, provenance({"a.py": "1", "b.py": "1"}), clock=lambda: 1.0)
    assert reconcile(ledger, tmp_path, provenance({"a.py": "1", "b.py": "1"}), clock=lambda: 2.0) == []
    assert len(ledger.get("source_history")) == 1
    changed = reconcile(ledger, tmp_path, provenance({"a.py": "2", "c.py": "1"}), clock=lambda: 3.0)
    assert changed == ["a.py", "b.py", "c.py"]
    history = ledger.get("source_history")
    assert [h["at"] for h in history] == [1.0, 3.0] and history[-1]["changed"] == changed


def test_protocol_change_stops_the_run(tmp_path):
    ledger = MemoryLedger()
    reconcile(ledger, tmp_path, provenance({"a.py": "1"}))
    with pytest.raises(RuntimeError, match="protocol changed"):
        reconcile(ledger, tmp_path, provenance({"a.py": "1"}, stake="2"))
    with pytest.raises(RuntimeError, match="protocol changed"):
        reconcile(ledger, tmp_path, {**provenance({"a.py": "1"}), "mode": "live"})


def test_legacy_run_signed_with_source_hashes_migrates(tmp_path):
    """Runs started before this module signed the whole document, including source hashes."""
    ledger = MemoryLedger()
    old = provenance({"a.py": "1", "serve.py": "1"})
    ledger.put("provenance_sha256", hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest())
    (tmp_path / "provenance.json").write_text(json.dumps(old, indent=2))
    # Same protocol, updated source: resumes, and the old source set opens the history.
    new = provenance({"a.py": "1", "serve.py": "2"})
    assert reconcile(ledger, tmp_path, new, clock=lambda: 9.0) == ["serve.py"]
    assert ledger.get("provenance_sha256") == protocol_signature(new)
    history = ledger.get("source_history")
    assert [h["at"] for h in history] == [None, 9.0] and history[0]["files"] == old["source_sha256"]
    # A legacy run whose protocol differs still stops.
    ledger2 = MemoryLedger()
    ledger2.put("provenance_sha256", hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest())
    with pytest.raises(RuntimeError):
        reconcile(ledger2, tmp_path, provenance({"a.py": "1", "serve.py": "2"}, stake="3"))
    # A stored signature that matches neither is a foreign run.
    ledger3 = MemoryLedger()
    ledger3.put("provenance_sha256", "0" * 64)
    with pytest.raises(RuntimeError):
        reconcile(ledger3, tmp_path, new)


def test_source_hashes_cover_python_and_cpp(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "k.cpp").write_text("y")
    (tmp_path / "sub" / "k.so").write_bytes(b"z")
    assert list(source_hashes(tmp_path)) == ["a.py", "sub/k.cpp"]


def test_paper_run_archives_itself_on_a_protocol_change(tmp_path):
    """A stake change is a protocol change: the paper run is archived and restarted."""
    import subprocess
    import sqlite3
    import sys

    out = tmp_path / "paper"
    base = [sys.executable, "-m", "stonkfly", "run", "--fixture", "--stub-brain", "--steps", "1", "--out", str(out)]
    subprocess.run(base, check=True, capture_output=True, text=True)
    second = subprocess.run(base + ["--stake", "2"], check=True, capture_output=True, text=True)
    assert '"protocol_changed": true' in second.stdout
    archives = list(tmp_path.glob("paper-archive-*"))
    assert len(archives) == 1 and (archives[0] / "ledger.sqlite").exists()
    db = sqlite3.connect(out / "ledger.sqlite")
    assert json.loads(db.execute("select value from meta where key='tick'").fetchone()[0]) == 1
