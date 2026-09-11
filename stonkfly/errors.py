"""Failure classes the run loop treats differently.

Transient: the outside world did not answer (API, RPC, a gateway page). The
loop waits and retries; nothing is halted. Everything else halts the run for
review, because continuing could double-deploy or misaccount money.
"""


class Transient(RuntimeError):
    pass
