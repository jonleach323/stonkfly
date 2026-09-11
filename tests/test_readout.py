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
    assert r.decode(np.zeros(63, dtype=np.int32), 0.5)["tiles"] == [1]  # argmax always
    r2, _ = readout(min_tiles=3, max_tiles=5)
    assert r2.decode(np.zeros(63, dtype=np.int32), 0.5)["tiles"] == [1, 2, 3]
    counts = np.arange(63, dtype=np.int32)
    assert len(r2.decode(counts, 0.5)["tiles"]) == 5
    assert r.identities["1"] == ["100", "101", "102"]


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
