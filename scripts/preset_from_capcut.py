"""Make a subtitle preset from a CapCut project the person cut by hand: the text style used most for normal
lines, the most-used coloured style for emphasis, and a small caption style if the project has one.

    python scripts/preset_from_capcut.py "<CapCut project folder>" --name my-look

Writes presets/<name>.json and prints it. Stops (no file) when the project has no subtitles to learn from.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")


def _hex(rgb: list[float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--name", required=True)
    a = ap.parse_args()
    f = Path(a.project) / "draft_content.json"
    if not f.is_file():
        print(f"not a CapCut project: {a.project}", file=sys.stderr)
        return 1
    d = json.loads(f.read_text(encoding="utf-8"))
    mats = {m["id"]: m for m in d["materials"].get("texts", [])}
    seen: Counter = Counter()
    for tr in d["tracks"]:
        if tr["type"] != "text":
            continue
        for s in tr["segments"]:
            m = mats.get(s["material_id"])
            if not m or not m.get("content", "").startswith("{"):
                continue
            st = (json.loads(m["content"]).get("styles") or [{}])[0]
            fill = st.get("fill", {}).get("content", {}).get("solid", {}).get("color")
            stroke = (st.get("strokes") or [{}])[0]
            if not fill or not st.get("size"):
                continue
            key = (round(float(st["size"])), _hex(fill), _hex(stroke.get("content", {}).get("solid", {}).get("color", [0, 0, 0])),
                   round(float(stroke.get("width", 0.04)), 3), round(float(s["clip"]["transform"]["y"]), 2))
            seen[key] += 1
    if not seen:
        print("this project has no subtitles to learn a preset from", file=sys.stderr)
        return 1
    role = lambda k: {"size": k[0], "y": k[4], "color": k[1], "outline": k[2], "outline_width": k[3]}  # noqa: E731
    white = lambda k: k[1] in ("#ffffff", "#fefefe", "#fdfdfd")  # noqa: E731
    big = [k for k, _ in seen.most_common() if k[0] > 12]
    small = [k for k, _ in seen.most_common() if k[0] <= 12]
    normal = next((k for k in big if white(k)), big[0] if big else None)
    emphasis = next((k for k in big if not white(k)), None)
    if normal is None or emphasis is None:
        print("need both a normal (white) and an emphasis (coloured) subtitle in the project", file=sys.stderr)
        return 1
    preset = {"about": f"Made from the CapCut project '{Path(a.project).name}'.",
              "normal": role(normal), "emphasis": role(emphasis),
              "caption": dict(role(small[0]), language="en") if small else None}
    out = ROOT / "presets" / f"{a.name}.json"
    out.write_text(json.dumps(preset, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"preset": str(out), **preset}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
