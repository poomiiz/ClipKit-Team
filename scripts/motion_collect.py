"""Pull HyperFrames registry items into a local collection to try out before they become ClipKit motion templates.

    python scripts/motion_collect.py --role หัวคลิป --mood ตื่นเต้น      # every item matching both filters
    python scripts/motion_collect.py --name lt-clean-bar,glitch --out D:/my-collection
    python scripts/motion_collect.py --role ซับ --dry-run                 # list the picks, install nothing
Items come from motion/library/hyperframes.json. The collection is a HyperFrames project (default motion/collection/,
portrait 1080x1920) and index.md in it lists each item with the steps to promote it into motion/<name>/.
Rerunning skips items already installed in the project.
"""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "motion" / "library" / "hyperframes.json"
HF = "hyperframes@0.8.101"


def pick(items, role, mood, names):
    for key, value in (("role", role), ("mood", mood)):
        valid = sorted({it[key] for it in items if it[key]})
        if value and value not in valid:
            raise SystemExit(f"unknown {key} {value!r}; valid: {', '.join(valid)}")
    by_name = {it["name"]: it for it in items}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise SystemExit(f"not in {LIBRARY.name}: {', '.join(missing)}")
    picked = [it for it in items if it["role"] != "skip"
              and (not role or it["role"] == role)
              and (not mood or it["mood"] == mood)
              and (not names or it["name"] in names)]
    if not picked:
        raise SystemExit("no item matches these filters")
    return picked


def hf(npx, *args, cwd):
    r = subprocess.run([npx, "--yes", HF, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"})
    if r.returncode:
        raise SystemExit(f"hyperframes {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    return r.stdout


def installed(out):
    return {it["name"] for it in json.loads((out / "hyperframes.json").read_text(encoding="utf-8")).get("registryItems", [])}


def write_index(out, items):
    lines = ["# HyperFrames collection", "",
             "Preview: `npx --yes " + HF + " preview` in this folder. Files are in `compositions/`.", "",
             "| name | type | role | mood | thai | vertical | seconds |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {it['name']} | {it['type']} | {it['role']} | {it['mood']} | {it['thai']} | {it['vertical']} | {it['seconds']} |"
              for it in items]
    lines += ["", "## Promote one into a ClipKit template (motion/README.md)", "",
              "1. Copy its HTML from `compositions/` (components: `compositions/components/`) to `motion/<name>/index.html`.",
              "2. Add the `<script type=\"application/json\" id=\"clipkit-motion\">` block with its texts and duration.",
              "3. Test with real Thai text (items marked `test` break per-letter effects) and at 1080x1920 (`adapt` items are landscape).",
              "4. Render once with `npx --yes " + HF + " render` and check the result before using it in a clip."]
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role")
    ap.add_argument("--mood")
    ap.add_argument("--name", default="", help="comma-separated item names")
    ap.add_argument("--out", type=Path, default=ROOT / "motion" / "collection")
    ap.add_argument("--dry-run", action="store_true", help="list the picks only")
    a = ap.parse_args()
    items = json.loads(LIBRARY.read_text(encoding="utf-8"))
    picked = pick(items, a.role, a.mood, [n for n in a.name.split(",") if n])
    out = a.out.resolve()
    if a.dry_run:
        for it in picked:
            print(f"{it['name']}\t{it['role']}\t{it['mood']}")
        return
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("Node.js (npx) is not installed")
    if not (out / "hyperframes.json").exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        hf(npx, "init", out.name, "--example", "blank", "--resolution", "portrait", "--non-interactive", cwd=out.parent)
    have = installed(out)
    for it in picked:
        if it["name"] in have:
            print("skip", it["name"])
            continue
        hf(npx, "add", it["name"], "--dir", str(out), "--json", "--no-clipboard", cwd=out)
        print("added", it["name"])
    have = installed(out)
    write_index(out, [it for it in items if it["name"] in have])
    print(f"{len(have)} items in {out / 'index.md'}")


if __name__ == "__main__":
    main()
