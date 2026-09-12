"""Single-worker run loop. Default execution is paper; live must be explicit."""

import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path

from .config import Settings
from .provenance import reconcile as reconcile_provenance, source_hashes


def main():
    p = argparse.ArgumentParser(prog="stonkfly")
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--reuse-doomfly", type=Path)
    sub.add_parser("verify")
    run = sub.add_parser("run")
    run.add_argument("--live", action="store_true", help="Send real deploys from the configured wallet")
    run.add_argument("--network", choices=["mainnet", "devnet"], default="mainnet")
    run.add_argument("--preflight-only", action="store_true", help="Read-only wallet and game checks; never deploys")
    run.add_argument("--resume-reviewed", action="store_true", help="After manual review, clear a transient halt")
    run.add_argument("--fixture", action="store_true", help="Synthetic offline rounds; paper only")
    run.add_argument("--stub-brain", action="store_true", help="Seeded random spikes instead of the connectome; paper only")
    run.add_argument("--steps", type=int, default=0, help="Observations to make; 0 keeps running")
    run.add_argument("--frozen", action="store_true", help="Freeze all memory efficacies for a control run")
    run.add_argument("--out", type=Path)
    run.add_argument("--stake", default="1", help="USDC per round (1-10)")
    run.add_argument("--loss-stop", default="20", help="Stop deploying after this USDC drawdown")
    run.add_argument("--daily-deploys", type=int, default=300)
    run.add_argument("--priority-fee", type=int, default=0, help="Microlamports per compute unit")
    run.add_argument("--neural-ms", type=float, default=500)
    run.add_argument("--publish", action="store_true", help="Upload public snapshots to Vercel Blob (BLOB_READ_WRITE_TOKEN)")
    serve_cmd = sub.add_parser("serve", help="Serve the watch site locally from a run directory")
    serve_cmd.add_argument("--out", type=Path, default=Path("runs/paper"))
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8787)
    serve_cmd.add_argument("--network", choices=["mainnet", "devnet"], default="mainnet")
    status = sub.add_parser("status")
    status.add_argument("--out", type=Path, default=Path("runs/paper"))
    keygen = sub.add_parser("keygen", help="Create a new dedicated Solana keypair file (never reuse a personal wallet)")
    keygen.add_argument("--out", type=Path, default=Path("satrush-keypair.json"))
    claim = sub.add_parser("claim", help="Move settled USDC and sats from the game to the wallet (live)")
    claim.add_argument("--network", choices=["mainnet", "devnet"], default="mainnet")
    claim.add_argument("--out", type=Path, default=Path("runs/live"))
    a = p.parse_args()
    from dotenv import load_dotenv

    # Never search parent projects for unrelated credentials.
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    if a.command in ("prepare", "verify"):
        from .data import prepare, verify

        if a.command == "prepare":
            prepare(a.reuse_doomfly)
        else:
            print(json.dumps(verify()))
        return
    if a.command == "status":
        import sqlite3

        db = sqlite3.connect(f"file:{a.out / 'ledger.sqlite'}?mode=ro", uri=True)
        meta = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM meta")}
        rows = db.execute(
            "SELECT status,COUNT(*) FROM deployments GROUP BY status"
        ).fetchall()
        won = db.execute(
            "SELECT COUNT(*) FROM deployments WHERE status='SETTLED' AND outcome LIKE '%\"won\": true%'"
        ).fetchone()[0]
        print(
            json.dumps(
                {
                    **{k: meta.get(k) for k in ["mode", "wallet", "tick", "cash", "initial_cash", "anchor", "halted", "last_outcomes"]},
                    "deployments": dict(rows),
                    "rounds_won": won,
                },
                indent=2,
            )
        )
        return
    if a.command == "keygen":
        print(json.dumps(keygen_file(a.out)), flush=True)
        return
    if a.command == "claim":
        claim_winnings(a)
        return
    if a.command == "serve":
        from .serve import serve

        serve(a.out, a.host, a.port, a.network)
        return
    if a.live and (a.fixture or a.stub_brain):
        p.error("Live mode forbids fixtures and the stub brain")
    if a.steps < 0:
        p.error("steps cannot be negative")
    settings = Settings(
        network=a.network,
        stake=a.stake,
        loss_stop=a.loss_stop,
        daily_deploys=a.daily_deploys,
        priority_fee_microlamports=a.priority_fee,
        learning=not a.frozen,
        neural_ms=a.neural_ms,
        pulse_ms=min(200, a.neural_ms),
    )
    out = a.out or Path("runs/live" if a.live else "runs/paper")
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / "worker.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A worker already owns this run directory")
    from .ledger import Ledger
    from .satrush.api import ENDPOINTS, FixtureApi, SatRushApi

    ledger = Ledger(out / "ledger.sqlite", settings, "live" if a.live else "paper")
    playing = False  # Only a failure while playing halts the ledger; a bad .env is not a state to reconcile.
    try:
        api = (
            FixtureApi()
            if a.fixture
            else SatRushApi(os.environ.get("SATRUSH_API_URL") or ENDPOINTS[a.network]["api"])
        )
        player = live_player(settings, ledger, api, a.network) if a.live else paper_player(settings, ledger, api)
        print(json.dumps(player.preflight()), flush=True)
        if a.resume_reviewed:
            if (out / "STOP").exists():
                raise RuntimeError("Remove STOP only after review")
            player.reconcile()
            reason = ledger.get("halted")
            if reason and "Loss stop" in reason:
                raise RuntimeError("A financial stop cannot be cleared by this flag")
            ledger.put("halted", None)
        else:
            player.reconcile()
        if a.preflight_only:
            return
        if a.stub_brain:
            from .neural.controller import StubController

            controller = StubController(settings)
            verified = {"stub": True}
        else:
            from .data import verify
            from .neural.controller import FlyController

            verified = verify()
            controller = FlyController(settings)
        cp = ledger.get("checkpoint")
        if cp:
            path = out / cp["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != cp["sha256"]:
                raise RuntimeError("Checkpoint integrity mismatch")
            controller.restore(path)
            controller.readout.load((ledger.get("observation") or {}).get("readout_state"))
        provenance = {
            "settings": dataclasses.asdict(settings),
            "dataset": verified,
            "readout": controller.readout.report,
            "readout_cells": controller.readout.identities,
            "mode": player.mode,
            "feed": "fixture" if a.fixture else f"satrush-public-{a.network}",
            "game": "SatRush: one deploy per round over the neurally selected tiles; fixed stake; no strategy layer.",
            "learning_validated": False,
            "pain_receptors_modeled": False,
            "timing": "Each round advances configured neural_ms regardless of wall time; no claim of real-time fly physiology.",
            "source_sha256": source_hashes(Path(__file__).parent),
        }
        if not a.stub_brain:
            provenance["circuit"] = controller.brain.circuit["report"]
            provenance["vision"] = controller.brain.visual_report
        changed = reconcile_provenance(ledger, out, provenance)
        if changed:
            print(json.dumps({"source_changed": changed, "note": "protocol unchanged; the run continues"}), flush=True)
        (out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        from .guard import Guard
        from .loop import GameLoop
        from .publish import BlobPublisher, write_files

        if a.publish:
            token = os.environ.get("BLOB_READ_WRITE_TOKEN")
            if not token:
                raise RuntimeError("--publish requires BLOB_READ_WRITE_TOKEN in .env")
            publisher = BlobPublisher(token, os.environ.get("STONKFLY_PUBLISH_PREFIX", "stonkfly"))
        else:
            publisher = write_files
        loop = GameLoop(
            settings, ledger, api, player, controller, Guard(settings, ledger, out / "STOP"), out,
            settle_margin=0.2 if a.fixture else 3.0, publisher=publisher,
        )
        playing = True
        loop.run(a.steps)
    except KeyboardInterrupt:
        print("Stopped; run state preserved.", flush=True)
    except Exception as e:
        # Never print RPC/API exception text blindly: it may contain wallet details.
        if playing and not ledger.get("halted"):
            ledger.halt(type(e).__name__)
        frames = traceback.extract_tb(e.__traceback__)
        origin = frames[-1] if frames else None
        internal = origin and Path(origin.filename).is_relative_to(Path(__file__).parent)
        diagnostic = {
            "type": type(e).__name__,
            "reason": str(e) if internal else "External dependency error; review connection and wallet state.",
            "locations": [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in frames],
        }
        (out / "error.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
        print(f"Stopped safely: {type(e).__name__}: {diagnostic['reason']}", file=sys.stderr)
        if playing:
            print("Inspect local state and reconcile before restarting.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        ledger.close()
        lock.close()


def keygen_file(path):
    """Write a fresh keypair (solana-keygen format, mode 600) and report only its public address."""
    from solders.keypair import Keypair

    path = Path(path)
    if path.exists():
        raise SystemExit(f"{path} exists; refusing to overwrite a keypair")
    keypair = Keypair()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(list(bytes(keypair))) + "\n")
    return {
        "address": str(keypair.pubkey()),
        "file": str(path),
        "next": "Fund this address with at most 100 USDC and about 0.02 SOL. Keep the file private; "
        "for Docker put its contents in SATRUSH_KEYPAIR_JSON in .env.",
    }


def paper_player(settings, ledger, api):
    from .satrush.player import PaperPlayer

    return PaperPlayer(settings, ledger, api)


def live_player(settings, ledger, api, network):
    if os.environ.get("STONKFLY_LIVE") != "I_ACCEPT_REAL_DEPLOYS":
        raise RuntimeError("Live mode requires STONKFLY_LIVE=I_ACCEPT_REAL_DEPLOYS in .env")
    from .satrush.api import ENDPOINTS
    from .satrush.chain import Rpc
    from .satrush.player import LivePlayer
    from .satrush.wallet import load_keypair

    try:
        keypair = load_keypair(os.environ.get("SATRUSH_KEYPAIR", "satrush-keypair.json"))
    except FileNotFoundError:
        raise RuntimeError(
            "No wallet key: set SATRUSH_KEYPAIR_JSON in .env (the 64-number array from `keygen`), "
            "or SATRUSH_KEYPAIR to a keypair file the worker can read"
        ) from None
    rpc = Rpc(os.environ.get("SATRUSH_RPC_URL") or ENDPOINTS[network]["rpc"])
    return LivePlayer(settings, ledger, api, rpc, keypair)


def claim_winnings(a):
    from .ledger import Ledger
    from .satrush.api import ENDPOINTS, SatRushApi

    settings = Settings(network=a.network)
    a.out.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(a.out / "ledger.sqlite", settings, "live")
    try:
        api = SatRushApi(os.environ.get("SATRUSH_API_URL") or ENDPOINTS[a.network]["api"])
        player = live_player(settings, ledger, api, a.network)
        print(json.dumps(player.preflight()), flush=True)
        if ledger.pending():
            raise RuntimeError("Reconcile the pending deploy before claiming")
        print(json.dumps(player.claim()), flush=True)
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
