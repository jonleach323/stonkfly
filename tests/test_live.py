"""Live player against an in-memory RPC double. Nothing here reaches a network."""

import base64
import struct

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from stonkfly.config import Settings
from stonkfly.ledger import Ledger
from stonkfly.satrush import program as p
from stonkfly.satrush.api import FixtureApi
from stonkfly.satrush.chain import Rpc, Unconfirmed
from stonkfly.satrush.player import LivePlayer

USDC = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
BTC = Pubkey.from_string("cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij")
TOKEN = Pubkey.from_string("SATqS9DYpLQsM2z51P4QCoqJRHa5wboV4qjJerJRUSH")


def config_account():
    keys = [Pubkey.default()] * 4 + [USDC, BTC]
    data = p.DISCRIMINATOR["account.satrush_config"] + struct.pack("<HB", 2, 253)
    data += b"".join(bytes(k) for k in keys)
    data += struct.pack("<IIIIIII", 208, 194, 48, 1200, 1000, 100, 3500)
    data += struct.pack("<QQQHI", 1_000_000, 864000, 0, 1097, 50) + bytes(TOKEN) + bytes(35)
    return data


def token_account(amount):
    return bytes(64) + struct.pack("<Q", amount) + bytes(93)


def miner_account(authority, unclaimed_usd):
    data = p.DISCRIMINATOR["account.miner"] + struct.pack("<HB", 2, 254) + bytes(authority)
    data += struct.pack("<QQQIIQQq", unclaimed_usd, 0, 0, 0, 0, 0, 0, 0)
    return data + bytes(p.SYSTEM_PROGRAM) + struct.pack("<Q", 0) + bytes(62)


class FakeChain:
    def __init__(self, keypair, usdc=50_000_000, unclaimed=0):
        self.keypair = keypair
        self.accounts = {
            str(p.config_pda()): config_account(),
            str(p.ata(keypair.pubkey(), USDC)): token_account(usdc),
        }
        if unclaimed:
            self.accounts[str(p.miner_pda(keypair.pubkey()))] = miner_account(keypair.pubkey(), unclaimed)
        self.sent = []
        self.statuses = {}
        self.height = 100
        self.behaviour = "confirm"

    def __call__(self, url, payload):
        m, params = payload["method"], payload["params"]
        if m == "getAccountInfo":
            data = self.accounts.get(params[0])
            value = None if data is None else {"data": [base64.b64encode(data).decode(), "base64"]}
            return {"result": {"value": value}}
        if m == "getBalance":
            return {"result": {"value": 10_000_000}}
        if m == "getLatestBlockhash":
            return {"result": {"value": {"blockhash": str(Pubkey.default()), "lastValidBlockHeight": 150}}}
        if m == "getBlockHeight":
            self.height += 30
            return {"result": self.height}
        if m == "sendTransaction":
            tx = VersionedTransaction.from_bytes(base64.b64decode(params[0]))
            self.sent.append(tx)
            sig = str(tx.signatures[0])
            if self.behaviour == "confirm":
                self.statuses[sig] = {"confirmationStatus": "confirmed", "err": None}
            elif self.behaviour == "reject":
                self.statuses[sig] = {"confirmationStatus": "confirmed", "err": {"InstructionError": [0, "Custom"]}}
            return {"result": sig}
        if m == "getSignatureStatuses":
            return {"result": {"value": [self.statuses.get(params[0][0])]}}
        raise AssertionError(m)


@pytest.fixture
def live(tmp_path):
    keypair = Keypair()
    chain = FakeChain(keypair)
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "live")
    api = FixtureApi(period=60.0, clock=lambda: 5.0)
    api.config = lambda: {**FixtureApi.config(api), "usd_mint": str(USDC), "btc_mint": str(BTC)}
    api.user_deployments = lambda wallet, limit=10: []
    player = LivePlayer(settings, ledger, api, Rpc("fake", post=chain, sleep=lambda s: None), keypair)
    yield player, chain, ledger, keypair
    ledger.close()


def plan(round_id=1000):
    return {"round_id": round_id, "tiles": [4], "mask": 8, "amount": 1_000_000, "stake_usd": "1"}


def test_preflight_records_wallet_and_start(live):
    player, chain, ledger, keypair = live
    info = player.preflight()
    assert info["usdc"] == 50 and ledger.get("wallet") == str(keypair.pubkey())
    assert ledger.get("initial_cash") == "50"
    chain.accounts[str(p.ata(keypair.pubkey(), USDC))] = token_account(150_000_000)
    player.preflight()  # a later balance never re-anchors the start
    assert ledger.get("initial_cash") == "50"


def test_deploy_signs_one_instruction_and_confirms(live):
    player, chain, ledger, keypair = live
    result = player.deploy(plan(), now=5.0)
    assert result["status"] == "CONFIRMED"
    tx = chain.sent[0]
    msg = tx.message
    assert len(msg.instructions) == 1
    ix = msg.instructions[0]
    assert msg.account_keys[ix.program_id_index] == p.PROGRAM
    assert bytes(ix.data)[:8] == p.DISCRIMINATOR["deploy_public"]
    assert bytes(ix.data)[8:12] == struct.pack("<I", 8)
    assert msg.account_keys[0] == keypair.pubkey()
    row = ledger.deployment(1000)
    assert row["status"] == "CONFIRMED" and row["signature"] == str(tx.signatures[0])
    with pytest.raises(RuntimeError):
        player.deploy(plan(), now=6.0)  # one deploy per round


def test_rejected_transaction_frees_the_round(live):
    player, chain, ledger, keypair = live
    chain.behaviour = "reject"
    result = player.deploy(plan(), now=5.0)
    assert result["status"] == "FAILED" and ledger.deployment(1000)["status"] == "FAILED"
    assert not ledger.pending()


def test_unknown_outcome_halts_until_reconciled(live):
    player, chain, ledger, keypair = live
    chain.behaviour = "silent"
    player.rpc.confirm = lambda *a, **k: (_ for _ in ()).throw(Unconfirmed("sig"))
    with pytest.raises(Unconfirmed):
        player.deploy(plan(), now=5.0)
    assert ledger.get("halted") == "Unknown deploy outcome"
    assert ledger.deployment(1000)["status"] == "UNKNOWN"
    # Chain later shows it landed: reconcile confirms instead of resending.
    chain.statuses[ledger.deployment(1000)["signature"]] = {"confirmationStatus": "finalized", "err": None}
    assert player.reconcile() == [(1000, "CONFIRMED")]
    assert ledger.get("halted") is None and not ledger.pending()


def test_shortfall_claims_unclaimed_usd_first(tmp_path):
    keypair = Keypair()
    chain = FakeChain(keypair, usdc=200_000, unclaimed=5_000_000)
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "live")
    api = FixtureApi(period=60.0, clock=lambda: 5.0)
    api.config = lambda: {**FixtureApi.config(api), "usd_mint": str(USDC), "btc_mint": str(BTC)}
    player = LivePlayer(settings, ledger, api, Rpc("fake", post=chain, sleep=lambda s: None), keypair)
    try:
        ixs = player.instructions(plan())
        assert [bytes(i.data)[:8] for i in ixs] == [p.DISCRIMINATOR["claim_usd"], p.DISCRIMINATOR["deploy_public"]]
        assert struct.unpack("<Q", bytes(ixs[0].data)[8:])[0] == 800_000
        assert str(player.available(api.board())) == "5.2"
    finally:
        ledger.close()


def test_keypair_from_env_or_file(tmp_path):
    import json
    import os

    from stonkfly.satrush.wallet import load_keypair

    keypair = Keypair()
    raw = list(bytes(keypair))
    assert load_keypair("missing.json", env={"SATRUSH_KEYPAIR_JSON": json.dumps(raw)}).pubkey() == keypair.pubkey()
    with pytest.raises(ValueError):
        load_keypair("missing.json", env={"SATRUSH_KEYPAIR_JSON": "[1,2,3]"})
    path = tmp_path / "k.json"
    path.write_text(json.dumps(raw))
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError):
        load_keypair(path, env={})
    os.chmod(path, 0o600)
    assert load_keypair(path, env={}).pubkey() == keypair.pubkey()
    with pytest.raises(FileNotFoundError):
        load_keypair(tmp_path / "none.json", env={})


def test_young_unknown_outcome_is_transient(live):
    from stonkfly.errors import Transient

    player, chain, ledger, keypair = live
    chain.behaviour = "silent"
    player.rpc.confirm = lambda *a, **k: (_ for _ in ()).throw(Unconfirmed("sig"))
    import time

    with pytest.raises(Unconfirmed):
        player.deploy(plan(), now=time.time())  # a fresh intent, not one from 1970
    # Nothing on chain yet and the intent is minutes old at most: wait, do not halt harder or resend.
    chain.statuses.clear()
    with pytest.raises(Transient):
        player.reconcile()
    assert ledger.deployment(1000)["status"] == "UNKNOWN" and not chain.sent[1:]


def test_keygen_writes_a_private_file_once(tmp_path):
    import json
    import os

    from stonkfly.cli import keygen_file
    from stonkfly.satrush.wallet import load_keypair

    path = tmp_path / "k.json"
    info = keygen_file(path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert str(load_keypair(path, env={}).pubkey()) == info["address"]
    assert len(json.loads(path.read_text())) == 64
    with pytest.raises(SystemExit):
        keygen_file(path)


def test_preflight_accepts_any_wallet_balance(tmp_path):
    """No funding cap: the wallet plays with what it holds; the loss stop is the brake."""
    keypair = Keypair()
    chain = FakeChain(keypair, usdc=1_000_370_000)
    settings = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", settings, "live")
    api = FixtureApi(period=60.0, clock=lambda: 5.0)
    api.config = lambda: {**FixtureApi.config(api), "usd_mint": str(USDC), "btc_mint": str(BTC)}
    api.user_deployments = lambda wallet, limit=10: []
    player = LivePlayer(settings, ledger, api, Rpc("fake", post=chain, sleep=lambda s: None), keypair)
    player.preflight()
    assert ledger.get("initial_cash") == "1000.37"
    ledger.close()
