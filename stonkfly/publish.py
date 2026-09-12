"""Build read-only public snapshots of a run directory for the watch site.

Everything here derives from the ledger, the event log and the latest frame.
Nothing feeds back into the brain or the guard. Wallet keys never appear;
the public wallet address does, because every deploy is public on chain.
"""

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

from .config import D, TILES

SNAPSHOT_VERSION = 1
MODEL = {
    "connectome": "MaleCNS v1.0",
    "neurons": 166700,
    "retained_edges": 25582938,
    "readout": "dn-21-group-relative-median-v3",  # overridden by the run's provenance (readout.model)
    "learning_validated": False,
}
PROGRAM = "satRushGBRY2vgapeTAkoxz26vL2cYqyPi6CnBj7Tco"
# Deploy intents in audit.json, newest first; the ledger keeps every one. A month-long run makes tens of
# thousands of rows, and the whole document is re-uploaded whenever a row changes.
AUDIT_LIMIT = 1000
# Statuses whose signature is public: the transaction was sent, whether or not its outcome is known yet.
SIGNED = ("CONFIRMED", "SETTLED", "SENT", "UNKNOWN")
# Vercel Blob's minimum edge cache lifetime for an overwritten blob (its docs: "cannot be set lower than 1 minute").
BLOB_MAX_AGE = 60
FRAME_MAX_AGE = 31536000  # content-addressed frames never change


_COPIES = {}  # ledger path -> (stamp, copy path) for read-only mounts, see _open_readonly


def _stamp(path):
    files = []
    for suffix in ("", "-wal"):
        try:
            st = os.stat(f"{path}{suffix}")
            files.append((suffix, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            pass
    return tuple(files)


def _open_readonly(path):
    """Open the ledger without ever writing to the run directory.

    The ledger is in WAL mode, and a read-only connection to a WAL database
    needs the -shm file, which SQLite creates on demand. On a read-only
    filesystem (the watch container mounts the run directory `:ro`) that fails
    with "unable to open database file" whenever the file is missing: the
    worker is stopped, halted, or reloading the graph on start, or its last
    close could not checkpoint. Two exact fallbacks, both leaving the run
    directory untouched:

    - no -wal file: nothing is un-checkpointed, so `immutable=1` reads the
      committed data as is;
    - a -wal file: copy the database and its WAL to a private temporary
      directory (cached by size and mtime) and open the copy, which lets SQLite
      recover the WAL there. WAL frames carry checksums, so a copy taken while
      the worker writes is read up to the last complete frame.
    """
    path = Path(path)
    db = None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.execute("PRAGMA schema_version").fetchone()  # opening is lazy: this is where the -shm is needed
        return db
    except sqlite3.OperationalError:
        if db is not None:
            db.close()
    wal = Path(f"{path}-wal")
    if not wal.exists():
        return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    stamp = _stamp(path)
    cached = _COPIES.get(str(path))
    if cached is None or cached[0] != stamp:
        folder = Path(tempfile.mkdtemp(prefix="stonkfly-ledger-"))
        copy = folder / path.name
        shutil.copyfile(path, copy)
        shutil.copyfile(wal, f"{copy}-wal")
        if cached is not None:
            shutil.rmtree(cached[1].parent, ignore_errors=True)
        _COPIES[str(path)] = (stamp, copy)
        cached = _COPIES[str(path)]
    return sqlite3.connect(f"file:{cached[1]}?mode=ro", uri=True)


def _read_meta(path):
    db = _open_readonly(path)
    try:
        meta = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM meta")}
        rows = db.execute(
            "SELECT round_id,status,created,plan,signature,outcome FROM deployments ORDER BY round_id DESC"
        ).fetchall()
    finally:
        db.close()
    deployments = [
        {
            "round_id": r[0],
            "status": r[1],
            "created": r[2],
            "plan": json.loads(r[3]),
            "signature": r[4],
            "outcome": json.loads(r[5]) if r[5] else None,
        }
        for r in rows
    ]
    return meta, deployments


TAIL_BYTES = 4 * 1024 * 1024


def _read_events(path, limit=4000):
    """The last `limit` events, reading only the file's tail: the log grows for months."""
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - TAIL_BYTES)
        handle.seek(start)
        chunk = handle.read()
    text = chunk.decode("utf-8", errors="replace")
    if start > 0:
        text = text.split("\n", 1)[1] if "\n" in text else ""
    lines = text.splitlines()[-limit:]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _money(value):
    return str(D(value).quantize(Decimal("0.000001")))


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _provenance(out):
    path = Path(out) / "provenance.json"
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _network(provenance):
    return (provenance.get("settings") or {}).get("network", "mainnet")


def _model(provenance):
    """MODEL with the readout the run actually declared; the constant is only a fallback."""
    readout = (provenance.get("readout") or {}).get("model") or MODEL["readout"]
    return {**MODEL, "readout": readout}


def _explorer(kind, ident, network):
    return f"https://solscan.io/{kind}/{ident}" + ("?cluster=devnet" if network == "devnet" else "")


def _round_row(row):
    plan, outcome = row["plan"], row["outcome"] or {}
    stake = D(plan["stake_usd"])
    fee = stake - D(outcome["stake_usd"]) if outcome.get("stake_usd") else None
    return {
        "round_id": row["round_id"],
        "time": row["created"],
        "status": row["status"],
        "tiles": plan["tiles"],
        "tile_count": len(plan["tiles"]),
        "stake": _money(stake),
        "signature": row["signature"] if row["status"] in SIGNED else None,
        "won": outcome.get("won"),
        "winning_tile": outcome.get("winning_tile"),
        "refund": _money(outcome["refund_usd"]) if outcome.get("refund_usd") is not None else None,
        "sats": outcome.get("sats"),
        "sats_usd": _money(outcome["sats_usd"]) if outcome.get("sats_usd") is not None else None,
        "token_usd": _money(outcome["token_usd"]) if outcome.get("token_usd") is not None else None,
        "fee": _money(fee) if fee is not None else None,
        "pnl": _money(outcome["pnl_usd"]) if outcome.get("pnl_usd") is not None else None,
        "simulated": outcome.get("simulated"),
    }


def snapshot(out, now=None):
    """The public state document. `out` is a run directory."""
    out = Path(out)
    now = time.time() if now is None else now
    ledger = out / "ledger.sqlite"
    if not ledger.exists():
        return {"ready": False, "version": SNAPSHOT_VERSION, "published_at": now}
    meta, deployments = _read_meta(ledger)
    events = _read_events(out / "events.jsonl")
    provenance = _provenance(out)
    settings = provenance.get("settings") or {}
    last = events[-1] if events else None
    status = meta.get("status") or {}
    mode = meta.get("mode", "paper")
    network = _network(provenance)
    feed = provenance.get("feed", "satrush-public-" + network)
    settled = [d for d in deployments if d["status"] == "SETTLED" and d["outcome"]]
    open_rows = [d for d in deployments if d["status"] in ("PAPER", "CONFIRMED", "SENT", "PREPARED", "UNKNOWN")]
    won = [d for d in settled if d["outcome"].get("won")]
    initial = D(meta.get("initial_cash") or "0")
    in_play = sum((D(d["plan"]["stake_usd"]) for d in open_rows), D(0))
    if mode == "paper":
        cash = D(meta.get("cash") or "0")
        equity = cash + in_play
    elif last:
        # Live: the worker samples the wallet before it deploys, so the latest observation still holds the
        # stake it then sent for that round. `available_usdc` (wallet + unclaimed USDC) is the cash figure;
        # older workers only report `equity_usdc`, which also counts unclaimed sats at the BTC price.
        just_sent = sum((D(d["plan"]["stake_usd"]) for d in open_rows if d["round_id"] == last.get("round_id")), D(0))
        equity = D(last["equity_usdc"])
        cash = max(D(last.get("available_usdc") or last["equity_usdc"]) - just_sent, D(0))
    else:
        cash = equity = initial
    pnl = equity - initial
    fees = sum((D(d["plan"]["stake_usd"]) - D(d["outcome"]["stake_usd"]) for d in settled if d["outcome"].get("stake_usd")), D(0))
    sats_total = sum(int(d["outcome"].get("sats") or 0) for d in settled)
    sats_usd_total = sum((D(d["outcome"].get("sats_usd") or 0) for d in settled), D(0))
    refunds_total = sum((D(d["outcome"].get("refund_usd") or 0) for d in settled), D(0))
    deployed_total = sum((D(d["plan"]["stake_usd"]) for d in settled), D(0))
    best = max((D(d["outcome"]["pnl_usd"]) for d in settled if d["outcome"].get("pnl_usd") is not None), default=None)
    day_start = now - now % 86400
    deploys_today = sum(1 for d in deployments if d["created"] >= day_start and d["status"] != "FAILED")
    history = [{"time": e["wall_time"], "equity": e["equity_usdc"]} for e in events if "equity_usdc" in e]
    if len(history) > 600:
        step = len(history) / 600
        history = [history[int(i * step)] for i in range(600)] + history[-1:]
    decisions = []
    for e in reversed(events[-60:]):
        n = e.get("neural") or {}
        ex = e.get("execution") or {}
        decisions.append(
            {
                "tick": e.get("tick"),
                "time": e.get("wall_time"),
                "round_id": e.get("round_id"),
                "tiles": n.get("tiles") or [],
                "tile_count": len(n.get("tiles") or []),
                "status": ex.get("status"),
                "reason": ex.get("reason"),
                "stimulus": e.get("stimulus"),
                "kc_spikes": n.get("KC_spikes"),
                "changed_edges": (n.get("memory") or {}).get("changed_edges"),
                "median_excess_hz": n.get("median_excess_hz"),
            }
        )
    neural = {}
    if last:
        n = last.get("neural") or {}
        neural = {
            "tiles": n.get("tiles") or [],
            "group_hz": n.get("group_hz") or [],
            "excess_hz": n.get("excess_hz") or [],
            "median_excess_hz": n.get("median_excess_hz"),
            "excess_rel": n.get("excess_rel") or [],
            "median_excess_rel": n.get("median_excess_rel"),
            "brain_ms": n.get("brain_ms"),
            "total_spikes": n.get("total_spikes"),
            "reward_spikes": n.get("reward_spikes"),
            "aversive_spikes": n.get("aversive_spikes"),
            "KC_spikes": n.get("KC_spikes"),
            "stimulus": n.get("stimulus"),
            "stimulus_ms": n.get("stimulus_ms"),
            "changed_edges": (n.get("memory") or {}).get("changed_edges"),
            "stub": bool(n.get("stub")),
            "compute_seconds": n.get("compute_seconds"),
        }
    frame = out / "latest-input.png"
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest() if frame.exists() else None
    heartbeat = _num(status.get("heartbeat"), 0.0)
    running = bool(status.get("running")) and now - heartbeat < 180 and not meta.get("halted")
    return {
        "ready": True,
        "version": SNAPSHOT_VERSION,
        "game": "satrush",
        "mode": mode,
        "network": network,
        "status": {
            "running": running,
            "phase": status.get("phase") or ("stopped" if not running else "running"),
            "heartbeat": heartbeat or None,
            "feed": feed,
            "halted": meta.get("halted"),
            "stub_brain": bool(neural.get("stub")),
        },
        "tick": meta.get("tick", 0),
        "observed_at": last["wall_time"] if last else None,
        "published_at": now,
        "wallet": {
            "address": meta.get("wallet"),
            "explorer": _explorer("account", meta["wallet"], network) if meta.get("wallet") else None,
        },
        "board": (last or {}).get("board"),
        "portfolio": {
            "equity": _money(equity),
            "initial": _money(initial),
            "cash": _money(cash),
            "in_play": _money(in_play),
            "pnl": _money(pnl),
            "pnl_percent": _money(pnl / initial * 100) if initial else "0",
            "fees": _money(fees),
            "refunds": _money(refunds_total),
            "deployed": _money(deployed_total),
            "sats_won": sats_total,
            "sats_won_usd": _money(sats_usd_total),
            "rounds_played": len(settled),
            "rounds_won": len(won),
            "hit_rate_percent": _money(D(len(won)) / D(len(settled)) * 100) if settled else None,
            "best_round_pnl": _money(best) if best is not None else None,
            "open_rounds": [d["round_id"] for d in open_rows],
        },
        "rounds": [_round_row(d) for d in deployments[:30]],
        "round_count": len(deployments),
        "decisions": decisions,
        "neural": neural,
        "settings": {
            "stake": settings.get("stake"),
            "daily_deploys": settings.get("daily_deploys"),
            "loss_stop": settings.get("loss_stop"),
            "min_slots_remaining": settings.get("min_slots_remaining"),
            "neural_ms": settings.get("neural_ms"),
            "learning": settings.get("learning"),
            "min_tiles": settings.get("min_tiles"),
            "max_tiles": settings.get("max_tiles"),
            "capital": settings.get("capital"),
        },
        "costs": {"deploys_today": deploys_today, "fee_bps": ((last or {}).get("outcomes") or [{}])[-1].get("fee_bps") if last else None},
        "history": history,
        "last_outcomes": meta.get("last_outcomes") or [],
        "readout": provenance.get("readout"),
        "model": _model(provenance),
        "policy": "Fixed 21-group descending-neuron readout picks tiles; stake, limits and timing are engineered settings.",
        "animation": "Decorative 3D avatar; not a neural or muscle reconstruction",
        "learning_validated": False,
        "publication": {
            "frame_sha256": frame_sha,
            "provenance_sha256": meta.get("provenance_sha256"),
            "publish_error": meta.get("publish_error"),
        },
    }


def audit(out):
    out = Path(out)
    ledger = out / "ledger.sqlite"
    if not ledger.exists():
        return {"ready": False}
    meta, deployments = _read_meta(ledger)
    provenance = _provenance(out)
    network = _network(provenance)
    events = {e.get("round_id"): e for e in _read_events(out / "events.jsonl")}
    rows = []
    for d in deployments[:AUDIT_LIMIT]:
        e = events.get(d["round_id"]) or {}
        n = e.get("neural") or {}
        rows.append(
            {
                "round_id": d["round_id"],
                "status": d["status"],
                "created": d["created"],
                "tiles": d["plan"]["tiles"],
                "selection_mask": d["plan"]["mask"],
                "amount_micro_usdc": d["plan"]["amount"],
                "signature": d["signature"],
                "explorer": _explorer("tx", d["signature"], network) if d["signature"] and d["status"] in SIGNED else None,
                "outcome": d["outcome"],
                "tick": e.get("tick"),
                "input_sha256": n.get("input_sha256"),
                "spike_sha256": n.get("spike_sha256"),
                "brain_ms": n.get("brain_ms"),
            }
        )
    return {
        "ready": True,
        "mode": meta.get("mode"),
        "wallet": meta.get("wallet"),
        "program": PROGRAM,
        "provenance_sha256": meta.get("provenance_sha256"),
        "network": network,
        "model": _model(provenance),
        "execution": "Paper: hypothetical deploys settled from real public round results"
        if meta.get("mode") == "paper"
        else "Live: DeployPublic transactions signed by the dedicated wallet",
        "deployments": rows,
        "deployment_count": len(deployments),
        "truncated": len(deployments) > len(rows),
        "note": "This audit supports traceability, not proof of skill or independent verification by SatRush."
        + (f" It lists the {AUDIT_LIMIT} most recent deploy intents; the worker's ledger holds all {len(deployments)}." if len(deployments) > len(rows) else ""),
    }


def write_files(out):
    """Write state.json, audit.json and sensory.png under <out>/site for uploads or static hosting."""
    out = Path(out)
    target = out / "site"
    target.mkdir(parents=True, exist_ok=True)
    state = snapshot(out)
    audit_doc = audit(out)
    audit_bytes = json.dumps(audit_doc, allow_nan=False).encode()
    state["publication"]["audit_sha256"] = hashlib.sha256(audit_bytes).hexdigest()
    frame = out / "latest-input.png"
    frame_bytes = frame.read_bytes() if frame.exists() else None
    if frame_bytes is not None:
        # Hash the bytes that ship, so the SHA the page shows is the SHA of the frame it fetches.
        state["publication"]["frame_sha256"] = hashlib.sha256(frame_bytes).hexdigest()
    files = {
        "state.json": (json.dumps(state, allow_nan=False).encode(), "application/json"),
        "audit.json": (audit_bytes, "application/json"),
    }
    if frame_bytes is not None:
        files["sensory.png"] = (frame_bytes, "image/png")
    for name, (data, _) in files.items():
        tmp = target / (name + ".partial")
        tmp.write_bytes(data)
        tmp.replace(target / name)
    return files


class BlobError(RuntimeError):
    """The Blob API refused a request; `code` is the HTTP status and the message carries its JSON body."""

    def __init__(self, code, body):
        super().__init__(f"Blob API {code}: {body}")
        self.code = code


class BlobPublisher:
    """Uploads the snapshot files to Vercel Blob (public store).

    Mirrors @vercel/blob's put(): PUT https://vercel.com/api/blob/?pathname=...
    with the read-write token. The store id is the third token segment.

    `state.json` and `audit.json` keep fixed pathnames and are overwritten in
    place (the edge caches them for BLOB_MAX_AGE, Vercel's minimum). The frame
    goes to `frames/<sha256>.png`, immutable and content-addressed, so the
    frame the site serves is always the one `state.publication.frame_sha256`
    names. Unchanged files are not re-uploaded: every put() is a billable
    "advanced operation".
    """

    API = "https://vercel.com/api/blob/"
    VERSION = "12"
    ATTEMPTS = 3
    RETRYABLE = (429, 500, 502, 503, 504)

    def __init__(self, token, prefix="stonkfly", request=None, sleep=time.sleep, announce=None):
        if not token or not token.startswith("vercel_blob_rw_"):
            raise ValueError("BLOB_READ_WRITE_TOKEN must be a vercel_blob_rw_ token")
        parts = token.split("_")
        if len(parts) < 5:
            raise ValueError("Malformed blob token")
        self.token = token
        self.store_id = parts[3]
        self.prefix = prefix.strip("/")
        self.request = request or self._http
        self.sleep = sleep
        self.announce = (lambda line: print(line, flush=True)) if announce is None else announce
        self.urls = {}
        self.uploaded = {}  # logical name -> sha256 of the bytes last uploaded
        self.announced = False

    @property
    def base_url(self):
        """What SNAPSHOT_BASE_URL should be set to (how @vercel/blob builds public URLs)."""
        return f"https://{self.store_id}.public.blob.vercel-storage.com/{self.prefix}"

    @staticmethod
    def _http(url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            body = e.read(300).decode("utf-8", "replace")
            raise BlobError(e.code, body) from e

    def put(self, name, data, content_type, max_age=BLOB_MAX_AGE):
        pathname = f"{self.prefix}/{name}"
        url = self.API + "?" + urllib.parse.urlencode({"pathname": pathname})
        headers = {
            "authorization": f"Bearer {self.token}",
            "x-api-version": self.VERSION,
            "x-vercel-blob-store-id": self.store_id,
            "x-api-blob-request-id": f"{self.store_id}:{int(time.time() * 1000)}:{secrets.token_hex(4)}",
            "x-vercel-blob-access": "public",
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-cache-control-max-age": str(max_age),
            "content-type": content_type,
        }
        for attempt in range(self.ATTEMPTS):
            headers["x-api-blob-request-attempt"] = str(attempt)
            try:
                reply = self.request(url, data, headers)
                break
            except (BlobError, OSError) as e:  # OSError covers URLError and timeouts
                retryable = e.code in self.RETRYABLE if isinstance(e, BlobError) else True
                if not retryable or attempt == self.ATTEMPTS - 1:
                    raise
                self.sleep(0.5 * 2**attempt)
        self.urls[name] = reply.get("url")
        return reply

    def __call__(self, out):
        files = write_files(out)
        try:
            # Dependencies first: state.json names the frame and the audit hash, so both must exist before it lands.
            for name in ("sensory.png", "audit.json", "state.json"):
                if name not in files:
                    continue
                data, content_type = files[name]
                sha = hashlib.sha256(data).hexdigest()
                if self.uploaded.get(name) == sha:
                    continue
                if name == "sensory.png":
                    self.put(f"frames/{sha}.png", data, content_type, max_age=FRAME_MAX_AGE)
                    self.urls[name] = self.urls.pop(f"frames/{sha}.png")
                else:
                    self.put(name, data, content_type)
                self.uploaded[name] = sha
        except Exception as e:
            # The loop keeps only the exception's class name; leave the detail (status, API body) in the log.
            self.announce(json.dumps({"publish_error": f"{type(e).__name__}: {e}"[:300]}))
            raise
        if not self.announced and self.urls.get("state.json"):
            self.announced = True
            self.announce(json.dumps({"publish": {"base_url": self.urls["state.json"].rsplit("/", 1)[0], "env": "SNAPSHOT_BASE_URL"}}))
        return dict(self.urls)


def publisher_from_env(env=os.environ):
    token = env.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        return write_files
    return BlobPublisher(token, env.get("STONKFLY_PUBLISH_PREFIX", "stonkfly"))
