"""Package a static demo of the watch page from a run directory.

The output is the same page with a captured /api/state, /api/board and sensory
frame embedded, root-relative paths made relative, and the clock frozen at
capture time (see DEMO in app.js). Use it for previews where no server runs.

    python site/tools/demo.py --run runs/paper --out /tmp/demo [--artifact]

--artifact strips the document wrapper so the page can be published where a
host supplies <html>, <head> and <body> itself.
"""

import argparse
import base64
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT))

from stonkfly.publish import snapshot  # noqa: E402
from stonkfly.satrush.api import ENDPOINTS, SatRushApi  # noqa: E402

COPY = ["app.js", "scene.js", "monitor.js", "style.css", "logo.svg", "favicon.svg"]


def build(run, out, network="mainnet", artifact=False, live_board=True):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name in COPY:
        shutil.copyfile(SITE / name, out / name)
    captured_at = time.time()
    state = snapshot(run, now=captured_at)
    state.setdefault("publication", {})["served_at"] = captured_at
    board = None
    if live_board:
        try:
            board = {**SatRushApi(ENDPOINTS[network]["api"]).board().summary(), "live": True}
        except Exception as e:  # The demo still works from the retina's board.
            print(json.dumps({"live_board": f"unavailable: {type(e).__name__}"}), file=sys.stderr)
    sensory = None
    frame = Path(run) / "latest-input.png"
    if frame.exists():
        sensory = "data:image/png;base64," + base64.b64encode(frame.read_bytes()).decode()
    demo = {"captured_at": captured_at, "state": state, "board": board, "sensory": sensory}
    (out / "demo-data.js").write_text("window.__STONKFLY_DEMO = " + json.dumps(demo, allow_nan=False) + ";\n")
    html = (SITE / "index.html").read_text()
    html = re.sub(r'(src|href|content)="/([^"/][^"]*)"', r'\1="\2"', html)
    html = html.replace('import("/scene.js")', 'import("./scene.js")')
    if sensory:
        html = html.replace('src="api/sensory.png"', f'src="{sensory}"')
    html = html.replace('<script type="module" src="app.js"></script>', '<script src="demo-data.js"></script>\n<script type="module" src="app.js"></script>')
    assert "demo-data.js" in html, "app.js script tag not found"
    if artifact:
        head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
        body = re.search(r"<body[^>]*>(.*?)</body>", html, re.S).group(1)
        head = re.sub(r'<meta charset[^>]*>|<meta name="viewport"[^>]*>', "", head)
        head = re.sub(r"<title>.*?</title>", "<title>Sat Rush Fly Watch</title>", head, count=1)
        html = head.strip() + "\n" + body.strip() + "\n"
    (out / "index.html").write_text(html)
    return {"out": str(out), "tick": state.get("tick"), "live_board": board is not None, "captured_at": captured_at}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, default=ROOT / "runs/paper")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--network", choices=["mainnet", "devnet"], default="mainnet")
    p.add_argument("--artifact", action="store_true")
    p.add_argument("--no-live-board", action="store_true")
    a = p.parse_args()
    print(json.dumps(build(a.run, a.out, a.network, a.artifact, not a.no_live_board)))


if __name__ == "__main__":
    main()
