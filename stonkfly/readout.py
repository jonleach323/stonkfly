"""Fixed neural readout: 21 pre-registered descending-neuron groups, one per tile.

Every neuron whose annotated type starts with "DN" is sorted by body ID and cut
into 21 contiguous groups. Each group keeps an exponential moving average of
its own firing rate (a stand-in for sensory adaptation), and its relative
excess is (rate - baseline) / (baseline + 1 Hz).

Tiles are chosen one at a time (`step`). Each step the network runs for a
short window while seeing the board with its picks so far. Each group also
keeps a running variance of its rate, so its excess can be read as a
z-score: how many of its own standard deviations above its usual rate it is
firing. The unpicked group with the largest excess picks its tile if it is
firing unusually high, more than Z_STOP (one) standard deviation above its
usual rate; when no unpicked group is, the fly stops. In a steady network
about one group in six is that high at any moment, so the count varies
widely from round to round: often a handful, sometimes one, sometimes all
21 when the whole network surges above its lagging averages. Relative
excess keeps a persistent rate ranking from deciding the tiles round after
round. Until a run has WARMUP steps of history the variance is unknown, and
the rule is simply rate above the median rate.

`decode` is the older whole-window rule (all groups above their usual rate at
once); it is kept for tests and comparison. The mapping is arbitrary, fixed before any round is played, and
logs its cell identities. It is an engineered interface, not a discovery of
"tile neurons". No game state enters the decode.
"""

import numpy as np

from .config import TILES

Z_STOP = 1.0     # a group must fire this many of its own standard deviations above its usual rate to pick
WARMUP = 21      # steps of history before the variance is trusted (one full observation)
SD_FLOOR = 0.5   # Hz; keeps z finite for near-silent groups


class TileReadout:
    def __init__(self, ids, annotation, min_tiles=1, max_tiles=TILES, adaptation=20):
        types = annotation.type.fillna("").astype(str)
        members = np.flatnonzero(types.str.startswith("DN").to_numpy())
        if len(members) < TILES * 2:
            raise RuntimeError("Too few annotated descending neurons for a 21-tile readout")
        order = np.argsort(np.asarray(ids)[members], kind="stable")
        members = members[order]
        self.groups = [g.astype(np.int32) for g in np.array_split(members, TILES)]
        if not 1 <= min_tiles <= max_tiles <= TILES:
            raise ValueError("Invalid tile bounds")
        if not 1 <= adaptation <= 10_000:
            raise ValueError("Adaptation must be 1-10000 observations")
        self.min_tiles, self.max_tiles = min_tiles, max_tiles
        self.adaptation = float(adaptation)
        self.baseline = None
        self.variance = None
        self.steps = 0
        self.identities = {
            str(i + 1): [str(ids[j]) for j in g] for i, g in enumerate(self.groups)
        }
        self.report = {
            "model": "dn-21-group-sequential-z-v5",
            "cells": int(len(members)),
            "group_sizes": [int(len(g)) for g in self.groups],
            "adaptation_observations": adaptation,
            "rule": "one tile per step of step_ms, up to max_tiles steps: excess = (group mean rate over the step - running average of that group's rate) / (running average + 1 Hz), z = (rate - running average) / (running sd + 0.5 Hz); the unpicked group with the largest excess picks its tile if its z > 1 (rate above the median rate during the first 21 steps of a run); otherwise the fly stops; the first min_tiles picks are always made",
            "z_stop": Z_STOP,
            "warmup_steps": WARMUP,
            "validated": False,
        }

    def state(self):
        if self.baseline is None:
            return None
        return {
            "baseline": [float(x) for x in self.baseline],
            "variance": None if self.variance is None else [float(x) for x in self.variance],
            "steps": int(self.steps),
        }

    def load(self, state):
        if state is None:
            self.baseline = None
            self.variance = None
            self.steps = 0
            return
        if isinstance(state, dict):
            baseline, variance, steps = state.get("baseline"), state.get("variance"), int(state.get("steps") or 0)
        else:  # older runs saved the baseline alone
            baseline, variance, steps = state, None, 0
        baseline = np.asarray(baseline, dtype=float)
        if baseline.shape != (TILES,) or not np.isfinite(baseline).all():
            raise ValueError("Invalid readout baseline")
        if variance is not None:
            variance = np.asarray(variance, dtype=float)
            if variance.shape != (TILES,) or not np.isfinite(variance).all() or (variance < 0).any():
                raise ValueError("Invalid readout variance")
        self.baseline = baseline
        self.variance = variance
        self.steps = steps

    def _excess(self, rates):
        if self.baseline is None:
            excess_hz = rates - float(np.median(rates))
            return excess_hz, excess_hz.copy()
        excess_hz = rates - self.baseline
        return excess_hz, excess_hz / (self.baseline + 1.0)

    def _z(self, rates):
        """Standard deviations above each group's usual rate; None until the variance is known."""
        if self.baseline is None or self.variance is None or self.steps < WARMUP:
            return None
        return (rates - self.baseline) / (np.sqrt(self.variance) + SD_FLOOR)

    def _adapt(self, rates):
        if self.baseline is None:
            self.baseline = rates.copy()
            self.variance = np.zeros(TILES)
        else:
            deviation = rates - self.baseline
            if self.variance is None:
                self.variance = np.zeros(TILES)
            self.variance += (deviation * deviation - self.variance) / self.adaptation
            self.baseline += deviation / self.adaptation
        self.steps += 1

    def step(self, counts, seconds, chosen):
        """One pick step. Returns the tile picked (1-21) or None to stop, with the group readings."""
        rates = np.asarray([float(np.mean(counts[g]) / seconds) for g in self.groups], dtype=float)
        excess_hz, excess = self._excess(rates)
        z = self._z(rates)
        taken = {int(t) for t in chosen}
        order = [int(i) for i in np.argsort(-excess, kind="stable") if int(i) + 1 not in taken]
        pick = None
        best = None
        if order and len(taken) < self.max_tiles:
            best = order[0]
            unusually_high = excess[best] > 0 if z is None else z[best] > Z_STOP
            if unusually_high or len(taken) < self.min_tiles:
                pick = best + 1
        self._adapt(rates)
        return {
            "pick": pick,
            "best_excess_rel": None if best is None else round(float(excess[best]), 4),
            "best_z": None if best is None or z is None else round(float(z[best]), 3),
            "warmup": z is None,
            "group_hz": [round(float(r), 3) for r in rates],
            "excess_hz": [round(float(e), 3) for e in excess_hz],
            "excess_rel": [round(float(e), 4) for e in excess],
            "median_excess_rel": round(float(np.median(excess)), 4),
            "median_excess_hz": round(float(np.median(excess_hz)), 3),
        }

    def decode(self, counts, seconds):
        rates = np.asarray(
            [float(np.mean(counts[g]) / seconds) for g in self.groups], dtype=float
        )
        excess_hz, excess = self._excess(rates)
        median = float(np.median(excess))
        order = np.argsort(-excess, kind="stable")
        threshold = median if self.baseline is None else 0.0
        chosen = {int(i) for i in np.flatnonzero(excess > threshold)}
        chosen.add(int(order[0]))
        ranked = [int(i) for i in order if int(i) in chosen]
        if len(ranked) > self.max_tiles:
            ranked = ranked[: self.max_tiles]
        if len(ranked) < self.min_tiles:
            extra = [int(i) for i in order if int(i) not in chosen]
            ranked += extra[: self.min_tiles - len(ranked)]
        tiles = sorted(i + 1 for i in ranked)
        self._adapt(rates)
        return {
            "tiles": tiles,
            "group_hz": [round(float(r), 3) for r in rates],
            "excess_hz": [round(float(e), 3) for e in excess_hz],
            "excess_rel": [round(float(e), 4) for e in excess],
            "median_excess_rel": round(median, 4),
            "median_excess_hz": round(float(np.median(excess_hz)), 3),
        }
