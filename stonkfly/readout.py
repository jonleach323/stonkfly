"""Fixed neural readout: 21 pre-registered descending-neuron groups, one per tile.

Every neuron whose annotated type starts with "DN" is sorted by body ID and cut
into 21 contiguous groups. A tile is selected when its group's mean firing
rate over the observation exceeds the median of the 21 group rates; the most
active group is always selected. The mapping is arbitrary, fixed before any
round is played, and logs its cell identities. It is an engineered interface,
not a discovery of "tile neurons". No game state enters the decode.
"""

import numpy as np

from .config import TILES


class TileReadout:
    def __init__(self, ids, annotation, min_tiles=1, max_tiles=TILES):
        types = annotation.type.fillna("").astype(str)
        members = np.flatnonzero(types.str.startswith("DN").to_numpy())
        if len(members) < TILES * 2:
            raise RuntimeError("Too few annotated descending neurons for a 21-tile readout")
        order = np.argsort(np.asarray(ids)[members], kind="stable")
        members = members[order]
        self.groups = [g.astype(np.int32) for g in np.array_split(members, TILES)]
        if not 1 <= min_tiles <= max_tiles <= TILES:
            raise ValueError("Invalid tile bounds")
        self.min_tiles, self.max_tiles = min_tiles, max_tiles
        self.identities = {
            str(i + 1): [str(ids[j]) for j in g] for i, g in enumerate(self.groups)
        }
        self.report = {
            "model": "dn-21-group-median-v1",
            "cells": int(len(members)),
            "group_sizes": [int(len(g)) for g in self.groups],
            "rule": "tile selected if group mean rate > median of 21 group rates; argmax always selected; then clipped to [min_tiles, max_tiles] by rate rank",
            "validated": False,
        }

    def decode(self, counts, seconds):
        rates = np.asarray(
            [float(np.mean(counts[g]) / seconds) for g in self.groups], dtype=float
        )
        median = float(np.median(rates))
        order = np.argsort(-rates, kind="stable")
        chosen = {int(i) for i in np.flatnonzero(rates > median)}
        chosen.add(int(order[0]))
        ranked = [int(i) for i in order if int(i) in chosen]
        if len(ranked) > self.max_tiles:
            ranked = ranked[: self.max_tiles]
        if len(ranked) < self.min_tiles:
            extra = [int(i) for i in order if int(i) not in chosen]
            ranked += extra[: self.min_tiles - len(ranked)]
        tiles = sorted(i + 1 for i in ranked)
        return {
            "tiles": tiles,
            "group_hz": [round(float(r), 3) for r in rates],
            "median_hz": round(median, 3),
        }
