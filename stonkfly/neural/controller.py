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
        """One observation: the network picks tiles one at a time until it stops.

        `rgb` is a frame, or a callable `render(picks)` that draws the board
        with the tiles picked so far, so the network sees its own selection
        grow. A reinforcement pulse, if any, runs through the first steps and
        finishes even if the picking stops early.
        """
        if reinforcement not in ("none", "reward", "aversive"):
            raise ValueError("Unknown reinforcement")
        render = rgb if callable(rgb) else (lambda picks: rgb)
        b = self.brain
        s = self.s
        counts = np.zeros(b.n, dtype=np.int32)
        # Spikes of the atlas subsample in BINS slices of the neural time budget, for the page's replay.
        watched = self.atlas["index"]
        activity = np.zeros((atlas.BINS, len(watched)), dtype=np.uint16)
        elapsed_ms = 0.0
        wall = 0.0
        pulse = round(s.pulse_ms / b.dt) if reinforcement != "none" else 0
        delivered = 0
        chosen = []
        steps = []
        stop_reason = None
        last = None
        frame = None

        def run(ms, frame):
            nonlocal elapsed_ms, wall, pulse, delivered
            step_counts = np.zeros(b.n, dtype=np.int32)
            remaining = round(ms / b.dt)
            while remaining:
                n = min(remaining, round(s.neural_bin_ms / b.dt))
                if pulse:
                    n = min(n, pulse)
                stimulus = (b.circuit[reinforcement], s.pulse_current) if pulse else None
                c, took = b.rgb_step(frame, n * b.dt, learning=s.learning, stimulation=stimulus)
                step_counts += c
                activity[min(atlas.BINS - 1, int(elapsed_ms * atlas.BINS / s.neural_ms))] += c[watched].astype(np.uint16)
                elapsed_ms += n * b.dt
                wall += took
                remaining -= n
                if pulse:
                    delivered += n
                    pulse -= n
            return step_counts

        for _ in range(s.max_steps):
            frame = render(chosen)
            step_counts = run(s.step_ms, frame)
            counts += step_counts
            last = self.readout.step(step_counts, s.step_ms / 1000, chosen)
            steps.append({"tile": last["pick"], "excess_rel": last["best_excess_rel"], "ms": round(elapsed_ms, 1)})
            if last["pick"] is None:
                stop_reason = "no unpicked group above its usual rate"
                break
            chosen.append(last["pick"])
            if len(chosen) >= s.max_tiles:
                stop_reason = "all 21 tiles" if s.max_tiles >= 21 else "tile limit"
                break
        else:
            stop_reason = "step budget"
        # The pulse is a stimulus of fixed length: let it finish with the picks frozen.
        while pulse:
            counts += run(min(s.step_ms, pulse * b.dt), frame)
        b.counts[:] = counts
        return {
            "tiles": list(chosen),
            "steps": steps,
            "stop_reason": stop_reason,
            "group_hz": last["group_hz"],
            "excess_hz": last["excess_hz"],
            "excess_rel": last["excess_rel"],
            "median_excess_rel": last["median_excess_rel"],
            "median_excess_hz": last["median_excess_hz"],
            "neural_ms_used": round(elapsed_ms, 1),
            "brain_ms": b.sim_ms,
            "compute_seconds": wall,
            "stimulus": reinforcement,
            "stimulus_ms": delivered * b.dt,
            "reward_spikes": int(counts[b.circuit["reward"]].sum()),
            "aversive_spikes": int(counts[b.circuit["aversive"]].sum()),
            "KC_spikes": int(counts[b.circuit["kc"]].sum()),
            "total_spikes": int(counts.sum()),
            "spike_sha256": hashlib.sha256(counts.tobytes()).hexdigest(),
            "input_sha256": hashlib.sha256(np.asarray(frame).tobytes()).hexdigest(),
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
        render = rgb if callable(rgb) else (lambda picks: rgb)
        s = self.s
        bins = np.zeros((atlas.BINS, self.n), dtype=np.int32)
        counts = np.zeros(self.n, dtype=np.int32)
        chosen = []
        steps = []
        elapsed = 0.0
        last = None
        frame = None
        stop_reason = "step budget"
        for _ in range(s.max_steps):
            frame = render(chosen)
            step_counts = self.rng.poisson(0.25, self.n).astype(np.int32)
            bins[min(atlas.BINS - 1, int(elapsed * atlas.BINS / s.neural_ms))] += step_counts
            counts += step_counts
            elapsed += s.step_ms
            last = self.readout.step(step_counts, s.step_ms / 1000, chosen)
            steps.append({"tile": last["pick"], "excess_rel": last["best_excess_rel"], "ms": round(elapsed, 1)})
            if last["pick"] is None:
                stop_reason = "no unpicked group above its usual rate"
                break
            chosen.append(last["pick"])
            if len(chosen) >= s.max_tiles:
                stop_reason = "all 21 tiles" if s.max_tiles >= 21 else "tile limit"
                break
        self.sim_ms += elapsed
        return {
            "tiles": list(chosen),
            "steps": steps,
            "stop_reason": stop_reason,
            "group_hz": last["group_hz"],
            "excess_hz": last["excess_hz"],
            "excess_rel": last["excess_rel"],
            "median_excess_rel": last["median_excess_rel"],
            "median_excess_hz": last["median_excess_hz"],
            "neural_ms_used": round(elapsed, 1),
            "brain_ms": self.sim_ms,
            "compute_seconds": 0.0,
            "stimulus": reinforcement,
            "stimulus_ms": s.pulse_ms if reinforcement != "none" else 0.0,
            "reward_spikes": 0,
            "aversive_spikes": 0,
            "KC_spikes": 0,
            "total_spikes": int(counts.sum()),
            "spike_sha256": hashlib.sha256(counts.tobytes()).hexdigest(),
            "input_sha256": hashlib.sha256(np.asarray(frame).tobytes()).hexdigest(),
            "memory": {"plastic_edges": 0, "changed_edges": 0, "model": "stub"},
            "stub": True,
            "activity": bins,
        }

    def save(self, path):
        np.savez(path, seed_state=np.asarray([0]))

    def restore(self, path):
        pass
