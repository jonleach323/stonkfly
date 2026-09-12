"""A fixed subsample of neurons with positions, for the page's neural activity view.

The page draws the central nervous system as a point cloud that lights up
with the spikes of each observation. Drawing all 166,700 cells every round
would be a megabyte a minute, so a fixed subsample of about 16,000 is chosen
once per run with a seeded RNG: every readout descending neuron (the raster
needs them) and a uniform random sample of every other cell that has an
annotated soma, so the silhouette of the brain, its optic lobes and the
nerve cord reads as it does in the dataset. Positions are the annotated soma
locations (or the nearest point to the soma). Per observation the spikes of
these cells are binned into BINS slices of neural time.

This is a picture of simulated activity in an approximate model, not a
recording of a fly. Nothing here feeds back into the game.
"""

import json
import struct

import numpy as np

CLASSES = ["other", "optic lobe", "central brain", "nerve cord", "retina", "KC", "MBON", "dopamine", "DN"]
BINS = 10
TARGET = 16_000
ATLAS_MAGIC = b"SFAT"
ACTIVITY_MAGIC = b"SFAC"
VERSION = 1


def _class_of(superclass):
    s = str(superclass or "")
    if s.startswith("ol_") or s.startswith("visual_"):
        return 1
    if s.startswith("cb_"):
        return 2
    if s.startswith("vnc_") or s in ("ascending_neuron", "sensory_ascending"):
        return 3
    return 0


def positions(ids):
    """(n, 3) float positions for these body IDs, NaN where the dataset has none, plus superclass and side."""
    import pyarrow.feather as f

    from ..data import DATA

    table = f.read_table(
        DATA / "annotations.feather", columns=["bodyId", "type", "superclass", "somaSide", "somaLocation", "tosomaLocation"]
    ).to_pandas().set_index("bodyId").reindex(ids)
    xyz = np.full((len(ids), 3), np.nan, dtype=np.float64)
    for column in ("tosomaLocation", "somaLocation"):  # soma wins when both exist
        col = table[column].to_numpy()
        for i, v in enumerate(col):
            if isinstance(v, np.ndarray) and v.shape == (3,) and np.isfinite(v).all():
                xyz[i] = v
    return (
        xyz,
        table["superclass"].fillna("").astype(str).to_numpy(),
        table["somaSide"].fillna("").astype(str).to_numpy(),
        table["type"].fillna("").astype(str).to_numpy(),
    )


def build(ids, xyz, superclass, side, circuit, readout_groups, retina, uv, target=TARGET, seed=0, types=None):
    """Choose the subsample and lay it out. Returns the atlas dict (arrays and metadata)."""
    n = len(ids)
    xyz = np.array(xyz, dtype=np.float64)
    cls = np.array([_class_of(s) for s in superclass], dtype=np.uint8)
    # Cell classes by annotated type: every MBON and every PAM/PPL dopamine cell,
    # not only the few the plasticity circuit uses.
    named_mbon = np.array([str(t).startswith("MBON") for t in (types if types is not None else [""] * n)], dtype=bool)
    named_dan = np.array([str(t).startswith(("PAM", "PPL")) for t in (types if types is not None else [""] * n)], dtype=bool)
    group = np.zeros(n, dtype=np.uint8)
    for k, members in enumerate(readout_groups):
        group[np.asarray(members, dtype=np.int64)] = k + 1
    cls[group > 0] = 8
    kc = np.asarray(circuit.get("kc", []), dtype=np.int64)
    dan = np.asarray(circuit.get("dan", []), dtype=np.int64)
    mb = np.union1d(np.asarray(circuit.get("mb", []), dtype=np.int64), np.flatnonzero(named_mbon))
    dan = np.union1d(dan, np.flatnonzero(named_dan))
    cls[mb] = 6
    cls[kc] = 5
    cls[dan] = 7

    retina = np.asarray(retina, dtype=np.int64)
    cls[retina] = 4
    finite = np.isfinite(xyz).all(axis=1)

    required = np.flatnonzero(group > 0)  # the readout cells, for the raster; everything else is a uniform sample
    required = required[finite[required]]
    rng = np.random.default_rng(seed)
    rest = np.setdiff1d(np.flatnonzero(finite), required)
    fill = max(0, min(len(rest), int(target) - len(required)))
    chosen = np.sort(np.r_[required, rng.choice(rest, size=fill, replace=False)]) if fill else np.sort(required)
    chosen = chosen.astype(np.int32)

    points = xyz[chosen]
    lo = points.min(axis=0)
    hi = points.max(axis=0)
    mid = (lo + hi) / 2
    scale = 30_000.0 / max(float((hi - lo).max()) / 2, 1.0)
    packed = np.clip(np.rint((points - mid) * scale), -32_000, 32_000).astype(np.int16)
    counts = np.bincount(cls[chosen], minlength=len(CLASSES))
    return {
        "index": chosen,
        "xyz": packed,
        "cls": cls[chosen],
        "group": group[chosen],
        "meta": {
            "n": int(len(chosen)),
            "of": int(n),
            "classes": CLASSES,
            "class_counts": {CLASSES[i]: int(c) for i, c in enumerate(counts)},
            "bins": BINS,
            "seed": int(seed),
            "positions": "annotated soma locations (MaleCNS v1.0), scaled to a 60,000-unit cube; cells without a soma location (photoreceptors among them) are not drawn",
            "note": "Simulated activity of an approximate model on a fixed subsample; not a recording of a fly.",
        },
    }


def stub(n, seed=0):
    """A small random atlas for the stub brain (paper only), so the page still draws something."""
    rng = np.random.default_rng(seed)
    index = np.arange(n, dtype=np.int32)
    xyz = np.clip(rng.normal(0, 12_000, size=(n, 3)), -32_000, 32_000).astype(np.int16)
    cls = rng.integers(0, len(CLASSES), size=n).astype(np.uint8)
    group = np.zeros(n, dtype=np.uint8)
    group[: min(n, 21)] = np.arange(1, min(n, 21) + 1)
    return {
        "index": index, "xyz": xyz, "cls": cls, "group": group,
        "meta": {"n": int(n), "of": int(n), "classes": CLASSES, "class_counts": {}, "bins": BINS, "seed": int(seed),
                 "positions": "random (stub brain)", "note": "Stub brain: seeded noise, not neural output."},
    }


def encode_atlas(atlas):
    n = len(atlas["index"])
    return ATLAS_MAGIC + struct.pack("<II", VERSION, n) + atlas["xyz"].astype("<i2").tobytes() + atlas["cls"].tobytes() + atlas["group"].tobytes()


def write(atlas, out):
    """atlas.bin and atlas.json in the run directory; the run keeps the same atlas for its whole life."""
    (out / "atlas.bin").write_bytes(encode_atlas(atlas))
    (out / "atlas.json").write_text(json.dumps(atlas["meta"], indent=2) + "\n")


def rebin(chunks, used_ms, n):
    """Fold (start_ms, counts) chunks into BINS equal slices of the time actually used."""
    bins = np.zeros((BINS, n), dtype=np.uint16)
    span = max(float(used_ms), 1e-9)
    for start_ms, counts in chunks:
        bins[min(BINS - 1, int(start_ms * BINS / span))] += counts
    return bins


def encode_activity(tick, bins, bin_ms):
    """bins: (BINS, n) spike counts per slice; stored as uint8, clipped."""
    bins = np.asarray(bins)
    data = np.minimum(bins, 255).astype(np.uint8)
    return ACTIVITY_MAGIC + struct.pack("<IIIII", VERSION, int(tick), data.shape[1], data.shape[0], int(round(bin_ms))) + data.tobytes()


def decode_activity(blob):
    if blob[:4] != ACTIVITY_MAGIC:
        raise ValueError("Not an activity file")
    version, tick, n, nbins, bin_ms = struct.unpack("<IIIII", blob[4:24])
    data = np.frombuffer(blob[24:24 + n * nbins], dtype=np.uint8).reshape(nbins, n)
    return {"version": version, "tick": tick, "n": n, "bins": nbins, "bin_ms": bin_ms, "counts": data}


def decode_atlas(blob):
    if blob[:4] != ATLAS_MAGIC:
        raise ValueError("Not an atlas file")
    version, n = struct.unpack("<II", blob[4:12])
    xyz = np.frombuffer(blob[12:12 + n * 6], dtype="<i2").reshape(n, 3)
    cls = np.frombuffer(blob[12 + n * 6:12 + n * 7], dtype=np.uint8)
    group = np.frombuffer(blob[12 + n * 7:12 + n * 8], dtype=np.uint8)
    return {"version": version, "n": n, "xyz": xyz, "cls": cls, "group": group}
