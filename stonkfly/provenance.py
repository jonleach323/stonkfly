"""What a run is allowed to resume with.

A run directory records the protocol it was started with: settings, dataset,
readout cells, circuit rule, mode and feed. A restart must match it exactly;
otherwise the ledger's rounds would mix incomparable experiments, so the
worker stops and asks for a fresh run directory.

The hashes of the source files are recorded as well, for traceability, but
they are not part of that check: an update to the page, the server or a bug
fix must not strand a run. Every source change is appended to the ledger's
``source_history`` with the files that changed, so the audit still shows which
code played which rounds.
"""

import hashlib
import json
import time
from pathlib import Path

SOURCE_KEY = "source_sha256"


def source_hashes(package_dir):
    """sha256 of every .py and .cpp file under the package, keyed by relative path."""
    root = Path(package_dir)
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.suffix in (".py", ".cpp")
    }


def _digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def protocol_signature(provenance):
    """Signature of everything in the provenance except the source hashes."""
    return _digest({k: v for k, v in provenance.items() if k != SOURCE_KEY})


def _legacy_protocol_signature(out, stored):
    """Runs started before source hashes were excluded signed the whole document.

    The saved provenance.json is that document; when its full signature is the
    stored one, return the signature of its protocol part so the run migrates.
    """
    path = Path(out) / "provenance.json"
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text())
    except ValueError:
        return None
    if not isinstance(saved, dict) or _digest(saved) != stored:
        return None
    return protocol_signature(saved)


def reconcile(ledger, out, provenance, clock=time.time):
    """Check the run may resume with this provenance; record it; note source changes.

    Returns the list of source files that changed since the last start (empty
    on a first start or an unchanged restart). Raises RuntimeError when the
    protocol differs from the run's.
    """
    signature = protocol_signature(provenance)
    stored = ledger.get("provenance_sha256")
    if stored not in (None, signature) and _legacy_protocol_signature(out, stored) != signature:
        raise RuntimeError("Run source/protocol changed; use a separate run directory or review migration")

    source = provenance.get(SOURCE_KEY) or {}
    history = ledger.get("source_history") or []
    previous = history[-1]["files"] if history else None
    changed = []
    if previous is None:
        # Legacy runs kept no history: their first source set is in provenance.json.
        path = Path(out) / "provenance.json"
        if path.exists():
            try:
                previous = (json.loads(path.read_text()) or {}).get(SOURCE_KEY)
            except ValueError:
                previous = None
        if isinstance(previous, dict) and previous != source:
            history.append({"sha256": _digest(previous), "at": None, "files": previous, "changed": []})
    if previous is not None and previous != source:
        changed = sorted(set(previous) ^ set(source) | {k for k in set(previous) & set(source) if previous[k] != source[k]})
    if not history or history[-1]["files"] != source:
        history.append({"sha256": _digest(source), "at": clock(), "files": source, "changed": changed})
        ledger.put("source_history", history)
    ledger.put("provenance_sha256", signature)
    return changed
