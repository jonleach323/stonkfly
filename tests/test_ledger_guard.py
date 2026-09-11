import pytest

from stonkfly.config import Settings
from stonkfly.guard import Guard, Veto
from stonkfly.ledger import Ledger
from stonkfly.reinforcement import reinforcement
from stonkfly.satrush.api import FixtureApi


@pytest.fixture
def env(tmp_path):
    s = Settings()
    ledger = Ledger(tmp_path / "ledger.sqlite", s, "paper")
    yield s, ledger, Guard(s, ledger, tmp_path / "STOP"), tmp_path
    ledger.close()


def board(now=10.0, period=60.0):
    return FixtureApi(period=period, clock=lambda: now, epoch=0.0).board()


def test_ledger_rejects_mismatched_settings(tmp_path):
    Ledger(tmp_path / "l.sqlite", Settings(), "paper").close()
    with pytest.raises(RuntimeError):
        Ledger(tmp_path / "l.sqlite", Settings(stake="2"), "paper")
    with pytest.raises(RuntimeError):
        Ledger(tmp_path / "l.sqlite", Settings(), "live")


def test_plan_and_veto_paths(env):
    s, l, g, tmp = env
    b = board()
    plan = g.plan(b, [1, 2, 2], "100", "100", now=b.fetched_at)
    assert plan["tiles"] == [1, 2] and plan["mask"] == 3 and plan["amount"] == 1_000_000
    with pytest.raises(Veto):
        g.plan(b, [1], "100", "100", now=b.fetched_at + 100)  # stale board
    with pytest.raises(Veto):
        g.plan(b, [1], "100", "0.5", now=b.fetched_at)  # cannot afford stake
    with pytest.raises(Veto):
        g.plan(b, [], "100", "100", now=b.fetched_at)
    with pytest.raises(Veto):
        g.plan(board(now=59.5), [1], "100", "100", now=59.5)  # closing round
    (tmp / "STOP").touch()
    with pytest.raises(Veto):
        g.check(b, "100", now=b.fetched_at)
    (tmp / "STOP").unlink()
    with pytest.raises(Veto):
        g.check(b, "80", now=b.fetched_at)  # loss stop
    assert l.get("halted") == "Loss stop reached"


def test_one_intent_per_round_and_paper_cash(env):
    s, l, g, tmp = env
    b = board()
    plan = g.plan(b, [5], "100", "100", now=b.fetched_at)
    l.reserve(plan, b.fetched_at, debit=True)
    assert str(l.cash) == "99"
    assert g.playable(b) == "Already deployed this round"
    with pytest.raises(RuntimeError):
        l.reserve({**plan, "round_id": plan["round_id"] + 1}, 1.0)  # open intent exists
    l.mark(plan["round_id"], "PAPER")
    outcome = {"refund_usd": "0.889974", "sats_usd": "0.000000", "won": False}
    l.settle(plan["round_id"], outcome, credit=True)
    l.settle(plan["round_id"], outcome, credit=True)  # idempotent
    assert str(l.cash) == "99.889974"
    with pytest.raises(RuntimeError):
        l.settle(plan["round_id"], {**outcome, "won": True}, credit=True)
    assert l.deploys_today(b.fetched_at) == 1
    with pytest.raises(Veto):
        g.before_submit(plan, b)


def test_refund_only_open_intents(env):
    s, l, g, tmp = env
    b = board()
    plan = g.plan(b, [5], "100", "100", now=b.fetched_at)
    l.reserve(plan, b.fetched_at, debit=True)
    l.refund(plan["round_id"])
    assert str(l.cash) == "100" and l.deployment(plan["round_id"])["status"] == "FAILED"
    with pytest.raises(RuntimeError):
        l.refund(plan["round_id"])
    assert l.deploys_today(b.fetched_at) == 0


@pytest.mark.parametrize(
    "pnl,expected", [("0.03", "reward"), ("-0.03", "aversive"), ("0.001", "none"), ("0", "none")]
)
def test_explicit_feedback(pnl, expected):
    assert reinforcement(pnl, ".01")[0] == expected
