"""Start a new motion effect for the ClipKit motion page: a folder motion/<name>/ with a working, Thai-safe
template (or a copy of an existing effect) that you then change by hand or with an agent.

    python scripts/motion_new.py pop-word --label "คำเด้งทีละตัว"            # from the starter template
    python scripts/motion_new.py hook-v2 --from hook-title --label "หัวคลิป v2"   # copy an existing effect
    python scripts/motion_new.py pop-word --render                            # test MOV with the default texts
The effect shows on the motion page once its folder has the clipkit-motion block (the starter has one).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTION = ROOT / "motion"
STARTER = ROOT / "templates" / "motion-starter"
HF = "hyperframes@0.8.101"
BLOCK = re.compile(r'(<script type="application/json" id="clipkit-motion">)(.*?)(</script>)', re.S)


def spec(page: Path) -> dict:
    m = BLOCK.search(page.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"{page} has no clipkit-motion block - add one (see motion/README.md)")
    return json.loads(m.group(2))


def create(name: str, source: str | None, label: str | None) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise SystemExit("name: lowercase letters, digits and - only (it becomes the folder name)")
    dst = MOTION / name
    if dst.exists():
        raise SystemExit(f"{dst} already exists")
    src = STARTER if source is None else MOTION / source
    if not (src / "index.html").is_file():
        have = sorted(p.parent.name for p in MOTION.glob("*/index.html"))
        raise SystemExit(f"no effect '{source}' in motion/ (have: {', '.join(have)})")
    shutil.copytree(src, dst)
    page = dst / "index.html"
    text = page.read_text(encoding="utf-8")
    m = BLOCK.search(text)
    if not m:
        raise SystemExit(f"{src.name} has no clipkit-motion block to copy")
    s = json.loads(m.group(2).replace("__LABEL__", name))
    s["label"] = label or (s["label"] if source else name)
    page.write_text(text[:m.start(2)] + json.dumps(s, ensure_ascii=False, indent=1) + text[m.end(2):], encoding="utf-8")
    meta = dst / "meta.json"
    if meta.is_file():
        meta.write_text(json.dumps({"id": name, "name": name}), encoding="utf-8")
    return dst


def render(folder: Path) -> Path:
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("npx not found: install Node.js LTS from nodejs.org")
    out = folder / f"{folder.name}-test.mov"
    r = subprocess.run([npx, "--yes", HF, "render", "--format", "mov", "-o", str(out)], cwd=folder)
    if r.returncode or not out.is_file():
        raise SystemExit("render failed (see the output above)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Start a new ClipKit motion effect.")
    ap.add_argument("name", help="folder name under motion/, e.g. pop-word")
    ap.add_argument("--from", dest="source", help="copy this motion/<effect> instead of the starter template")
    ap.add_argument("--label", help="name shown on the motion page")
    ap.add_argument("--render", action="store_true", help="render a test MOV (an existing effect is rendered as it is)")
    a = ap.parse_args()
    folder = MOTION / a.name
    if folder.exists() and a.render and not a.source and not a.label:
        spec(folder / "index.html")
    else:
        folder = create(a.name, a.source, a.label)
        print(f"made {folder.relative_to(ROOT)} - label: {spec(folder / 'index.html')['label']}")
    print(f"preview: serve motion/ and open {a.name}/index.html?bg=<image> (add ?text=... to try other words)")
    if a.render:
        print(f"rendered {render(folder)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
