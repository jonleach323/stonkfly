"""Fixed neural readout: 21 pre-registered descending-neuron groups, one per tile.

Every neuron whose annotated type starts with "DN" is sorted by body ID and cut
into 21 contiguous groups. Each group keeps an exponential moving average of
its own firing rate (a stand-in for sensory adaptation), and its relative
excess is (rate - baseline) / (baseline + 1 Hz).

Tiles are chosen one at a time (`step`). Each step the network runs for a
short window while seeing the board with its picks so far; the unpicked
group with the largest excess picks its tile if that excess is positive,
that is if the group fires above its own usual rate. When no unpicked group
is above its usual rate the fly stops. Nothing else caps the count: a quiet
network stops after one tile, a network-wide surge above the lagging
baselines can go on to all 21. Relative excess keeps a persistent rate
ranking from deciding the tiles round after round. On the first step of a
run there is no baseline yet, so excess is the rate minus the median rate.

`decode` is the older whole-window rule (all groups above their usual rate at
once); it is kept for tests and comparison. The mapping is arbitrary, fixed before any round is played, and
logs its cell identities. It is an engineered interface, not a discovery of
"tile neurons". No game state enters the decode.
"""

import numpy as np

from .config import TILES


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
        self.identities = {
            str(i + 1): [str(ids[j]) for j in g] for i, g in enumerate(self.groups)
        }
        self.report = {
            "model": "dn-21-group-sequential-v5",
            "cells": int(len(members)),
            "group_sizes": [int(len(g)) for g in self.groups],
            "adaptation_observations": adaptation,
            "rule": "one tile per step of step_ms, up to max_tiles steps: excess = (group mean rate over the step - running average of that group's rate) / (running average + 1 Hz); the unpicked group with the largest excess picks its tile if excess > 0 (rate above the median rate before any baseline exists); otherwise the fly stops; the first min_tiles picks are always made",
            "validated": False,
        }

    def state(self):
        return None if self.baseline is None else [float(x) for x in self.baseline]

    def load(self, state):
        if state is None:
            self.baseline = None
            return
        baseline = np.asarray(state, dtype=float)
        if baseline.shape != (TILES,) or not np.isfinite(baseline).all():
            raise ValueError("Invalid readout baseline")
        self.baseline = baseline

    def _excess(self, rates):
        if self.baseline is None:
            excess_hz = rates - float(np.median(rates))
            return excess_hz, excess_hz.copy()
        excess_hz = rates - self.baseline
        return excess_hz, excess_hz / (self.baseline + 1.0)

    def _adapt(self, rates):
        if self.baseline is None:
            self.baseline = rates.copy()
        else:
            self.baseline += (rates - self.baseline) / self.adaptation

    def step(self, counts, seconds, chosen):
        """One pick step. Returns the tile picked (1-21) or None to stop, with the group readings."""
        rates = np.asarray([float(np.mean(counts[g]) / seconds) for g in self.groups], dtype=float)
        excess_hz, excess = self._excess(rates)
        taken = {int(t) for t in chosen}
        order = [int(i) for i in np.argsort(-excess, kind="stable") if int(i) + 1 not in taken]
        pick = None
        best = None
        if order and len(taken) < self.max_tiles:
            best = order[0]
            if excess[best] > 0 or len(taken) < self.min_tiles:
                pick = best + 1
        self._adapt(rates)
        return {
            "pick": pick,
            "best_excess_rel": None if best is None else round(float(excess[best]), 4),
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
        if self.baseline is None:
            excess_hz = rates - float(np.median(rates))
            excess = excess_hz.copy()
        else:
            excess_hz = rates - self.baseline
            excess = excess_hz / (self.baseline + 1.0)
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
        if self.baseline is None:
            self.baseline = rates.copy()
        else:
            self.baseline += (rates - self.baseline) / self.adaptation
        return {
            "tiles": tiles,
            "group_hz": [round(float(r), 3) for r in rates],
            "excess_hz": [round(float(e), 3) for e in excess_hz],
            "excess_rel": [round(float(e), 4) for e in excess],
            "median_excess_rel": round(median, 4),
            "median_excess_hz": round(float(np.median(excess_hz)), 3),
        }
