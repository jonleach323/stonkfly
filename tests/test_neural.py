import json
import os
from pathlib import Path

import numpy as np
import pytest

from stonkfly.config import Settings
from stonkfly.neural.rule import advance


def trace_protocol(order, frozen=False):
    k = np.zeros(2)
    d = np.zeros(1)
    u = np.zeros(2)
    w = np.zeros(2)
    gain = np.ones((1, 2))
    for phase in order:
        for _ in range(20):
            kh = np.array([20.0, 0.0]) if phase == "cue" else np.zeros(2)
            dh = np.array([30.0]) if phase == "reinforce" else np.zeros(1)
            advance(k, d, u, w, kh, dh, gain, 0.01, 0.001, frozen=frozen)
    return w


def test_memory_rule_temporal_specificity():
    paired = trace_protocol(["cue", "reinforce"])
    reverse = trace_protocol(["reinforce", "cue"])
    assert paired[0] < 0 and reverse[0] > 0
    assert paired[1] == 0 and reverse[1] == 0  # Unactivated input is unchanged.
    assert np.array_equal(trace_protocol(["cue", "reinforce"], True), np.zeros(2))


@pytest.mark.skipif(
    os.environ.get("STONKFLY_FULL_TEST") != "1",
    reason="Downloads/uses full MaleCNS; explicit integration test",
)
def test_full_graph_sensory_reinforcement_checkpoint(tmp_path):
    from stonkfly.data import verify
    from stonkfly.display import board_frame
    from stonkfly.neural.controller import FlyController
    from stonkfly.satrush.api import parse_board

    assert verify()["neurons"] == 166700
    c = FlyController(Settings())
    assert len(c.brain.post) == 25582938 and len(c.brain.circuit["edges"]) == 7835
    assert len(c.brain.retina) == 3335 and len(c.brain.r8) == 811
    assert sum(c.readout.report["group_sizes"]) == c.readout.report["cells"]
    # A real public board: uniform synthetic boards barely activate KCs.
    board = parse_board(json.loads((Path(__file__).parent / "fixtures/board.json").read_text())["data"], fetched_at=0.0)
    frame = board_frame(board, now=0.0)
    for _ in range(3):
        out = c.observe(frame, "none")
    assert 1 <= len(out["tiles"]) <= 21
    c.save(tmp_path / "before.npz")
    before = c.brain.weight[c.brain.circuit["edges"]].copy()
    reward = c.observe(frame, "reward")
    assert reward["reward_spikes"] > 0 and reward["stimulus_ms"] == 200
    assert reward["KC_spikes"] > 0 and reward["memory"]["changed_edges"] > 0
    reward_weights = c.brain.weight[c.brain.circuit["edges"]].copy()
    c.restore(tmp_path / "before.npz")
    c.observe(frame, "none")
    assert not np.array_equal(reward_weights, c.brain.weight[c.brain.circuit["edges"]])
    c.restore(tmp_path / "before.npz")
    c.brain.weights_frozen = True
    c.observe(frame, "reward")
    assert np.array_equal(before, c.brain.weight[c.brain.circuit["edges"]])
    c.restore(tmp_path / "before.npz")
    loss = c.observe(frame, "aversive")
    assert loss["aversive_spikes"] > 0 and loss["stimulus_ms"] == 200
    assert np.isfinite(c.brain.weight).all()
