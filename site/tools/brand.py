#!/usr/bin/env python3
"""Generate the Stonkfly brand assets from hand-drawn pixel maps.

Outputs (relative to --out, default: the ``site`` directory next to this file):

    logo.svg     276x56 pixel wordmark: tiny fly glyph, STONKFLY, acid cursor block
    favicon.svg  16x16 pixel fly on a dark squircle
    share.png    1200x630 share card: large pixel fly, wordmark, tagline, 7x3 tiles

Everything is drawn from a 5x7 bitmap font and pixel maps defined in this file,
so the output is reproducible and depends only on Pillow. No third-party fonts,
images or code. Run:

    python site/tools/brand.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Palette (mirrors site/style.css tokens)
# ---------------------------------------------------------------------------

BG = "#060709"
PANEL = "#0e1015"
LINE = "#32353e"
MUTED = "#989aaa"
INK = "#f3f4ed"
ACID = "#bdff32"
RED = "#ff4b78"
BLUE = "#7376ff"


def rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha


def blend(top: str, alpha: float, under: str) -> str:
    """Opaque hex colour of ``top`` at ``alpha`` composited over ``under``."""
    t, u = rgba(top), rgba(under)
    return "#%02x%02x%02x" % tuple(round(t[i] * alpha + u[i] * (1 - alpha)) for i in range(3))


# ---------------------------------------------------------------------------
# 5x7 bitmap font. Glyphs are 7 rows; width is the row length (most are 5).
# ---------------------------------------------------------------------------

FONT: dict[str, list[str]] = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
    " ": ["...", "...", "...", "...", "...", "...", "..."],
    ".": ["..", "..", "..", "..", "..", "##", "##"],
    ",": ["..", "..", "..", "..", "##", "##", "#."],
    "·": ["..", "..", "..", "##", "##", "..", ".."],
    ":": ["..", "##", "##", "..", "##", "##", ".."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    "+": [".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."],
    "/": ["....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."],
    "%": ["##..#", "##..#", "...#.", "..#..", ".#...", "#..##", "#..##"],
    "$": ["..#..", ".####", "#.#..", ".###.", "..#.#", "####.", "..#.."],
    "!": ["#", "#", "#", "#", "#", ".", "#"],
    "?": [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."],
    "&": [".##..", "#..#.", "#..#.", ".##..", "#.#.#", "#..#.", ".##.#"],
    "#": [".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."],
}

for _ch, _rows in FONT.items():
    assert len(_rows) == 7, _ch
    assert len({len(r) for r in _rows}) == 1, _ch


def text_cells(text: str, tracking: int = 1) -> tuple[list[tuple[int, int]], int]:
    """Lit cells of ``text`` in font units, plus the total width in cells."""
    cells: list[tuple[int, int]] = []
    x = 0
    for ch in text.upper():
        rows = FONT[ch]
        for y, row in enumerate(rows):
            for dx, bit in enumerate(row):
                if bit == "#":
                    cells.append((x + dx, y))
        x += len(rows[0]) + tracking
    return cells, x - tracking


# ---------------------------------------------------------------------------
# Pixel maps. One character per cell; "." is transparent.
# ---------------------------------------------------------------------------

EYE_DARK = "#8c1a3f"
EYE_HI = "#ffb9cb"
BODY = "#7d8499"
BODY_DARK = "#4d526a"
BODY_LIGHT = "#aab0c4"
LEG = "#5f6579"
WING = "#dde3f4"
WING_VEIN = "#ffffff"

def mirror(half: list[str]) -> list[str]:
    """Left half of a symmetric map -> full map (right half is the reflection)."""
    return [row + row[::-1] for row in half]


# Large fly, 24x20, seen from above, head up, wings swept back, six legs.
# Only the left 12 columns are drawn; the right half mirrors them.
FLY_BIG = mirror([
    #012345678901
    "........g...",  # 0 antenna tip
    ".........g..",  # 1
    ".......eeeEl",  # 2 eye (E rim, e red, h highlight), face l
    "..g..EeheeEl",  # 3 front leg reaches up beside the eye
    "...g.EeeeeEl",  # 4
    "....gEeeeeBb",  # 5
    ".....gEeeeBb",  # 6
    "......gEE.lb",  # 7 neck
    ".......glbbb",  # 8 thorax
    ".....ggglbbB",  # 9 mid leg
    "...gg.wwlbbB",  # 10 wing root
    ".gg..wvwwlbB",  # 11
    "g...wwvwwxbb",  # 12 abdomen; x = wing over abdomen, v = vein
    "...wwvwwwxBB",  # 13
    "..wwvwwwwxbb",  # 14
    ".wwwvwwwgbbb",  # 15 hind leg
    ".wwvwww.gBBB",  # 16
    "wwvwww.g..bb",  # 17
    "wwvww..g..bB",  # 18
    ".vw...g....B",  # 19 abdomen tip
])

WING_ALPHA = 0.5
FLY_BIG_PALETTE = {
    "e": rgba(RED),
    "E": rgba(EYE_DARK),
    "h": rgba(EYE_HI),
    "b": rgba(BODY),
    "B": rgba(BODY_DARK),
    "l": rgba(BODY_LIGHT),
    "g": rgba(LEG),
    "w": rgba(WING, round(255 * WING_ALPHA)),
    "v": rgba(WING_VEIN, 185),
    "x": rgba(blend(WING, WING_ALPHA, BODY)),  # wing membrane over the abdomen
}

# Favicon fly, 14x14, grey body, red eyes, pale wings (left half, mirrored).
FLY_ICON = mirror([
    #0123456
    "..eee..",  # 0
    ".eeeebb",  # 1
    ".eeeebb",  # 2
    "..eeebb",  # 3
    "g...bbb",  # 4 front leg
    ".g.wBBB",  # 5 wing root
    "..wwbbb",  # 6
    ".wwwbbb",  # 7
    "wwww.bb",  # 8
    "www.gbb",  # 9 hind leg
    "ww..gbb",  # 10
    ".w..g.b",  # 11
    "....g.b",  # 12
    "......b",  # 13 tip
])

FLY_ICON_PALETTE = {
    "e": rgba(RED),
    "b": rgba(MUTED),
    "B": rgba("#6b6f80"),
    "g": rgba("#6b6f80"),
    "w": rgba(INK, 170),
}

# Wordmark fly, 12x12, ink body so it reads on the dark header at 4px cells.
FLY_MARK = mirror([
    #012345
    "..ee..",  # 0
    ".eeeii",  # 1
    ".eeeii",  # 2
    "..eeii",  # 3
    "g..iii",  # 4 front leg
    ".g.wii",  # 5 wing root
    "..wwii",  # 6
    ".wwwii",  # 7
    "wwww.i",  # 8
    "www.gi",  # 9 hind leg
    ".ww.gi",  # 10
    "....g.",  # 11
])

FLY_MARK_PALETTE = {
    "e": rgba(RED),
    "i": rgba(INK),
    "g": rgba(MUTED),
    "w": rgba(MUTED, 190),
}


def check_map(pixmap: list[str], width: int, height: int, palette: dict) -> None:
    assert len(pixmap) == height, (len(pixmap), height)
    for row in pixmap:
        assert len(row) == width, (row, len(row), width)
        for ch in row:
            assert ch == "." or ch in palette, ch


check_map(FLY_BIG, 24, 20, FLY_BIG_PALETTE)
check_map(FLY_ICON, 14, 14, FLY_ICON_PALETTE)
check_map(FLY_MARK, 12, 12, FLY_MARK_PALETTE)


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------


def runs(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Merge lit cells into rectangles (x, y, w, h): horizontal runs first,
    then identical runs on consecutive rows are stacked."""
    rows: list[tuple[int, int, int]] = []
    by_row: dict[int, list[int]] = {}
    for x, y in cells:
        by_row.setdefault(y, []).append(x)
    for y in sorted(by_row):
        xs = sorted(by_row[y])
        start = prev = xs[0]
        for x in xs[1:]:
            if x == prev + 1:
                prev = x
                continue
            rows.append((start, y, prev - start + 1))
            start = prev = x
        rows.append((start, y, prev - start + 1))
    out: list[tuple[int, int, int, int]] = []
    open_runs: dict[tuple[int, int], int] = {}  # (x, w) -> index into out
    for x, y, w in rows:
        i = open_runs.get((x, w))
        if i is not None and out[i][1] + out[i][3] == y:
            out[i] = (x, out[i][1], w, out[i][3] + 1)
        else:
            open_runs[(x, w)] = len(out)
            out.append((x, y, w, 1))
    return sorted(out, key=lambda r: (r[1], r[0]))


def svg_group(cells: set[tuple[int, int]], scale: int, ox: int, oy: int, fill: str, opacity: float | None = None) -> str:
    attrs = f'fill="{fill}"'
    if opacity is not None:
        attrs += f' fill-opacity="{opacity:.3g}"'
    parts = [f"  <g {attrs}>"]
    for x, y, w, h in runs(cells):
        parts.append(f'    <rect x="{ox + x * scale}" y="{oy + y * scale}" width="{w * scale}" height="{h * scale}"/>')
    parts.append("  </g>")
    return "\n".join(parts)


def svg_map(pixmap: list[str], palette: dict, scale: int, ox: int, oy: int) -> str:
    """Emit one <g> per palette entry so alpha stays a plain fill-opacity."""
    groups: dict[str, set[tuple[int, int]]] = {}
    for y, row in enumerate(pixmap):
        for x, ch in enumerate(row):
            if ch != ".":
                groups.setdefault(ch, set()).add((x, y))
    parts = []
    for ch in sorted(groups, key=lambda c: list(palette).index(c)):
        r, g, b, a = palette[ch]
        fill = "#%02x%02x%02x" % (r, g, b)
        parts.append(svg_group(groups[ch], scale, ox, oy, fill, None if a == 255 else a / 255))
    return "\n".join(parts)


def write_logo(path: Path) -> None:
    """276x56: 69x14 cells of 4px. Fly | gap | STONKFLY | acid cursor block."""
    scale = 4
    width, height = 276, 56
    fly_x, fly_y = 0, 1  # cells
    text_x, text_y = 14, 3
    cells, text_w = text_cells("STONKFLY", tracking=1)
    cursor_x = text_x + text_w + 1
    assert (cursor_x + 2) * scale <= width, cursor_x
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'shape-rendering="crispEdges" role="img" aria-label="STONKFLY">',
        "  <title>STONKFLY</title>",
        svg_map(FLY_MARK, FLY_MARK_PALETTE, scale, fly_x * scale, fly_y * scale),
        svg_group(set(cells), scale, text_x * scale, text_y * scale, INK),
        svg_group({(0, 0), (1, 0), (0, 1), (1, 1)}, scale, cursor_x * scale, (text_y + 5) * scale, ACID),
        "</svg>",
        "",
    ]
    path.write_text("\n".join(parts))


def write_favicon(path: Path) -> None:
    """16x16: dark squircle, 14x14 fly at 1px cells centred."""
    fly_w, fly_h = len(FLY_ICON[0]), len(FLY_ICON)
    ox, oy = (16 - fly_w) // 2, (16 - fly_h) // 2
    squircle = "M8 0C14 0 16 2 16 8C16 14 14 16 8 16C2 16 0 14 0 8C0 2 2 0 8 0Z"
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" '
        'shape-rendering="crispEdges" role="img" aria-label="Stonkfly">',
        "  <title>Stonkfly</title>",
        f'  <path d="{squircle}" fill="{PANEL}"/>',
        svg_map(FLY_ICON, FLY_ICON_PALETTE, 1, ox, oy),
        "</svg>",
        "",
    ]
    path.write_text("\n".join(parts))


# ---------------------------------------------------------------------------
# PNG helpers
# ---------------------------------------------------------------------------


def render_map(pixmap: list[str], palette: dict, scale: int) -> Image.Image:
    """Rasterise a pixel map at 1px per cell, then nearest-neighbour upscale."""
    h, w = len(pixmap), len(pixmap[0])
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y, row in enumerate(pixmap):
        for x, ch in enumerate(row):
            if ch != ".":
                px[x, y] = palette[ch]
    return im.resize((w * scale, h * scale), Image.NEAREST)


def draw_text(im: Image.Image, text: str, x: int, y: int, scale: int, color: str, tracking: int = 1) -> int:
    """Draw bitmap text with its top-left at (x, y). Returns the pixel width."""
    cells, width = text_cells(text, tracking)
    d = ImageDraw.Draw(im)
    fill = rgba(color)
    for cx, cy in cells:
        x0, y0 = x + cx * scale, y + cy * scale
        d.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill=fill)
    return width * scale


def text_width(text: str, scale: int, tracking: int = 1) -> int:
    return text_cells(text, tracking)[1] * scale


def hard_box(im: Image.Image, box: tuple[int, int, int, int], fill: str, line: str, border: int = 2,
             shadow: tuple[int, int, str] | None = None) -> None:
    """Rectangle with a solid border and an optional hard offset shadow."""
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    if shadow:
        dx, dy, sc = shadow
        d.rectangle((x0 + dx, y0 + dy, x1 + dx, y1 + dy), fill=rgba(sc))
    d.rectangle((x0, y0, x1, y1), fill=rgba(line))
    d.rectangle((x0 + border, y0 + border, x1 - border, y1 - border), fill=rgba(fill))


def dotted_grid(im: Image.Image, pitch: int = 16, dot: int = 2, color: str = "#1c1e24") -> None:
    d = ImageDraw.Draw(im)
    w, h = im.size
    off = pitch // 2 - dot // 2
    for y in range(off, h, pitch):
        for x in range(off, w, pitch):
            d.rectangle((x, y, x + dot - 1, y + dot - 1), fill=rgba(color))


def write_share(path: Path) -> None:
    W, H = 1200, 630
    im = Image.new("RGBA", (W, H), rgba(BG))
    dotted_grid(im)

    # --- left: the fly in a stage frame -----------------------------------
    scale = 13
    fly = render_map(FLY_BIG, FLY_BIG_PALETTE, scale)  # 312 x 260
    frame = (64, 80, 64 + 400, 80 + 440)
    hard_box(im, frame, fill="#000000", line=LINE, border=2, shadow=(6, 6, "#1f2128"))
    # stage bar
    bar_h = 34
    d = ImageDraw.Draw(im)
    d.rectangle((frame[0] + 2, frame[1] + 2, frame[2] - 2, frame[1] + bar_h), fill=rgba("#000000"))
    d.rectangle((frame[0] + 2, frame[1] + bar_h, frame[2] - 2, frame[1] + bar_h + 1), fill=rgba(LINE))
    d.rectangle((frame[0] + 16, frame[1] + 13, frame[0] + 23, frame[1] + 20), fill=rgba(ACID))
    draw_text(im, "FLY.EXE", frame[0] + 34, frame[1] + 10, 2, INK)
    label = "DECORATIVE AVATAR"
    draw_text(im, label, frame[2] - 16 - text_width(label, 2), frame[1] + 10, 2, MUTED)
    fx = frame[0] + (frame[2] - frame[0] - fly.width) // 2
    fy = frame[1] + bar_h + (frame[3] - frame[1] - bar_h - fly.height) // 2
    im.alpha_composite(fly, (fx, fy))
    # caption strip at the bottom of the stage
    cap = "166,700 NEURONS · 25.6M CONNECTIONS"
    draw_text(im, cap, frame[0] + (frame[2] - frame[0] - text_width(cap, 2)) // 2, frame[3] - 30, 2, MUTED)

    # --- right: wordmark, tagline, tiles -----------------------------------
    rx = 540
    ty = 112
    wm_scale = 10
    wm_w = draw_text(im, "STONKFLY", rx, ty, wm_scale, INK, tracking=1)
    # acid cursor block after the wordmark
    cx = rx + wm_w + wm_scale
    d.rectangle((cx, ty + 5 * wm_scale, cx + 2 * wm_scale - 1, ty + 7 * wm_scale - 1), fill=rgba(ACID))
    ty += 7 * wm_scale + 26
    draw_text(im, "NEURAL MINING ON SAT RUSH", rx, ty, 4, ACID, tracking=1)
    ty += 7 * 4 + 22
    draw_text(im, "21 TILES · ONE DEPLOY PER ROUND · FIXED STAKE", rx, ty, 2, MUTED)
    ty += 7 * 2 + 10
    draw_text(im, "NO EDGE · EXPECTED LOSS · DECORATIVE AVATAR", rx, ty, 2, MUTED)

    # 7x3 tile strip
    ty += 7 * 2 + 38
    tile, gap = 56, 8
    lit = {2, 5, 9, 12, 17, 19}
    red = 14
    for i in range(21):
        r, c = divmod(i, 7)
        x0 = rx + c * (tile + gap)
        y0 = ty + r * (tile + gap)
        n = i + 1
        if n == red:
            fill, line, num = RED, RED, BG
            shadow = (3, 3, "#7a2440")
        elif n in lit:
            fill, line, num = ACID, ACID, BG
            shadow = (3, 3, "#5c7d19")
        else:
            fill, line, num = PANEL, LINE, MUTED
            shadow = (3, 3, "#1f2128")
        hard_box(im, (x0, y0, x0 + tile - 1, y0 + tile - 1), fill=fill, line=line, border=2, shadow=shadow)
        s = str(n)
        draw_text(im, s, x0 + (tile - text_width(s, 2)) // 2, y0 + (tile - 14) // 2, 2, num)
    ty += 3 * tile + 2 * gap + 18
    legend_x = rx
    d.rectangle((legend_x, ty + 2, legend_x + 9, ty + 11), fill=rgba(ACID))
    legend_x += 16 + draw_text(im, "FLY PICKS", legend_x + 16, ty, 2, MUTED) + 24
    d.rectangle((legend_x, ty + 2, legend_x + 9, ty + 11), fill=rgba(RED))
    draw_text(im, "LAST WINNING TILE", legend_x + 16, ty, 2, MUTED)

    im.convert("RGB").save(path, "PNG", optimize=True)


# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="directory to write logo.svg, favicon.svg and share.png into")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_logo(out / "logo.svg")
    write_favicon(out / "favicon.svg")
    write_share(out / "share.png")
    for name in ("logo.svg", "favicon.svg", "share.png"):
        print(f"{name}\t{(out / name).stat().st_size} bytes")


if __name__ == "__main__":
    main()
