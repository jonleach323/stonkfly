"""Fixed neural readout: 21 pre-registered descending-neuron groups, one per tile.

Every neuron whose annotated type starts with "DN" is sorted by body ID and cut
into 21 contiguous groups. Each group keeps an exponential moving average of
its own firing rate (a stand-in for sensory adaptation). A tile is selected
when its group's rate exceeds that baseline by more than the median excess
across groups; the group with the largest excess is always selected. On the
first observation the baseline is the observation itself, so the raw median
rule applies. The mapping is arbitrary, fixed before any round is played, and
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
            "model": "dn-21-group-adaptive-median-v2",
            "cells": int(len(members)),
            "group_sizes": [int(len(g)) for g in self.groups],
            "adaptation_observations": adaptation,
            "rule": "excess = group mean rate - exponential moving average of that group's rate; tile selected if excess > median excess; argmax always selected; then clipped to [min_tiles, max_tiles] by excess rank",
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

    def decode(self, counts, seconds):
        rates = np.asarray(
            [float(np.mean(counts[g]) / seconds) for g in self.groups], dtype=float
        )
        if self.baseline is None:
            excess = rates - float(np.median(rates))
        else:
            excess = rates - self.baseline
        median = float(np.median(excess))
        order = np.argsort(-excess, kind="stable")
        chosen = {int(i) for i in np.flatnonzero(excess > median)}
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
            "excess_hz": [round(float(e), 3) for e in excess],
            "median_excess_hz": round(median, 3),
        }
