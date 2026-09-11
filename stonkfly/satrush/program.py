"""Instruction and account layouts of the SatRush program, reconstructed from its
public web client. Only the instructions this experiment needs are encoded:
DeployPublic (stake a round), ClaimUsd and ClaimSats (move settled winnings
to the wallet). No admin, automation, vault-ticket or affiliate actions."""

import struct

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

PROGRAM = Pubkey.from_string("satRushGBRY2vgapeTAkoxz26vL2cYqyPi6CnBj7Tco")
RNG_PROGRAM = Pubkey.from_string("SatRngpc6hC9uMXqS4dRk4trqhySktoxMXYSSRbjemd")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
SLOT_HASHES = Pubkey.from_string("SysvarS1otHashes111111111111111111111111111")
COMPUTE_BUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")

DISCRIMINATOR = {
    "deploy_public": bytes([193, 31, 240, 159, 30, 224, 157, 85]),
    "claim_usd": bytes([241, 89, 98, 124, 223, 205, 215, 104]),
    "claim_sats": bytes([196, 77, 168, 181, 6, 119, 249, 154]),
    "account.miner": bytes([223, 113, 15, 54, 123, 122, 140, 100]),
    "account.public_deployment": bytes([209, 64, 180, 169, 40, 181, 110, 236]),
    "account.round": bytes([87, 127, 165, 51, 73, 78, 116, 174]),
    "account.board": bytes([79, 48, 160, 63, 153, 132, 240, 56]),
    "account.satrush_config": bytes([113, 169, 164, 118, 148, 217, 160, 200]),
}


def pda(seeds, program=PROGRAM):
    address, _ = Pubkey.find_program_address(list(seeds), program)
    return address


def board_pda():
    return pda([b"board"])


def config_pda():
    return pda([b"satrush_config"])


def sats_vault_pda():
    return pda([b"sats_vault"])


def token_vault_pda():
    return pda([b"token_vault"])


def event_authority_pda():
    return pda([b"__event_authority"])


def round_pda(round_id):
    return pda([b"round", struct.pack("<I", int(round_id))])


def miner_pda(authority):
    return pda([b"miner", bytes(authority)])


def deployment_pda(authority, round_id):
    return pda([b"public_deployment", bytes(authority), struct.pack("<I", int(round_id))])


def ata(owner, mint):
    return pda([bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM)


def rng_accounts():
    rotor = pda([b"rotor", b"round"], RNG_PROGRAM)
    config = pda([b"config"], RNG_PROGRAM)
    return [
        AccountMeta(rotor, is_signer=False, is_writable=True),
        AccountMeta(config, is_signer=False, is_writable=False),
        AccountMeta(RNG_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(SLOT_HASHES, is_signer=False, is_writable=False),
    ]


def ro(key):
    return AccountMeta(key, is_signer=False, is_writable=False)


def rw(key):
    return AccountMeta(key, is_signer=False, is_writable=True)


def deploy_public(authority, usd_mint, round_id, mask, amount, affiliate=None):
    """One deploy per wallet per round: `amount` micro-USDC over `mask` tiles."""
    if not 0 < mask < 1 << 21:
        raise ValueError("Selection mask must select 1-21 tiles")
    if not 0 < amount < 1 << 64:
        raise ValueError("Amount out of range")
    data = DISCRIMINATOR["deploy_public"] + struct.pack("<IQ?", mask, amount, False)
    accounts = [
        AccountMeta(authority, is_signer=True, is_writable=True),
        ro(config_pda()),
        rw(board_pda()),
        rw(round_pda(round_id)),
        ro(usd_mint),
        rw(ata(authority, usd_mint)),
        rw(ata(board_pda(), usd_mint)),
        rw(deployment_pda(authority, round_id)),
        rw(miner_pda(authority)),
        ro(PROGRAM if affiliate is None else affiliate),
        ro(TOKEN_PROGRAM),
        ro(SYSTEM_PROGRAM),
        ro(event_authority_pda()),
        ro(PROGRAM),
        *rng_accounts(),
    ]
    return Instruction(PROGRAM, data, accounts)


def claim_usd(authority, usd_mint, amount):
    data = DISCRIMINATOR["claim_usd"] + struct.pack("<Q", int(amount))
    accounts = [
        AccountMeta(authority, is_signer=True, is_writable=True),
        ro(config_pda()),
        ro(board_pda()),
        rw(miner_pda(authority)),
        ro(usd_mint),
        rw(ata(board_pda(), usd_mint)),
        rw(ata(authority, usd_mint)),
        ro(TOKEN_PROGRAM),
        ro(ATA_PROGRAM),
        ro(SYSTEM_PROGRAM),
    ]
    return Instruction(PROGRAM, data, accounts)


def claim_sats(authority, btc_mint, token_mint, shares):
    data = DISCRIMINATOR["claim_sats"] + struct.pack("<Q", int(shares))
    accounts = [
        AccountMeta(authority, is_signer=True, is_writable=True),
        ro(config_pda()),
        rw(sats_vault_pda()),
        rw(token_vault_pda()),
        rw(miner_pda(authority)),
        ro(btc_mint),
        ro(token_mint),
        rw(ata(sats_vault_pda(), btc_mint)),
        rw(ata(token_vault_pda(), token_mint)),
        rw(ata(authority, btc_mint)),
        rw(ata(authority, token_mint)),
        ro(TOKEN_PROGRAM),
        ro(ATA_PROGRAM),
        ro(SYSTEM_PROGRAM),
        ro(event_authority_pda()),
        ro(PROGRAM),
    ]
    return Instruction(PROGRAM, data, accounts)


def compute_unit_price(microlamports):
    return Instruction(COMPUTE_BUDGET, bytes([3]) + struct.pack("<Q", int(microlamports)), [])


def compute_unit_limit(units):
    return Instruction(COMPUTE_BUDGET, bytes([2]) + struct.pack("<I", int(units)), [])


class Layout:
    def __init__(self, data, discriminator):
        if len(data) < 8 or data[:8] != DISCRIMINATOR[discriminator]:
            raise ValueError(f"Not a {discriminator} account")
        self.data = data
        self.offset = 8

    def take(self, fmt):
        size = struct.calcsize(fmt)
        values = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += size
        return values[0] if len(values) == 1 else values

    def key(self):
        raw = self.data[self.offset : self.offset + 32]
        self.offset += 32
        return Pubkey.from_bytes(raw)

    def skip(self, n):
        self.offset += n


def decode_miner(data):
    m = Layout(data, "account.miner")
    version, bump = m.take("<HB")
    out = {"version": version, "bump": bump, "authority": str(m.key())}
    out["unclaimed_usd"] = m.take("<Q")
    out["unclaimed_btc_shares"] = m.take("<Q")
    out["hashrate"] = m.take("<Q")
    out["current_streak"] = m.take("<I")
    out["last_mined_round_id"] = m.take("<I")
    out["unclaimed_hashrate"] = m.take("<Q")
    out["grubstake_usd"] = m.take("<Q")
    out["grubstake_expires"] = m.take("<q")
    affiliate = m.key()
    out["affiliate"] = None if affiliate == SYSTEM_PROGRAM else str(affiliate)
    out["unclaimed_token_shares"] = m.take("<Q")
    return out


def decode_public_deployment(data):
    m = Layout(data, "account.public_deployment")
    version, bump = m.take("<HB")
    out = {"version": version, "bump": bump, "authority": str(m.key())}
    out["round_id"] = m.take("<I")
    out["deployed_usd"] = m.take("<Q")
    out["total_stake_usd"] = m.take("<Q")
    out["selection_mask"] = m.take("<I")
    out["streak_multiplier"] = m.take("<I")
    return out


def decode_board(data):
    m = Layout(data, "account.board")
    version, bump = m.take("<HB")
    out = {"version": version, "bump": bump}
    out["round_id"] = m.take("<I")
    out["round_duration"] = m.take("<I")
    out["start_slot"] = m.take("<Q")
    out["end_slot"] = m.take("<Q")
    return out


def decode_config(data):
    m = Layout(data, "account.satrush_config")
    version, bump = m.take("<HB")
    out = {"version": version, "bump": bump}
    for name in ["owner", "admin", "game_authority", "fee_recipient"]:
        out[name] = str(m.key())
    out["usd_mint"] = str(m.key())
    out["btc_mint"] = str(m.key())
    for name in [
        "strike_fee_bps",
        "epoch_fee_bps",
        "one_btc_fee_bps",
        "sats_vault_round_fee_bps",
        "vault_exit_fee_bps",
        "protocol_fee_bps",
        "unclaimed_hashrate_bps",
    ]:
        out[name] = m.take("<I")
    out["min_deploy_usd_amount"] = m.take("<Q")
    out["epoch_vault_iteration_duration"] = m.take("<Q")
    out["deployment_settle_grace_duration"] = m.take("<Q")
    out["strike_trigger_modulus"] = m.take("<H")
    out["buybacks_fee_bps"] = m.take("<I")
    out["token_mint"] = str(m.key())
    return out


def decode_round(data):
    m = Layout(data, "account.round")
    version, bump = m.take("<HB")
    out = {"version": version, "bump": bump}
    out["id"] = m.take("<I")
    out["state"] = ["active", "revealed", "settled", "finished"][m.take("<B")]
    m.skip(32)
    present = m.take("<B")
    tile = m.take("<B") if present else None
    out["winning_tile"] = tile
    return out


def decode_token_amount(data):
    """SPL token account amount (u64 at offset 64)."""
    if len(data) < 72:
        raise ValueError("Not a token account")
    return struct.unpack_from("<Q", data, 64)[0]
