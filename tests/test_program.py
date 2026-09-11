"""Instruction layout and PDA derivation against known mainnet addresses."""

import base64
import struct

import pytest
from solders.pubkey import Pubkey

from stonkfly.satrush import program as p

AUTHORITY = Pubkey.from_string("2Gk1yQTVXPYNyuA5m1ngnG1aV3LqfeTKhJJ9QMKFFBMS")
USDC = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
BOARD_ACCOUNT = base64.b64decode(
    "TzCgP5mE8DgCAP4c2QAAyAAAAHlNlhoAAAAAQU6WGgAAAAAAAAAAAAAAAG2dk+MBAAAA9V4/AAAAAABH1QAAqWVFiAAAAACxoBAAAAAAANQBAAAAAAAAm38UYgIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


def test_pdas_match_public_addresses():
    assert str(p.board_pda()) == "FbVd1fsYKpEj1Bzupbjo2VGJyfgLU9aw4r8U5uuR8v6s"
    assert str(p.config_pda()) == "5pJUG7jjfQxQ8jmrbdpNNCZrNmqXXkppKPNMs4Twfyfc"
    assert str(p.round_pda(55575)) == "8Pa5vVYGbPTeKQ38wUAkc6w3iBi3DnY6b4X35UFCQdXH"
    assert str(p.deployment_pda(AUTHORITY, 55575)) == "9sYbj2zEn5f2gvd9vgyArY872d2rXupkg4WZUGPGd8ja"
    assert str(p.sats_vault_pda()) == "5ATZbUaByMDTePxRdjAaisopAULwsjMFTrM4f5vXVJQu"
    assert str(p.token_vault_pda()) == "BfwR6rayqvrGwL2oD2PWkPJCJ3RYTcjKnn4YafhVTrs1"


def test_deploy_instruction_layout():
    ix = p.deploy_public(AUTHORITY, USDC, 55575, 949118, 1_000_000)
    assert ix.program_id == p.PROGRAM
    assert ix.data == p.DISCRIMINATOR["deploy_public"] + struct.pack("<IQ?", 949118, 1_000_000, False)
    keys = [a.pubkey for a in ix.accounts]
    assert keys[0] == AUTHORITY and ix.accounts[0].is_signer and ix.accounts[0].is_writable
    assert keys[3] == p.round_pda(55575)
    assert keys[7] == p.deployment_pda(AUTHORITY, 55575)
    assert keys[8] == p.miner_pda(AUTHORITY)
    assert keys[9] == p.PROGRAM and not ix.accounts[9].is_writable  # optional affiliate
    assert keys[13] == p.PROGRAM
    assert keys[-1] == p.SLOT_HASHES and keys[-2] == p.RNG_PROGRAM
    assert len(keys) == 18
    with pytest.raises(ValueError):
        p.deploy_public(AUTHORITY, USDC, 1, 0, 1_000_000)
    with pytest.raises(ValueError):
        p.deploy_public(AUTHORITY, USDC, 1, 1, 0)


def test_claim_instructions_and_budget():
    usd = p.claim_usd(AUTHORITY, USDC, 5)
    assert usd.data == p.DISCRIMINATOR["claim_usd"] + struct.pack("<Q", 5)
    assert len(usd.accounts) == 10
    sats = p.claim_sats(AUTHORITY, USDC, USDC, 7)
    assert sats.data == p.DISCRIMINATOR["claim_sats"] + struct.pack("<Q", 7)
    assert len(sats.accounts) == 16
    assert p.compute_unit_price(10).data == bytes([3]) + struct.pack("<Q", 10)
    assert p.compute_unit_limit(400_000).data == bytes([2]) + struct.pack("<I", 400_000)


def test_board_decoder():
    board = p.decode_board(BOARD_ACCOUNT)
    assert board == {
        "version": 2,
        "bump": 254,
        "round_id": 55580,
        "round_duration": 200,
        "start_slot": 446057849,
        "end_slot": 446058049,
    }
    with pytest.raises(ValueError):
        p.decode_miner(BOARD_ACCOUNT)


def test_miner_decoder_round_trip():
    data = p.DISCRIMINATOR["account.miner"] + struct.pack("<HB", 2, 254) + bytes(AUTHORITY)
    data += struct.pack("<QQQIIQQq", 558512658, 59414998, 47145, 389, 55587, 24077, 0, 0)
    data += bytes(p.SYSTEM_PROGRAM) + struct.pack("<Q", 176043806811) + bytes(62)
    miner = p.decode_miner(data)
    assert miner["unclaimed_usd"] == 558512658 and miner["affiliate"] is None
    assert miner["last_mined_round_id"] == 55587
    token = bytes(64) + struct.pack("<Q", 5871) + bytes(93)
    assert p.decode_token_amount(token) == 5871
