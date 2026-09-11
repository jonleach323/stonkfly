"""Snapshots and the local watch server; no network, no wallet."""

import json
import threading
import urllib.request

import pytest

from stonkfly.config import Settings
from stonkfly.guard import Guard
from stonkfly.ledger import Ledger
from stonkfly.loop import GameLoop
from stonkfly.neural.controller import StubController
from stonkfly.publish import BlobPublisher, audit, snapshot, write_files
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
    json.dumps(s, allow_nan=False)


def test_audit_and_files(run_dir):
    out, clock = run_dir
    a = audit(out)
    assert a["ready"] and len(a["deployments"]) == 3 and a["program"].startswith("satRush")
    assert a["deployments"][0]["input_sha256"]
    files = write_files(out)
    assert set(files) == {"state.json", "audit.json", "sensory.png"}
    state = json.loads((out / "site" / "state.json").read_text())
    assert state["publication"]["audit_sha256"]
    assert (out / "site" / "sensory.png").read_bytes()[:4] == b"\x89PNG"


def test_snapshot_without_ledger(tmp_path):
    assert snapshot(tmp_path)["ready"] is False
    assert audit(tmp_path)["ready"] is False


def test_blob_publisher_requests(run_dir):
    out, clock = run_dir
    calls = []

    def fake(url, data, headers):
        calls.append((url, len(data), headers))
        return {"url": "https://store.public.blob.vercel-storage.com/" + url.split("pathname=")[1]}

    with pytest.raises(ValueError):
        BlobPublisher("nope")
    p = BlobPublisher("vercel_blob_rw_STORE123_secretpart", prefix="/fly/", request=fake)
    urls = p(out)
    assert set(urls) == {"state.json", "audit.json", "sensory.png"}
    url, size, headers = calls[0]
    assert url == "https://vercel.com/api/blob/?pathname=fly%2Fstate.json" and size > 100
    assert headers["x-vercel-blob-store-id"] == "STORE123" and headers["x-allow-overwrite"] == "1"
    assert headers["x-add-random-suffix"] == "0" and headers["authorization"].startswith("Bearer vercel_blob_rw_")


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
        state = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert state["ready"] and state["tick"] == 3
        board = json.loads(urllib.request.urlopen(base + "/api/board").read())
        assert board["live"] and len(board["tile_stakes"]) == 21
        assert json.loads(urllib.request.urlopen(base + "/api/audit").read())["ready"]
        png = urllib.request.urlopen(base + "/api/sensory.png")
        assert png.headers["content-type"] == "image/png"
        assert b"<title>t</title>" in urllib.request.urlopen(base + "/").read()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/nope")
    finally:
        server.shutdown()
        server.server_close()
        serve_module.SITE = original
