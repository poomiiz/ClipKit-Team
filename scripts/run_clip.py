"""One command from a raw video to finished short clips, without the ClipKit window.

    python scripts/run_clip.py "<video>"                 # pilot: story 1 only
    python scripts/run_clip.py "<video>" --all           # every story
    python scripts/run_clip.py "<video>" --insert        # put the downloaded Envato clips in
    python scripts/run_clip.py "<video>" --all --export  # MP4 for every story

Steps: transcribe -> split into stories (max --max seconds) -> one project per story (crop on the face,
subtitles, silences trimmed, subtitle look, zoom cut) -> punch words and b-roll searches -> free Pixabay
b-roll -> cover -> HyperFrames page -> "<video> - ClipKit.html" with Envato search links -> MP4 on --export.

Two steps need judgement and are done by the AI agent (skill make-clip): splitting stories and picking punch
words. When one is missing the script prints `WAIT` with the command to run and exits 2; the agent does it
and runs the same command again. Every step already done is skipped, so rerunning is always safe.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from urllib.parse import quote

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="One command from a raw video to finished short clips.")
    ap.add_argument("video")
    ap.add_argument("--max", type=int, default=120, help="longest clip in seconds")
    ap.add_argument("--all", action="store_true", help="every story (default: story 1 only, as a pilot)")
    ap.add_argument("--shape", default="portrait", choices=["portrait", "landscape", "square", "source"])
    ap.add_argument("--look", default="pair", choices=["pair", "karaoke", "pop", "none"])
    ap.add_argument("--preset", default="default", help="subtitle preset in presets/ (normal + emphasis text)")
    ap.add_argument("--insert", action="store_true", help="use the clips downloaded into each clipkit_insert folder")
    ap.add_argument("--export", action="store_true", help="render the MP4s")
    ap.add_argument("--capcut", action="store_true", help="also lay the finished clip out as a CapCut project to keep editing")
    ap.add_argument("--client", help="with --capcut: check the CapCut project against this client's pace (presets/pace/<client>.json)")
    return ap


if __name__ == "__main__" and {"-h", "--help"} & set(sys.argv):
    _parser().parse_args()  # help works before config.json exists


def _self_update() -> None:
    """Team machines (a clone of ClipKit-Team) take the newest version before every run, so nobody has to type
    clipkit update. Offline, or local edits in the way: say so and run the version already here."""
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=60)  # noqa: E731
    try:
        if "ClipKit-Team" not in git("remote", "get-url", "origin").stdout or os.environ.get("CLIPKIT_UPDATED"):
            return
        if git("fetch", "-q").returncode != 0:
            print('{"update": "skipped: no connection to GitHub"}', flush=True)
            return
        if git("rev-parse", "HEAD").stdout == git("rev-parse", "@{u}").stdout:
            return
        changed = git("diff", "--name-only", "HEAD", "@{u}").stdout.split()
        if git("pull", "-q", "--ff-only").returncode != 0:
            print('{"update": "skipped: files in the ClipKit folder were changed by hand - run clipkit repair"}', flush=True)
            return
        if "requirements.txt" in changed:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(root / "requirements.txt")])
        print('{"update": "updated to the newest ClipKit - restarting"}', flush=True)
        os.environ["CLIPKIT_UPDATED"] = "1"
        sys.exit(subprocess.run([sys.executable, *sys.argv]).returncode)
    except (OSError, subprocess.SubprocessError) as exc:
        print('{"update": "skipped: ' + str(exc).replace('"', "'") + '"}', flush=True)


def _app_icon() -> None:
    """Team machines get the ClipKit desktop icon and the 12-hour update check on their first run."""
    import subprocess
    icon = Path(__file__).with_name("app_icon.ps1")
    desk = Path.home() / "Desktop" / "ClipKit.lnk"
    task = subprocess.run(["schtasks", "/Query", "/TN", "ClipKit update"], capture_output=True).returncode == 0
    if icon.is_file() and not (desk.exists() and task):
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(icon)],
                       capture_output=True, timeout=60)


if __name__ == "__main__":
    _self_update()
    _app_icon()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import capcut_edit  # noqa: E402
import hf_build  # noqa: E402
import kit_settings  # noqa: E402
import render  # noqa: E402
import cutlog  # noqa: E402  (scripts/cutlog.py: the cut log)
import video_edit  # noqa: E402
import video_editor as ve  # noqa: E402
from fastapi import HTTPException  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
sys.stdout.reconfigure(encoding="utf-8")  # Thai titles when the output goes to a file or another program


def say(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def detail(exc: Exception) -> str:
    return exc.detail if isinstance(exc, HTTPException) else str(exc)


def stories(raw: str, max_s: int) -> list[dict] | None:
    tf = video_edit.transcript_file(raw)
    if not tf.is_file():
        say(step="ถอดเสียงทั้งไฟล์")
        phrases = video_edit.transcribe(raw, 0, video_edit.probe(raw)["duration"], "th", None)
        tf.write_text(json.dumps(phrases, ensure_ascii=False, indent=1), encoding="utf-8")
    video_edit.STORY_MAX_S, video_edit.STORY_AIM_S = max_s, int(max_s * 0.65)
    return video_edit.stories_from_agent(raw, json.loads(tf.read_text(encoding="utf-8")))


def make_project(raw: str, n: int, st: dict, shape: str, look: str, preset: str = "default") -> str:
    f = video_edit.find_focus(raw, st["start"], st["end"])
    r = ve.capcut(ve.DraftRequest(file=raw, name=f"{Path(raw).stem} - {n} {st['title']}", start=st["start"], end=st["end"],
                                  via="clipkit", shape=shape, focus_x=f["x"], focus_y=f["y"]))
    path = r["draft_path"]
    got = capcut_edit.transcribe_draft(path)
    capcut_edit.set_subtitles(path, got["subtitles"])
    capcut_edit.subtitles_language(path, "th")
    capcut_edit.trim_pauses(path)
    capcut_edit.tidy_subtitles(path)  # long lines: two lines at a Thai break, or a new subtitle
    (Path(path) / "clipkit_style.json").write_text(json.dumps({"anim": look, "preset": preset, "zoomcut": True, "skin": render.SKIN_DEFAULT}), encoding="utf-8")
    ve.auto_color(path)
    return path


def queries(path: str) -> list[str]:
    p = Path(path) / "clipkit_punch.json"
    seen = []
    for pairs in json.loads(p.read_text(encoding="utf-8")).values():
        for x in pairs:
            if len(x) > 2 and isinstance(x[2], str) and x[2] and x[2] not in seen:
                seen.append(x[2])
    return seen


def insert(path: str) -> int:
    """Files in <project>/clipkit_insert: a leading number picks the search it belongs to (1 = first link);
    files without one fill the remaining searches in download order."""
    box = Path(path) / "clipkit_insert"
    files = [f for f in box.glob("*") if f.suffix.lower() in VIDEO_EXT] if box.is_dir() else []
    if not files:
        return 0
    qs = queries(path)
    out = Path(path) / "clipkit_broll.json"
    chosen = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    numbered = {int(f.stem.split()[0].split("_")[0].split("-")[0]): f for f in files if f.stem.split()[0].split("_")[0].split("-")[0].isdigit()}
    rest = sorted((f for f in files if f not in numbered.values()), key=lambda f: f.stat().st_mtime)
    for i, q in enumerate(qs, 1):
        f = numbered.get(i) or (rest.pop(0) if rest else None)
        if f:
            chosen[q] = {"file": str(f), "source": "envato", "id": f.name, "thumb": "", "page": ""}
    out.write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    (Path(path) / "clipkit_placed.json").unlink(missing_ok=True)  # the export follows the new b-roll list
    return len(files)


def page(raw: str, rows: list[dict]) -> Path:
    out = Path(raw).with_name(Path(raw).stem + " - ClipKit.html")
    parts = []
    for r in rows:
        links = "".join(
            f'<li><a href="https://elements.envato.com/stock-video/{quote(q)}" target="_blank">{i}. {html.escape(q)}</a>'
            f'{" · มีแล้ว (Pixabay)" if q in r["have"] else ""}</li>' for i, q in enumerate(r["queries"], 1))
        parts.append(f"""<section><h2>{r['n']}. {html.escape(r['title'])} <small>{r['length']} วิ</small></h2>
<p><a href="{Path(r['hf']).as_uri()}">▶ ดูคลิป (HyperFrames)</a>{f' · <a href="{Path(r["mp4"]).as_uri()}">MP4</a>' if r.get('mp4') else ''}</p>
<p>โหลดคลิปจาก Envato ใส่โฟลเดอร์ <code>{html.escape(str(Path(r['path']) / 'clipkit_insert'))}</code>
ตั้งชื่อขึ้นต้นด้วยเลขลิงก์ (เช่น <code>1 coffee.mp4</code>) แล้วสั่ง <code>--insert</code></p><ol style="list-style:none;padding:0">{links}</ol></section>""")
    out.write_text(f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><title>{html.escape(Path(raw).stem)} · ClipKit</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;background:#111;color:#eee}}
a{{color:#8fb8ff}}section{{border:1px solid #333;border-radius:10px;padding:12px 16px;margin:12px 0}}small{{color:#999}}code{{color:#fc6}}</style>
</head><body><h1>{html.escape(Path(raw).name)}</h1>{''.join(parts)}</body></html>""", encoding="utf-8")
    return out


def main() -> int:
    a = _parser().parse_args()
    render.pair_look({"preset": a.preset})  # unknown or broken preset: stop before any work
    raw = str(Path(a.video).resolve())
    if not Path(raw).is_file():
        say(error="file not found", file=raw)
        return 1
    state_f = Path(raw).with_name(Path(raw).name + ".clipkit_run.json")
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.is_file() else {}

    sts = stories(raw, a.max)
    if sts is None:
        say(WAIT=video_edit.agent_command("stories", raw), then=" ".join(sys.argv))
        return 2
    picked = range(1, len(sts) + 1) if a.all else [1]
    rows, waits = [], []
    for n in picked:
        st = sts[n - 1]
        key = f"{st['start']:.2f}-{st['end']:.2f}"
        try:
            if key not in state or not Path(state[key]).is_dir():
                say(step="ทำโปรเจกต์", story=n, title=st["title"])
                state[key] = make_project(raw, n, st, a.shape, a.look, a.preset)
                state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            path = state[key]
            need = ["clipkit_punch.json", "clipkit_hook.json"]
            style = json.loads((Path(path) / "clipkit_style.json").read_text(encoding="utf-8"))
            if style.get("anim") == "pair" and render.pair_look(style)["caption"]:
                need.append("clipkit_caption.json")  # the preset has a small translated caption
            if not all((Path(path) / f).is_file() for f in need):
                waits.append(ve.draft_punch_request(ve.DraftPath(path=path))["command"])
                continue
            if a.insert:
                say(step="ใส่คลิปที่โหลดมา", story=n, files=insert(path))
            note = ""
            try:
                ve.broll_fill(path)
            except video_edit.VideoEditError as exc:  # no Pixabay key / offline: the Envato links still cover it
                note = str(exc)
            if not json.loads((Path(path) / "clipkit_style.json").read_text(encoding="utf-8")).get("cover"):
                ve.auto_cover(path)
            row = {"n": n, "title": st["title"], "path": path, "hf": str(hf_build.build(path)), "queries": queries(path),
                   "length": round(ve.draft_timeline(path)["duration"], 1), "pixabay": note}
            bf = Path(path) / "clipkit_broll.json"
            row["have"] = list(json.loads(bf.read_text(encoding="utf-8"))) if bf.is_file() else []
            if a.capcut:
                import capcut_build
                say(step="ทำโปรเจกต์ CapCut", story=n)
                cc, row["capcut_warnings"] = capcut_build.build(path)
                row["capcut"] = str(cc)
                if a.client:
                    sys.path.insert(0, str(ROOT / "scripts" / "capcut"))
                    import pace
                    row["pace"] = pace.check(cc, a.client)
                    say(pace=row["pace"]["score"], problems=row["pace"]["problems"], story=n)
            row["safe_zone"] = render.safe_zone(row.get("capcut") or path)
            if row["safe_zone"]:
                say(safe_zone=row["safe_zone"], story=n)
            if a.export:
                say(step="ส่งออก MP4", story=n)
                row["mp4"] = ve.draft_hf_export(ve.ExportRequest(path=path))["file"]
            rows.append(row)
            row["check"] = cutlog.log_run(raw, st, n, path, {k: v for k, v in vars(a).items() if k != "video"}, row["length"])
        except (video_edit.VideoEditError, HTTPException) as exc:
            say(error=detail(exc), story=n, title=st["title"])
            return 1
    if waits:
        say(WAIT=waits, then=" ".join(sys.argv))
        return 2
    say(done=str(page(raw, rows)), clips=[{k: r[k] for k in ("n", "title", "length", "path") if k in r} | {"mp4": r.get("mp4"), "capcut": r.get("capcut"), "capcut_warnings": r.get("capcut_warnings"), "safe_zone": r.get("safe_zone"), "check": r.get("check")}
                                         for r in rows], stories=len(sts), pilot=not a.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
