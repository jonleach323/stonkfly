"""Minimal Solana JSON-RPC access and one-transaction-at-a-time submission."""

import base64
import json
import time
import urllib.request

from solders.hash import Hash
from solders.message import MessageV0
from solders.signature import Signature
from solders.transaction import VersionedTransaction

USER_AGENT = "stonkfly/0.2 (+https://github.com/jonleach323/stonkfly)"


from ..errors import Transient


class RpcError(Transient):
    """An RPC call failed or was refused. Inside a deploy it is handled there; elsewhere it is retried."""


class Unconfirmed(RuntimeError):
    """The transaction outcome is unknown; never resend before reconciling."""


def http_rpc(url, payload, timeout=30):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class Rpc:
    def __init__(self, url, post=http_rpc, sleep=time.sleep):
        self.url = url
        self.post = post
        self.sleep = sleep

    def call(self, method, params=None):
        reply = self.post(
            self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        )
        if not isinstance(reply, dict) or "result" not in reply:
            raise RpcError(f"{method}: {reply.get('error') if isinstance(reply, dict) else reply}")
        return reply["result"]

    def slot(self):
        return int(self.call("getSlot", [{"commitment": "confirmed"}]))

    def block_height(self):
        return int(self.call("getBlockHeight", [{"commitment": "confirmed"}]))

    def latest_blockhash(self):
        value = self.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        return Hash.from_string(value["blockhash"]), int(value["lastValidBlockHeight"])

    def account(self, pubkey):
        value = self.call(
            "getAccountInfo", [str(pubkey), {"encoding": "base64", "commitment": "confirmed"}]
        )["value"]
        if value is None:
            return None
        return base64.b64decode(value["data"][0])

    def lamports(self, pubkey):
        return int(self.call("getBalance", [str(pubkey), {"commitment": "confirmed"}])["value"])

    def token_amount(self, ata):
        data = self.account(ata)
        if data is None:
            return 0
        from .program import decode_token_amount

        return decode_token_amount(data)

    def signature_status(self, signature):
        value = self.call("getSignatureStatuses", [[str(signature)], {"searchTransactionHistory": True}])
        status = value["value"][0]
        if status is None:
            return None
        return {
            "confirmed": status.get("confirmationStatus") in ("confirmed", "finalized"),
            "err": status.get("err"),
        }

    def send(self, tx_bytes):
        encoded = base64.b64encode(tx_bytes).decode()
        return self.call(
            "sendTransaction",
            [encoded, {"encoding": "base64", "preflightCommitment": "confirmed", "maxRetries": 3}],
        )

    def confirm(self, signature, last_valid_block_height, timeout=90.0, clock=time.monotonic):
        start = clock()
        while True:
            status = self.signature_status(signature)
            if status is not None and status["err"] is not None:
                raise RpcError(f"Transaction failed on chain: {status['err']}")
            if status is not None and status["confirmed"]:
                return True
            if self.block_height() > last_valid_block_height:
                status = self.signature_status(signature)
                if status is not None and status["err"] is not None:
                    raise RpcError(f"Transaction failed on chain: {status['err']}")
                if status is not None and status["confirmed"]:
                    return True
                return False
            if clock() - start > timeout:
                raise Unconfirmed(str(signature))
            self.sleep(1.0)


def build_transaction(payer, instructions, blockhash):
    message = MessageV0.try_compile(payer.pubkey(), instructions, [], blockhash)
    tx = VersionedTransaction(message, [payer])
    return bytes(tx), Signature.from_bytes(bytes(tx.signatures[0]))
