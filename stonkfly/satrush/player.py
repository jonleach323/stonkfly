"""Paper and live players. Paper never touches a wallet; live sends one guarded deploy."""

import time
from decimal import Decimal

from solders.pubkey import Pubkey

from ..config import D
from ..errors import Transient
from . import program
from .chain import Unconfirmed, build_transaction
from .rules import (
    SATS_PER_BTC,
    implied_btc_price,
    observed_fee_bps,
    outcome_from_record,
    round_fee_bps,
    simulate,
)

MIN_SOL_LAMPORTS = 5_000_000  # 0.005 SOL keeps fees payable for hundreds of rounds.


def btc_price(board):
    value = board.prices.get("btc")
    if not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeError("Board carries no BTC price")
    return D(repr(float(value)))


def sats_share_value(board, shares, price):
    vault = (board.raw or {}).get("sats_vault") or {}
    total_btc = int(vault.get("btc_amount") or 0)
    total_shares = int(vault.get("btc_shares") or 0)
    if shares <= 0 or total_shares <= 0:
        return Decimal(0)
    sats = D(shares) * D(total_btc) / D(total_shares)
    return sats / D(SATS_PER_BTC) * price


class PaperPlayer:
    mode = "paper"

    def __init__(self, settings, ledger, api):
        self.s = settings
        self.l = ledger
        self.api = api
        # The public config omits one fee component; a settled round shows the real total.
        configured = round_fee_bps(api.config())
        previous = (api.board().previous_round or {}).get("id")
        self.fee_bps = observed_fee_bps(api.round(previous) if previous else {}, configured)

    def preflight(self):
        return {"mode": self.mode, "cash": str(self.l.cash), "fee_bps": self.fee_bps}

    def reconcile(self):
        return []

    def equity(self, board):
        return self.l.cash

    def available(self, board):
        return self.l.cash

    def deploy(self, plan, now=None):
        now = time.time() if now is None else now
        self.l.reserve(plan, now, debit=True)
        self.l.mark(plan["round_id"], "PAPER")
        return {"status": "PAPER", "round_id": plan["round_id"], "tiles": plan["tiles"]}

    def resolve(self, board):
        outcomes = []
        for row in self.l.unsettled():
            if row["round_id"] >= board.round_id:
                continue
            data = self.api.round(row["round_id"])
            if data.get("winning_tile") is None or data.get("state") not in ("settled", "finished"):
                continue
            previous = board.previous_round or {}
            if previous.get("id") == row["round_id"] and previous.get("winning_tile") is not None:
                # The board's previous-round view carries per-tile stakes and the pot.
                data = {**previous, **{k: v for k, v in data.items() if v is not None}}
            price = implied_btc_price(data, fallback=btc_price(board))
            fee = observed_fee_bps(data, self.fee_bps)
            outcome = simulate(row["plan"]["amount"], row["plan"]["mask"], data, fee, price)
            outcome["fee_bps"] = fee
            self.l.settle(row["round_id"], outcome, credit=True)
            outcomes.append({"round_id": row["round_id"], **outcome})
        return outcomes


class LivePlayer:
    mode = "live"

    def __init__(self, settings, ledger, api, rpc, keypair):
        self.s = settings
        self.l = ledger
        self.api = api
        self.rpc = rpc
        self.keypair = keypair
        self.wallet = keypair.pubkey()
        config = api.config()
        chain_config = program.decode_config(rpc.account(program.config_pda()))
        if (
            chain_config["usd_mint"] != config["usd_mint"]
            or chain_config["btc_mint"] != config["btc_mint"]
        ):
            raise RuntimeError("API and on-chain config disagree on mints")
        self.config = chain_config
        self.usd_mint = Pubkey.from_string(chain_config["usd_mint"])
        self.btc_mint = Pubkey.from_string(chain_config["btc_mint"])
        self.token_mint = Pubkey.from_string(chain_config["token_mint"])
        if int(chain_config["min_deploy_usd_amount"]) > int(D(settings.stake) * 10**6):
            raise RuntimeError("Configured stake is below the program minimum")
        self.fee_bps = round_fee_bps(chain_config)

    def miner(self):
        data = self.rpc.account(program.miner_pda(self.wallet))
        return None if data is None else program.decode_miner(data)

    def wallet_usdc(self):
        return self.rpc.token_amount(program.ata(self.wallet, self.usd_mint))

    def preflight(self):
        stored = self.l.get("wallet")
        if stored not in (None, str(self.wallet)):
            raise RuntimeError("Ledger belongs to a different wallet")
        lamports = self.rpc.lamports(self.wallet)
        usdc = self.wallet_usdc()
        miner = self.miner()
        unclaimed = 0 if miner is None else miner["unclaimed_usd"]
        if lamports < MIN_SOL_LAMPORTS:
            raise RuntimeError("Wallet needs at least 0.005 SOL for network fees")
        if stored is None:
            with self.l.transaction():
                self.l.put("wallet", str(self.wallet))
                start = str(D(usdc + unclaimed) / 10**6)
                self.l.put("initial_cash", start)
                self.l.put("anchor", start)
        return {
            "mode": self.mode,
            "network": self.s.network,
            "wallet": str(self.wallet),
            "sol": lamports / 1e9,
            "usdc": usdc / 1e6,
            "unclaimed_usdc": unclaimed / 1e6,
            "fee_bps": self.fee_bps,
        }

    def reconcile(self):
        """Resolve SENT/UNKNOWN intents from chain state before any new deploy."""
        resolved = []
        for row in self.l.pending():
            rid = row["round_id"]
            if row["status"] == "PREPARED" or not row["signature"]:
                self.l.refund(rid)
                resolved.append((rid, "FAILED"))
                continue
            status = self.rpc.signature_status(row["signature"])
            if status is not None and status["confirmed"] and status["err"] is None:
                self.l.mark(rid, "CONFIRMED")
                resolved.append((rid, "CONFIRMED"))
            elif status is not None and status["err"] is not None:
                self.l.refund(rid)
                resolved.append((rid, "FAILED"))
            else:
                on_chain = self.rpc.account(program.deployment_pda(self.wallet, rid))
                records = self.api.user_deployments(str(self.wallet), 20)
                if on_chain is not None or any(r.get("round_id") == rid for r in records):
                    self.l.mark(rid, "CONFIRMED")
                    resolved.append((rid, "CONFIRMED"))
                elif row["status"] == "UNKNOWN" and time.time() - row["created"] > 300:
                    self.l.refund(rid)
                    resolved.append((rid, "FAILED"))
                else:
                    raise Transient("Deploy outcome still unknown; waiting to reconcile again")
        if resolved:
            self.l.put("halted", None) if self.l.get("halted") == "Unknown deploy outcome" else None
        return resolved

    def equity(self, board):
        price = btc_price(board)
        miner = self.miner()
        usdc = D(self.wallet_usdc()) / 10**6
        if miner is None:
            return usdc
        unclaimed = D(miner["unclaimed_usd"]) / 10**6
        return usdc + unclaimed + sats_share_value(board, miner["unclaimed_btc_shares"], price)

    def available(self, board):
        miner = self.miner()
        unclaimed = 0 if miner is None else miner["unclaimed_usd"]
        return D(self.wallet_usdc() + unclaimed) / 10**6

    def instructions(self, plan):
        ixs = []
        if self.s.priority_fee_microlamports:
            ixs.append(program.compute_unit_limit(400_000))
            ixs.append(program.compute_unit_price(self.s.priority_fee_microlamports))
        wallet_usdc = self.wallet_usdc()
        miner = self.miner()
        affiliate = None
        if miner is not None:
            if miner["affiliate"]:
                affiliate = Pubkey.from_string(miner["affiliate"])
            shortfall = plan["amount"] - wallet_usdc
            if shortfall > 0:
                if miner["unclaimed_usd"] < shortfall:
                    raise RuntimeError("Insufficient USDC in wallet plus unclaimed balance")
                ixs.append(program.claim_usd(self.wallet, self.usd_mint, shortfall))
        elif wallet_usdc < plan["amount"]:
            raise RuntimeError("Insufficient USDC in wallet")
        ixs.append(
            program.deploy_public(
                self.wallet, self.usd_mint, plan["round_id"], plan["mask"], plan["amount"], affiliate
            )
        )
        return ixs

    def deploy(self, plan, now=None):
        now = time.time() if now is None else now
        self.l.reserve(plan, now)
        rid = plan["round_id"]
        try:
            ixs = self.instructions(plan)
            blockhash, last_valid = self.rpc.latest_blockhash()
            tx, signature = build_transaction(self.keypair, ixs, blockhash)
        except Exception:
            self.l.refund(rid)
            raise
        # The signature is durable before the network can see the transaction.
        self.l.mark(rid, "SENT", str(signature))
        try:
            self.rpc.send(tx)
            landed = self.rpc.confirm(signature, last_valid)
        except Unconfirmed:
            self.l.mark(rid, "UNKNOWN")
            self.l.halt("Unknown deploy outcome")
            raise
        except Exception as e:
            status = None
            try:
                status = self.rpc.signature_status(signature)
            except Exception:
                pass
            if status is not None and status["confirmed"] and status["err"] is None:
                self.l.mark(rid, "CONFIRMED")
                return {"status": "CONFIRMED", "round_id": rid, "signature": str(signature)}
            if status is None or status["err"] is not None:
                self.l.refund(rid)
                return {"status": "FAILED", "round_id": rid, "reason": type(e).__name__}
            self.l.mark(rid, "UNKNOWN")
            self.l.halt("Unknown deploy outcome")
            raise
        if not landed:
            self.l.refund(rid)
            return {"status": "FAILED", "round_id": rid, "reason": "blockhash expired"}
        self.l.mark(rid, "CONFIRMED")
        return {"status": "CONFIRMED", "round_id": rid, "signature": str(signature), "tiles": plan["tiles"]}

    def resolve(self, board):
        rows = [r for r in self.l.unsettled() if r["round_id"] < board.round_id]
        if not rows:
            return []
        records = {
            r["round_id"]: r
            for r in self.api.user_deployments(str(self.wallet), max(10, len(rows) + 5))
            if isinstance(r, dict) and isinstance(r.get("round_id"), int)
        }
        price = btc_price(board)
        outcomes = []
        for row in rows:
            record = records.get(row["round_id"])
            if record is None:
                continue
            try:
                detail = self.api.round(row["round_id"])
            except Exception:  # the strike tag is decoration; the settlement is in the record
                detail = None
            outcome = outcome_from_record(record, price, detail)
            if outcome is None:
                continue
            self.l.settle(row["round_id"], outcome)
            outcomes.append({"round_id": row["round_id"], **outcome})
        if outcomes:
            self.l.put("vault_positions", self.vault_positions())
        return outcomes

    def vault_positions(self):
        """This wallet's standing in the epoch and 1 BTC vaults, from the public API.

        Tickets are earned by deploying; the API does not publish the formula,
        so they are read back rather than estimated. Read-only and decorative:
        a failure leaves the last known value in place.
        """
        try:
            epoch = self.api.user_epoch(str(self.wallet)) or []
            one_btc = self.api.user_one_btc(str(self.wallet)) or []
        except Exception:
            return self.l.get("vault_positions")

        def newest(entries):
            entries = [e for e in entries if isinstance(e, dict)]
            return max(entries, key=lambda e: int(e.get("iteration_id") or 0), default=None)

        e = newest(epoch)
        o = newest(one_btc)
        return {
            "epoch": None if e is None else {
                "iteration": e.get("iteration_id"),
                "tickets": int(e.get("tickets") or 0),
                "rank": e.get("rank"),
                "won": bool(e.get("is_won")),
                "won_usd": float(e.get("won_combined_usd_amount") or 0),
            },
            "one_btc": None if o is None else {
                "iteration": o.get("iteration_id"),
                "tickets": int(o.get("tickets") or 0),
                "won": bool(o.get("is_won")),
            },
            "epoch_wins": sum(1 for x in epoch if isinstance(x, dict) and x.get("is_won")),
            "one_btc_wins": sum(1 for x in one_btc if isinstance(x, dict) and x.get("is_won")),
            "fetched_at": time.time(),
        }

    def claim(self, usd=True, sats=True):
        miner = self.miner()
        if miner is None:
            return {"status": "NO_MINER"}
        ixs = []
        if usd and miner["unclaimed_usd"] > 0:
            ixs.append(program.claim_usd(self.wallet, self.usd_mint, miner["unclaimed_usd"]))
        if sats and miner["unclaimed_btc_shares"] > 0:
            ixs.append(
                program.claim_sats(self.wallet, self.btc_mint, self.token_mint, miner["unclaimed_btc_shares"])
            )
        if not ixs:
            return {"status": "NOTHING_TO_CLAIM"}
        blockhash, last_valid = self.rpc.latest_blockhash()
        tx, signature = build_transaction(self.keypair, ixs, blockhash)
        self.rpc.send(tx)
        landed = self.rpc.confirm(signature, last_valid)
        return {"status": "CONFIRMED" if landed else "EXPIRED", "signature": str(signature)}
