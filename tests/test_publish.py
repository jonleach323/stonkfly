"""Snapshots and the local watch server; no network, no wallet."""

import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stonkfly.config import Settings
from stonkfly.guard import Guard
from stonkfly.ledger import Ledger
from stonkfly.loop import GameLoop
from stonkfly.neural.controller import StubController
from stonkfly.publish import AUDIT_LIMIT, BlobError, BlobPublisher, audit, snapshot, write_files
from stonkfly.satrush.api import FixtureApi
from stonkfly.satrush.player import PaperPlayer
from stonkfly.serve import make_server


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def run_dir(tmp_path):
    clock = Clock()
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "paper")
    api = FixtureApi(period=60.0, seed=1, clock=clock)
    player = PaperPlayer(settings, ledger, api)
    (tmp_path / "provenance.json").write_text(json.dumps({"settings": {"stake": "1", "network": "mainnet"}, "feed": "fixture", "readout": {"model": "stub"}}))
    loop = GameLoop(
        settings, ledger, api, player, StubController(settings), Guard(settings, ledger, tmp_path / "STOP"),
        tmp_path, clock=clock, sleep=clock.sleep, settle_margin=1.0, publisher=write_files,
    )
    loop.run(steps=3)
    ledger.close()
    return tmp_path, clock


def _plan(round_id, stake="1"):
    return {"round_id": round_id, "tiles": [1, 2], "mask": 3, "amount": 1_000_000, "stake_usd": stake}


@pytest.fixture
def live_dir(tmp_path):
    """A live-mode run directory as the worker leaves it: one settled deploy, one sent after the last observation."""
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "live")
    ledger.put("wallet", "FLYwa11et")
    ledger.reserve(_plan(7001), 100.0)
    ledger.mark(7001, "CONFIRMED", "sig7001")
    ledger.settle(7001, {"won": False, "winning_tile": 9, "stake_usd": "0.94", "refund_usd": "0.8", "sats": 0, "sats_usd": "0", "pnl_usd": "-0.2"})
    ledger.reserve(_plan(7002), 200.0)
    ledger.mark(7002, "SENT", "sig7002")
    ledger.close()
    (tmp_path / "provenance.json").write_text(json.dumps({"settings": {"network": "devnet"}, "readout": {"model": "dn-test-v9"}}))
    return tmp_path


def _event(tmp_path, **fields):
    row = {"tick": 2, "wall_time": 199.0, "round_id": 7002, "mode": "live", "equity_usdc": "80.500000", **fields}
    (tmp_path / "events.jsonl").write_text(json.dumps(row) + "\n")


def test_snapshot_reflects_ledger_and_events(run_dir):
    out, clock = run_dir
    s = snapshot(out, now=clock.now)
    assert s["ready"] and s["mode"] == "paper" and s["game"] == "satrush"
    assert s["tick"] == 3 and s["round_count"] == 3
    assert s["portfolio"]["rounds_played"] == 2 and s["portfolio"]["open_rounds"] == [1002]
    assert s["portfolio"]["in_play"] == "1.000000"
    # Total value counts the open stake; P&L is the settled results only.
    equity = float(s["portfolio"]["equity"])
    assert abs(equity - (float(s["portfolio"]["cash"]) + 1.0)) < 1e-6
    assert s["rounds"][0]["status"] == "PAPER" and s["rounds"][1]["status"] == "SETTLED"
    assert s["rounds"][1]["pnl"] is not None and s["rounds"][1]["fee"] == "0.060000"
    assert len(s["decisions"]) == 3 and s["decisions"][0]["tick"] == 3
    assert s["neural"]["tiles"] and s["neural"]["stub"] is True
    assert s["board"]["round_id"] == 1002 and len(s["board"]["tile_stakes"]) == 21
    assert s["status"]["phase"] == "stopped" and s["status"]["running"] is False
    assert s["publication"]["frame_sha256"]
    assert len(s["history"]) == 3
    assert "wallet" in s and s["wallet"]["address"] is None
    # The model block names the readout the run declared, not a constant.
    assert s["model"]["readout"] == "stub" and s["readout"]["model"] == "stub"
    json.dumps(s, allow_nan=False)


def test_live_snapshot_separates_cash_from_equity(live_dir):
    # The worker samples the wallet before it sends: the stake for the observed round is still in the wallet.
    _event(live_dir, available_usdc="79.000000")
    p = snapshot(live_dir, now=300.0)["portfolio"]
    assert p["equity"] == "80.500000" and p["in_play"] == "1.000000"
    assert p["cash"] == "78.000000"  # available minus the stake sent after the sample; sats value excluded
    assert p["pnl"] == "-19.500000" and p["open_rounds"] == [7002]
    # Older workers report only equity: the stake just sent is still taken out of the cash figure.
    _event(live_dir)
    p = snapshot(live_dir, now=300.0)["portfolio"]
    assert p["equity"] == "80.500000" and p["cash"] == "79.500000"
    # Never negative, even when the wallet reading is behind.
    _event(live_dir, available_usdc="0.5")
    assert snapshot(live_dir, now=300.0)["portfolio"]["cash"] == "0.000000"


def test_explorer_links_follow_network_and_signed_statuses(live_dir):
    _event(live_dir)
    s = snapshot(live_dir, now=300.0)
    assert s["network"] == "devnet"
    assert s["wallet"]["explorer"] == "https://solscan.io/account/FLYwa11et?cluster=devnet"
    assert s["model"]["readout"] == "dn-test-v9"
    a = audit(live_dir)
    assert a["network"] == "devnet" and a["model"]["readout"] == "dn-test-v9"
    by_round = {d["round_id"]: d for d in a["deployments"]}
    assert by_round[7002]["status"] == "SENT" and by_round[7002]["explorer"] == "https://solscan.io/tx/sig7002?cluster=devnet"
    assert by_round[7001]["explorer"] == "https://solscan.io/tx/sig7001?cluster=devnet"
    assert a["deployment_count"] == 2 and a["truncated"] is False
    assert [r["signature"] for r in s["rounds"]] == ["sig7002", "sig7001"]


def test_audit_caps_deployments(tmp_path, monkeypatch):
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "paper")
    for rid in range(1, 6):
        ledger.reserve(_plan(rid), float(rid))
        ledger.mark(rid, "PAPER")
    ledger.close()
    monkeypatch.setattr("stonkfly.publish.AUDIT_LIMIT", 3)
    a = audit(tmp_path)
    assert [d["round_id"] for d in a["deployments"]] == [5, 4, 3]
    assert a["deployment_count"] == 5 and a["truncated"] is True and "3 most recent" in a["note"]
    assert AUDIT_LIMIT >= 500


def test_audit_and_files(run_dir):
    out, clock = run_dir
    a = audit(out)
    assert a["ready"] and len(a["deployments"]) == 3 and a["program"].startswith("satRush")
    assert a["deployments"][0]["input_sha256"]
    files = write_files(out)
    assert set(files) == {"state.json", "audit.json", "sensory.png"}
    state = json.loads((out / "site" / "state.json").read_text())
    assert state["publication"]["audit_sha256"] == hashlib.sha256(files["audit.json"][0]).hexdigest()
    assert state["publication"]["frame_sha256"] == hashlib.sha256(files["sensory.png"][0]).hexdigest()
    assert (out / "site" / "sensory.png").read_bytes()[:4] == b"\x89PNG"


def test_snapshot_without_ledger(tmp_path):
    assert snapshot(tmp_path)["ready"] is False
    assert audit(tmp_path)["ready"] is False


def test_blob_publisher_requests(run_dir):
    out, clock = run_dir
    calls, lines = [], []

    def fake(url, data, headers):
        calls.append((url, len(data), headers))
        return {"url": "https://store.public.blob.vercel-storage.com/" + urllib.parse.unquote(url.split("pathname=")[1])}

    with pytest.raises(ValueError):
        BlobPublisher("nope")
    p = BlobPublisher("vercel_blob_rw_STORE123_secretpart", prefix="/fly/", request=fake, announce=lines.append)
    assert p.base_url == "https://STORE123.public.blob.vercel-storage.com/fly"
    urls = p(out)
    assert set(urls) == {"state.json", "audit.json", "sensory.png"}
    frame_sha = json.loads((out / "site" / "state.json").read_text())["publication"]["frame_sha256"]
    # Dependencies land before the document that names them; the frame is content-addressed and immutable.
    assert [c[0].split("pathname=")[1] for c in calls] == [f"fly%2Fframes%2F{frame_sha}.png", "fly%2Faudit.json", "fly%2Fstate.json"]
    assert urls["sensory.png"].endswith(f"/fly/frames/{frame_sha}.png")
    assert calls[0][2]["x-cache-control-max-age"] == "31536000"
    url, size, headers = calls[2]
    assert url == "https://vercel.com/api/blob/?pathname=fly%2Fstate.json" and size > 100
    assert headers["x-vercel-blob-store-id"] == "STORE123" and headers["x-allow-overwrite"] == "1"
    assert headers["x-add-random-suffix"] == "0" and headers["authorization"].startswith("Bearer vercel_blob_rw_")
    assert headers["x-cache-control-max-age"] == "60" and headers["x-api-blob-request-id"].startswith("STORE123:")
    # The operator learns SNAPSHOT_BASE_URL from the log, once.
    assert json.loads(lines[0]) == {"publish": {"base_url": "https://store.public.blob.vercel-storage.com/fly", "env": "SNAPSHOT_BASE_URL"}}
    # Nothing changed but the timestamp: only state.json is uploaded again.
    p(out)
    assert len(calls) == 4 and calls[3][0].endswith("fly%2Fstate.json") and len(lines) == 1


def test_blob_publisher_retries_and_reports(run_dir):
    out, clock = run_dir
    attempts, naps, lines = [], [], []

    def flaky(url, data, headers):
        attempts.append(headers["x-api-blob-request-attempt"])
        if len(attempts) < 3:
            raise BlobError(503, '{"error":{"code":"service_unavailable"}}')
        return {"url": "https://x/" + url.split("pathname=")[1]}

    p = BlobPublisher("vercel_blob_rw_STORE123_secretpart", request=flaky, sleep=naps.append, announce=lines.append)
    p.put("state.json", b"{}", "application/json")
    assert attempts == ["0", "1", "2"] and naps == [0.5, 1.0]

    def forbidden(url, data, headers):
        raise BlobError(403, '{"error":{"code":"forbidden","message":"bad token"}}')

    p = BlobPublisher("vercel_blob_rw_STORE123_secretpart", request=forbidden, sleep=naps.append, announce=lines.append)
    with pytest.raises(BlobError) as info:
        p(out)
    assert info.value.code == 403 and "bad token" in str(info.value) and len(naps) == 2  # no retry on a refusal
    assert json.loads(lines[-1])["publish_error"].startswith("BlobError: Blob API 403")


def test_local_server_routes(run_dir, tmp_path):
    out, clock = run_dir
    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>")
    import stonkfly.serve as serve_module

    original = serve_module.SITE
    serve_module.SITE = tmp_path
    board_api = FixtureApi(period=60.0, seed=2, clock=clock)
    server = make_server(out, port=0, board_api=board_api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        reply = urllib.request.urlopen(base + "/api/state")
        assert reply.headers["access-control-allow-origin"] == "*"
        state = json.loads(reply.read())
        assert state["ready"] and state["tick"] == 3
        board = json.loads(urllib.request.urlopen(base + "/api/board").read())
        assert board["live"] and len(board["tile_stakes"]) == 21
        assert json.loads(urllib.request.urlopen(base + "/api/audit").read())["ready"]
        png = urllib.request.urlopen(base + "/api/sensory.png")
        assert png.headers["content-type"] == "image/png"
        page = urllib.request.urlopen(base + "/")
        assert b"<title>t</title>" in page.read()
        # Static files must revalidate; the API documents are never cached.
        assert page.headers["cache-control"] == "no-cache"
        assert reply.headers["cache-control"] == "no-store"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/nope")
    finally:
        server.shutdown()
        server.server_close()
        serve_module.SITE = original


def test_events_tail_reads_only_the_end(tmp_path):
    from stonkfly.publish import TAIL_BYTES, _read_events

    path = tmp_path / "events.jsonl"
    with path.open("w") as f:
        for i in range(60_000):
            f.write(json.dumps({"tick": i, "pad": "x" * 100}) + "\n")
    assert path.stat().st_size > TAIL_BYTES
    events = _read_events(path, limit=50)
    assert [e["tick"] for e in events] == list(range(59_950, 60_000))
    assert _read_events(path, limit=5)[0]["tick"] == 59_995


def test_readonly_ledger_falls_back_to_immutable_without_wal(tmp_path, monkeypatch):
    """A closed WAL ledger on a read-only mount cannot be opened with mode=ro
    (SQLite wants to create the -shm file); with no -wal file an immutable open
    reads the same committed rows."""
    import sqlite3

    from stonkfly.publish import _open_readonly

    path = tmp_path / "ledger.sqlite"
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(x)")
    db.execute("INSERT INTO t VALUES (1)")
    db.close()  # a clean close checkpoints and removes -wal/-shm
    assert not (tmp_path / "ledger.sqlite-wal").exists()

    real_connect = sqlite3.connect
    uris = []

    def connect(database, **kw):
        uris.append(database)
        if "immutable" not in database:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return real_connect(database, **kw)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert _open_readonly(path).execute("SELECT x FROM t").fetchall() == [(1,)]
    assert len(uris) == 2 and "immutable=1" in uris[1]

    # With a -wal present the fallback would miss rows, so the error propagates.
    (tmp_path / "ledger.sqlite-wal").write_bytes(b"")
    with pytest.raises(sqlite3.OperationalError):
        _open_readonly(path)
