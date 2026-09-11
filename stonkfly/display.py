"""Render the public board into a fixed 320x180 RGB frame for the retina.

The frame shows only what any spectator sees: stake per tile, recent winning
tiles, the pot and the round countdown. It never shows this wallet's balance,
its own past choices or its P&L, so reinforcement stays an explicit stimulus.
"""

import numpy as np
from PIL import Image, ImageDraw

from .config import TILES

WIDTH, HEIGHT = 320, 180
COLUMNS, ROWS = 7, 3
BOARD_LEFT, BOARD_TOP, TILE_W, TILE_H, GAP = 9, 30, 42, 38, 2


def tile_box(index):
    """Pixel rectangle of 0-based tile index in a 7x3 grid, row-major."""
    row, col = divmod(index, COLUMNS)
    x = BOARD_LEFT + col * (TILE_W + GAP)
    y = BOARD_TOP + row * (TILE_H + GAP)
    return (x, y, x + TILE_W - 1, y + TILE_H - 1)


def board_frame(board, now=None):
    im = Image.new("RGB", (WIDTH, HEIGHT), (238, 241, 247))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, WIDTH - 1, 23), fill=(19, 36, 71))
    pot = board.deployed_usd / 1e6
    d.text((8, 6), f"ROUND {board.round_id}  POT {pot:.0f}  MINERS {board.miners_count}"[:48], fill=(219, 229, 249))
    stakes = [s for s, _ in board.tile_stakes]
    top = max(max(stakes), 1)
    recent = {tile: rank for rank, (_, tile) in enumerate(board.previous_winners[:5])}
    for i in range(TILES):
        x0, y0, x1, y1 = tile_box(i)
        share = stakes[i] / top
        # Heavier stake = deeper blue. Light background keeps photoreceptor drive.
        fill = (int(225 - 150 * share), int(232 - 110 * share), int(248 - 40 * share))
        d.rectangle((x0, y0, x1, y1), fill=fill, outline=(120, 135, 170))
        if i + 1 in recent:
            rank = recent[i + 1]
            color = (200, 30, 60) if rank == 0 else (240, 150, 60)
            d.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), outline=color, width=3 - min(rank, 2))
        d.text((x0 + 3, y0 + 2), f"{i + 1}", fill=(28, 46, 82))
        d.text((x0 + 3, y0 + 22), f"{stakes[i] / 1e6:.0f}", fill=(28, 46, 82))
    seconds = board.seconds_remaining(now)
    total = max(board.end_slot - board.start_slot, 1) * board.slot_ms / 1000
    fraction = 0.0 if seconds is None else min(max(seconds / total, 0.0), 1.0)
    d.rectangle((9, 156, 310, 162), outline=(120, 135, 170), fill=(255, 255, 255))
    d.rectangle((10, 157, 10 + int(299 * fraction), 161), fill=(0, 101, 183))
    winners = " ".join(str(t) for _, t in board.previous_winners[:8])
    d.text((9, 165), f"LAST {winners}"[:50], fill=(28, 46, 82))
    return np.asarray(im, dtype=np.uint8)
