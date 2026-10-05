"""The cut log: what each run made and what people said about it, so the rules can be fixed from real results.

Everything goes under <output_dir>/logs/ (config.json):
  runs.jsonl      one line per finished clip: version, preset, story, subtitle lengths, title, cuts, length
  feedback.jsonl  one line per complaint: what the person said, what was changed
  runs/<time> <project>/   copy of the AI's decisions (stories, emphasis, title, caption, style) - text only

    python scripts/cutlog.py feedback "<project folder>" "ซับยาวไป" [--fix "ตัดท่อนเหลือ 8-14 ตัว"]
    python scripts/cutlog.py report            # zip the logs to send back (no video inside)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ("clipkit_punch.json", "clipkit_hook.json", "clipkit_caption.json", "clipkit_style.json",
             "clipkit_lines.json", "clipkit_broll.json")


def logs_dir() -> Path:
    sys.path.insert(0, str(ROOT / "app"))
    import kit_settings
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root")
    if not out:
        raise SystemExit("no output folder in config.json - run setup first")
    d = Path(out) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def version() -> str:
    r = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %cs"], capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def _snapshot(project: Path, tag: str) -> str:
    dst = logs_dir() / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')} {tag} {project.name}"[:120]
    dst.mkdir(parents=True, exist_ok=True)
    for f in DECISIONS:
        if (project / f).is_file():
            shutil.copy2(project / f, dst / f)
    return str(dst)


def _subtitle_stats(project: Path) -> dict:
    p = project / "clipkit_punch.json"
    if not p.is_file():
        return {}
    leads, punches, looks = [], [], {}
    for items in json.loads(p.read_text(encoding="utf-8")).values():
        for it in items:
            opts = next((x for x in it[2:] if isinstance(x, dict)), {}) or {}
            w, c = opts.get("show") or (it[0], it[1])
            look = opts.get("look", "pair")
            looks[look] = looks.get(look, 0) + 1
            if look != "skip":
                if w:
                    leads.append(len(w.replace(" ", "")))
                if c:
                    punches.append(len(c.replace(" ", "")))
    stat = lambda xs: {"n": len(xs), "min": min(xs), "max": max(xs), "avg": round(sum(xs) / len(xs), 1)} if xs else {}  # noqa: E731
    return {"normal_letters": stat(leads), "emphasis_letters": stat(punches), "looks": looks}


def log_run(raw: str, story: dict, n: int, project: str, args: dict, length: float) -> None:
    """Called by run_clip.py for every clip it finishes."""
    proj = Path(project)
    hook = proj / "clipkit_hook.json"
    style = json.loads((proj / "clipkit_style.json").read_text(encoding="utf-8")) if (proj / "clipkit_style.json").is_file() else {}
    draft = proj / "draft_content.json"
    cuts = None
    if draft.is_file():
        d = json.loads(draft.read_text(encoding="utf-8"))
        cuts = max(0, len(next((t["segments"] for t in d["tracks"] if t["type"] == "video"), [])) - 1)
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "version": version(), "video": Path(raw).name, "story": n,
           "title": story.get("title"), "range_s": [story.get("start"), story.get("end")], "length_s": length,
           "preset": style.get("preset"), "look": style.get("anim"), "args": args, "cuts": cuts,
           "hook": json.loads(hook.read_text(encoding="utf-8")) if hook.is_file() else None,
           "subtitles": _subtitle_stats(proj), "snapshot": _snapshot(proj, "run")}
    with open(logs_dir() / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def feedback(project: str, said: str, fix: str) -> None:
    proj = Path(project)
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "version": version(), "project": proj.name, "said": said,
           "fix": fix, "subtitles": _subtitle_stats(proj), "snapshot": _snapshot(proj, "feedback")}
    with open(logs_dir() / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"logged": True, "snapshot": row["snapshot"]}, ensure_ascii=False))


def report() -> None:
    d = logs_dir()
    out = Path.home() / "Desktop" / f"clipkit_report_{time.strftime('%Y%m%d-%H%M')}.zip"
    if not out.parent.is_dir():
        out = d.parent / out.name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d))
        z.writestr("machine.txt", f"version {version()}\n")
    print("Note: the report holds the spoken words of your clips (subtitles), no video.")
    print("Do not send it if the client's content must not leave this machine.")
    print(f"Report: {out}  ({out.stat().st_size // 1024} KB) - send this file back.")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    fb = sub.add_parser("feedback")
    fb.add_argument("project")
    fb.add_argument("said")
    fb.add_argument("--fix", default="")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "feedback":
        if not Path(a.project).is_dir():
            sys.exit(f"project folder not found: {a.project}")
        feedback(a.project, a.said, a.fix)
    else:
        report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
