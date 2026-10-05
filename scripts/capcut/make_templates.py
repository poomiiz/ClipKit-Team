"""Pull the CapCut building blocks for app/capcut_build.py out of your own CapCut projects into
app/capcut_templates.json: one text style per role (white lead, orange punch, small caption, title), the click
sound he puts on the coloured words, a b-roll clip on the overlay track, and the skin effect. Styles only - no
footage, no words of the talk are kept (text is blanked). Re-run when the look changes.

    python scripts/capcut/make_templates.py "<project with the subtitle roles>" "<project with the skin effect>"
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "app" / "capcut_templates.json"


def load(folder: str) -> tuple[dict, dict]:
    d = json.loads((Path(folder) / "draft_content.json").read_text(encoding="utf-8"))
    idx = {m["id"]: (k, m) for k, v in d["materials"].items() if isinstance(v, list)
           for m in v if isinstance(m, dict) and "id" in m}
    return d, idx


def with_refs(seg: dict, idx: dict) -> dict:
    return {"seg": copy.deepcopy(seg), "mat": copy.deepcopy(idx[seg["material_id"]][1]),
            "refs": [[idx[r][0], copy.deepcopy(idx[r][1])] for r in seg.get("extra_material_refs", []) if r in idx]}


def main() -> None:
    roles_project, skin_project = sys.argv[1], sys.argv[2]
    d, idx = load(roles_project)
    out: dict = {}
    texts = [s for t in d["tracks"] if t["type"] == "text" for s in t["segments"]]
    def body(s): return json.loads(idx[s["material_id"]][1]["content"])
    def pick(test):
        s = next(s for s in texts if test(s, body(s)))
        t = with_refs(s, idx)
        b = json.loads(t["mat"]["content"])
        b["text"] = ""
        t["mat"]["content"] = json.dumps(b, ensure_ascii=False)
        return t
    size = lambda b: round(b["styles"][0].get("size", 0))  # noqa: E731
    out["white"] = pick(lambda s, b: size(b) == 25 and b["styles"][0]["fill"]["content"]["solid"]["color"][2] > 0.9)
    out["orange"] = pick(lambda s, b: size(b) == 30 and b["styles"][0]["fill"]["content"]["solid"]["color"][2] < 0.1)
    out["caption"] = pick(lambda s, b: size(b) <= 10)
    out["title"] = pick(lambda s, b: len(b["styles"]) == 2 and s["target_timerange"]["start"] < 500000)
    tracks = [t for t in d["tracks"]]
    broll = next(t for t in tracks if t["type"] == "video" and t is not next(x for x in tracks if x["type"] == "video"))
    out["broll"] = with_refs(broll["segments"][0], idx)
    out["broll"]["mat"].update(path="", material_name="", name="")  # the client's own file: not kept
    click = next(t for t in tracks if t["type"] == "audio" and len(t["segments"]) > 5)
    out["click"] = with_refs(click["segments"][0], idx)
    d8, idx8 = load(skin_project)
    out["skin"] = copy.deepcopy(next(m for k, m in idx8.values() if k == "effects" and "face_adjust_skin_Intensity" in json.dumps(m)))
    import os
    import re
    text = json.dumps(out, ensure_ascii=False, indent=1)
    local = Path(os.environ["LOCALAPPDATA"]).as_posix()
    # no machine-specific paths in the repo: capcut_build fills these in on each machine
    text = re.sub(re.escape(local) + r"/CapCut/Apps/[0-9.]+", "{CAPCUT_APP}", text).replace(local, "{LOCALAPPDATA}")
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT, {k: (v["mat"].get("name") or v["mat"].get("path", ""))[-40:] if isinstance(v, dict) and "mat" in v else "" for k, v in out.items()})


if __name__ == "__main__":
    main()
