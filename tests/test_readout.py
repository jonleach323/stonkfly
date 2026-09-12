import numpy as np
import pandas as pd
import pytest

from stonkfly.config import Settings
from stonkfly.display import board_frame, tile_box
from stonkfly.readout import TileReadout
from stonkfly.satrush.api import FixtureApi


def readout(**kw):
    ids = np.arange(100, 100 + 63)
    return TileReadout(ids, pd.DataFrame({"type": ["DNx"] * 63}), **kw), ids


def test_median_rule_and_bounds():
    r, ids = readout()
    assert [len(g) for g in r.groups] == [3] * 21
    counts = np.zeros(63, dtype=np.int32)
    counts[0:3] = 10  # group 1
    counts[60:63] = 4  # group 21
    out = r.decode(counts, 0.5)
    assert out["tiles"] == [1, 21] and out["group_hz"][0] == 20.0
    r, _ = readout()
    assert r.decode(np.zeros(63, dtype=np.int32), 0.5)["tiles"] == [1]  # argmax always
    r2, _ = readout(min_tiles=3, max_tiles=5)
    assert r2.decode(np.zeros(63, dtype=np.int32), 0.5)["tiles"] == [1, 2, 3]
    counts = np.arange(63, dtype=np.int32)
    assert len(r2.decode(counts, 0.5)["tiles"]) == 5
    assert r.identities["1"] == ["100", "101", "102"]


def test_adaptation_removes_a_constant_bias():
    r, _ = readout(adaptation=2)
    biased = np.zeros(63, dtype=np.int32)
    biased[0:3] = 10  # group 1 always loud
    first = r.decode(biased, 0.5)
    assert first["tiles"] == [1]
    for _ in range(5):
        r.decode(biased, 0.5)
    biased[30:33] = 1  # group 11 becomes slightly more active than usual
    out = r.decode(biased, 0.5)
    assert out["tiles"] == [11] and out["excess_hz"][0] == pytest.approx(0, abs=1e-6)
    assert out["excess_rel"][10] > out["median_excess_rel"]


def test_relative_excess_ignores_a_global_ramp():
    r, _ = readout(adaptation=2)
    base = np.arange(1, 64, dtype=np.int32)  # groups differ in rate a lot
    for _ in range(4):
        r.decode(base, 0.5)
    doubled = r.decode(base * 2, 0.5)  # every group doubles: a network-wide regime shift
    # Every group's relative excess is (2b - EMA) / (EMA + 1); with equal history the
    # spread should not simply reproduce the raw ranking [21, 20, 19, ...].
    assert doubled["excess_rel"][20] < 2 * doubled["excess_rel"][0] + 1
    state = r.state()
    fresh, _ = readout(adaptation=2)
    fresh.load(state)
    assert np.allclose(fresh.baseline, r.baseline)
    with pytest.raises(ValueError):
        fresh.load([1.0])


def test_readout_needs_descending_neurons():
    with pytest.raises(RuntimeError):
        TileReadout(np.arange(5), pd.DataFrame({"type": ["DN"] * 5}))


def test_frame_geometry_and_content():
    board = FixtureApi(period=60, clock=lambda: 0.0).board()
    frame = board_frame(board, now=0.0)
    assert frame.shape == (180, 320, 3) and frame.dtype == np.uint8
    x0, y0, x1, y1 = tile_box(20)
    assert x1 < 320 and y1 < 156
    # Tiles are drawn over the light background.
    assert frame[y0 + 10, x0 + 30].tolist() != [238, 241, 247]
    later = board_frame(board, now=30.0)
    assert not np.array_equal(frame, later)  # countdown bar moved


@pytest.mark.parametrize(
    "changes",
    [
        dict(capital="101"),
        dict(stake="0.5"),
        dict(stake="11"),
        dict(stake="1.0000001"),
        dict(loss_stop="0"),
        dict(daily_deploys=2000),
        dict(min_tiles=0),
        dict(max_tiles=22),
        dict(network="testnet"),
        dict(priority_fee_microlamports=-1),
        dict(neural_bin_ms=11),
    ],
)
def test_settings_bounds(changes):
    with pytest.raises(ValueError):
        Settings(**changes)


def test_count_is_a_neural_quantity_from_one_tile_to_all_21():
    r, _ = readout(adaptation=2)
    base = np.full(63, 4, dtype=np.int32)
    for _ in range(4):
        r.decode(base, 0.5)
    assert r.decode(base * 3, 0.5)["tiles"] == list(range(1, 22))  # every group above its own average
    r2, _ = readout(adaptation=2)
    for _ in range(4):
        r2.decode(base * 3, 0.5)
    assert r2.decode(base, 0.5)["tiles"] == [1]  # every group below: only the argmax stays


def test_one_tile_per_step_until_nothing_is_above_its_usual_rate():
    r, _ = readout(adaptation=1000)  # a slow baseline, so the steps below read against a fixed average
    base = np.full(63, 4, dtype=np.int32)
    r.decode(base, 0.5)  # sets the baseline: every group at 8 Hz
    chosen = []
    loud = base.copy()
    loud[0:3] = 20   # group 1
    loud[30:33] = 12  # group 11
    first = r.step(loud, 0.5, chosen)
    assert first["pick"] == 1 and first["best_excess_rel"] > 0
    chosen.append(first["pick"])
    second = r.step(loud, 0.5, chosen)
    assert second["pick"] == 11  # the loudest unpicked group
    chosen.append(second["pick"])
    third = r.step(loud, 0.5, chosen)
    assert third["pick"] is None  # nothing left above its usual rate: stop
    # With every group above its average, picks continue until all 21 are taken.
    r2, _ = readout(adaptation=1000)
    r2.decode(base, 0.5)
    taken = []
    for _ in range(25):
        out = r2.step(base * 3, 0.5, taken)
        if out["pick"] is None:
            break
        taken.append(out["pick"])
    assert sorted(taken) == list(range(1, 22)) and out["pick"] is None
    # The first pick is always made, even from a quiet network.
    r3, _ = readout(adaptation=1000)
    r3.decode(base * 3, 0.5)
    assert r3.step(base, 0.5, [])["pick"] is not None


def test_frame_marks_the_picks_so_far():
    board = FixtureApi(period=60, clock=lambda: 0.0).board()
    plain = board_frame(board, now=0.0)
    marked = board_frame(board, now=0.0, picks=[5, 21])
    x0, y0, x1, y1 = tile_box(4)
    assert not np.array_equal(plain[y0:y1, x0:x1], marked[y0:y1, x0:x1])
    x0, y0, x1, y1 = tile_box(0)
    assert np.array_equal(plain[y0:y1, x0:x1], marked[y0:y1, x0:x1])


def test_stub_controller_picks_sequentially():
    from stonkfly.neural.controller import StubController

    c = StubController(Settings(neural_ms=400, step_ms=40))
    frames = []
    out = c.observe(lambda picks: frames.append(list(picks)) or np.zeros((180, 320, 3), np.uint8), "none")
    assert 1 <= len(out["tiles"]) <= 10 and len(set(out["tiles"])) == len(out["tiles"])
    assert out["steps"][-1]["tile"] is None or len(out["tiles"]) == 10
    assert out["stop_reason"] in ("no unpicked group above its usual rate", "step budget", "tile limit", "all 21 tiles")
    assert frames[0] == [] and all(frames[i] == out["tiles"][:i] for i in range(len(frames)))
    assert out["neural_ms_used"] == 40 * len(out["steps"])
