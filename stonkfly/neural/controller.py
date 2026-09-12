"""Only RGB and engineered reinforcement enter the network. No game policy."""

import hashlib

import numpy as np

from . import atlas

from ..readout import TileReadout
from .common import annotations
from .visual import VisualMemoryBrain


class FlyController:
    def __init__(self, settings):
        self.s = settings
        self.brain = VisualMemoryBrain()
        self.brain.weights_frozen = not settings.learning
        self.readout = TileReadout(
            self.brain.ids,
            annotations(self.brain.ids),
            settings.min_tiles,
            settings.max_tiles,
        )
        xyz, superclass, side, types = atlas.positions(self.brain.ids)
        self.atlas = atlas.build(
            self.brain.ids, xyz, superclass, side, self.brain.circuit, self.readout.groups, self.brain.retina, self.brain.uv, types=types
        )

    def write_atlas(self, out):
        atlas.write(self.atlas, out)

    def observe(self, rgb, reinforcement):
        if reinforcement not in ("none", "reward", "aversive"):
            raise ValueError("Unknown reinforcement")
        b = self.brain
        counts = np.zeros(b.n, dtype=np.int32)
        # Spikes of the atlas subsample in BINS slices of neural time, for the page's replay.
        watched = self.atlas["index"]
        activity = np.zeros((atlas.BINS, len(watched)), dtype=np.uint16)
        elapsed_ms = 0.0
        wall = 0.0
        remaining = round(self.s.neural_ms / b.dt)
        pulse = round(self.s.pulse_ms / b.dt) if reinforcement != "none" else 0
        delivered = 0
        while remaining:
            n = min(remaining, round(self.s.neural_bin_ms / b.dt))
            if pulse:
                n = min(n, pulse)
            stimulus = (
                (b.circuit[reinforcement], self.s.pulse_current) if pulse else None
            )
            c, elapsed = b.rgb_step(
                rgb, n * b.dt, learning=self.s.learning, stimulation=stimulus
            )
            counts += c
            activity[min(atlas.BINS - 1, int(elapsed_ms * atlas.BINS / self.s.neural_ms))] += c[watched].astype(np.uint16)
            elapsed_ms += n * b.dt
            wall += elapsed
            remaining -= n
            if pulse:
                delivered += n
                pulse -= n
        b.counts[:] = counts
        return {
            **self.readout.decode(counts, self.s.neural_ms / 1000),
            "brain_ms": b.sim_ms,
            "compute_seconds": wall,
            "stimulus": reinforcement,
            "stimulus_ms": delivered * b.dt,
            "reward_spikes": int(counts[b.circuit["reward"]].sum()),
            "aversive_spikes": int(counts[b.circuit["aversive"]].sum()),
            "KC_spikes": int(counts[b.circuit["kc"]].sum()),
            "total_spikes": int(counts.sum()),
            "spike_sha256": hashlib.sha256(counts.tobytes()).hexdigest(),
            "input_sha256": hashlib.sha256(np.asarray(rgb).tobytes()).hexdigest(),
            "memory": b.memory(),
            "activity": activity,
        }

    def save(self, path):
        self.brain.checkpoint(path)

    def restore(self, path):
        self.brain.restore(path)


class StubController:
    """Seeded random spike counts standing in for the connectome. PAPER ONLY.

    Exists so the game loop can be exercised without the 1 GB dataset. It is
    never neural output and the CLI refuses it outside paper/fixture runs.
    """

    def __init__(self, settings, seed=0):
        import pandas as pd

        self.s = settings
        self.rng = np.random.default_rng(seed)
        self.n = 210
        ids = np.arange(1, self.n + 1)
        annotation = pd.DataFrame({"type": ["DN%03d" % (i % 50) for i in range(self.n)]})
        self.readout = TileReadout(ids, annotation, settings.min_tiles, settings.max_tiles)
        self.sim_ms = 0.0
        self.atlas = atlas.stub(self.n, seed)

    def write_atlas(self, out):
        atlas.write(self.atlas, out)

    def observe(self, rgb, reinforcement):
        if reinforcement not in ("none", "reward", "aversive"):
            raise ValueError("Unknown reinforcement")
        bins = self.rng.poisson(0.2, (atlas.BINS, self.n)).astype(np.int32)
        counts = bins.sum(axis=0).astype(np.int32)
        self.sim_ms += self.s.neural_ms
        return {
            **self.readout.decode(counts, self.s.neural_ms / 1000),
            "brain_ms": self.sim_ms,
            "compute_seconds": 0.0,
            "stimulus": reinforcement,
            "stimulus_ms": self.s.pulse_ms if reinforcement != "none" else 0.0,
            "reward_spikes": 0,
            "aversive_spikes": 0,
            "KC_spikes": 0,
            "total_spikes": int(counts.sum()),
            "spike_sha256": hashlib.sha256(counts.tobytes()).hexdigest(),
            "input_sha256": hashlib.sha256(np.asarray(rgb).tobytes()).hexdigest(),
            "memory": {"plastic_edges": 0, "changed_edges": 0, "model": "stub"},
            "stub": True,
            "activity": bins,
        }

    def save(self, path):
        np.savez(path, seed_state=np.asarray([0]))

    def restore(self, path):
        pass
