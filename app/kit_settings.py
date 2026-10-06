"""Settings API for the ClipKit app: machine check, setup, config folders, Envato login, update.

Local tool only (the app binds 127.0.0.1). Long jobs (setup, update, login) run in the background
and report through GET /api/kit/job/{name}.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

KIT = Path(__file__).resolve().parents[1]
CONFIG = KIT / "config.json"
EXAMPLE = KIT / "config.example.json"

# keys the settings page may edit; everything else in config.json is kept untouched
FOLDER_KEYS = ["work_root", "capcut_drafts", "stock_video", "stock_music", "sfx", "output_dir", "models_dir"]
TEXT_KEYS = ["card_font", "envato_backend", "whisper_device", "whisper_model", "bot_api_url", "workspace",
             "stock_source", "pixabay_key"]
# standard layout under one workspace folder: every machine in the team looks the same
WORKSPACE_LAYOUT = {"work_root": "footage", "stock_video": "stock\\video", "stock_music": "stock\\music",
                    "sfx": "sfx", "output_dir": "output", "models_dir": "models"}


def capcut_default_drafts() -> str:
    """CapCut's own default drafts folder on this Windows user, or '' if CapCut never created it."""
    import os
    d = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return str(d) if d.is_dir() else ""

router = APIRouter(prefix="/api/kit", tags=["kit-settings"])
_jobs: dict[str, dict[str, Any]] = {}


def _read_config() -> dict[str, Any]:
    src = CONFIG if CONFIG.is_file() else EXAMPLE
    return json.loads(src.read_text(encoding="utf-8-sig"))


def _run_job(name: str, cmd: list[str], cwd: Path = KIT) -> None:
    job = _jobs[name] = {"status": "running", "log": "", "started": time.time()}
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            job["log"] += line
        p.wait()
        job["status"] = "done" if p.returncode == 0 else "failed"
        job["code"] = p.returncode
    except Exception as exc:  # report, never swallow
        job["status"] = "failed"
        job["log"] += f"\n{exc}"


def _start(name: str, cmd: list[str]) -> dict[str, Any]:
    if _jobs.get(name, {}).get("status") == "running":
        raise HTTPException(409, f"{name} is already running")
    threading.Thread(target=_run_job, args=(name, cmd), daemon=True).start()
    return {"job": name, "status": "running"}


@router.get("/doctor")
def doctor(asr: bool = False) -> dict[str, Any]:
    """Run scripts/doctor.py and return one row per check."""
    cmd = [sys.executable, str(KIT / "scripts" / "doctor.py")] + (["--asr"] if asr else [])
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    rows = []
    for line in p.stdout.splitlines():
        state, rest = line[:4].strip(), line[5:]
        if state in ("OK", "FAIL", "WARN"):
            rows.append({"state": state, "name": rest[:18].strip(), "detail": rest[18:].strip()})
    if not rows:
        raise HTTPException(500, f"doctor produced no result: {p.stderr.strip()[-400:]}")
    return {"ok": p.returncode == 0, "checks": rows}


@router.get("/config")
def get_config() -> dict[str, Any]:
    cfg = _read_config()
    return {"exists": CONFIG.is_file(), "folders": {k: cfg.get(k, "") for k in FOLDER_KEYS},
            "options": {k: cfg.get(k, "") for k in TEXT_KEYS}, "layout": WORKSPACE_LAYOUT}


class ConfigUpdate(BaseModel):
    values: dict[str, str]


@router.post("/config")
def save_config(body: ConfigUpdate) -> dict[str, Any]:
    cfg = _read_config()
    bad = [k for k in body.values if k not in FOLDER_KEYS + TEXT_KEYS]
    if bad:
        raise HTTPException(400, f"unknown setting: {', '.join(bad)}")
    missing = [f"{k}={v}" for k, v in body.values.items() if k in FOLDER_KEYS and v and not Path(v).is_dir()]
    if missing:
        raise HTTPException(400, "folder not found: " + "; ".join(missing))
    cfg.update({k: v for k, v in body.values.items()})
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"saved": True, "note": "restart the app for scripts already running to pick up new paths"}


class Workspace(BaseModel):
    root: str


@router.post("/workspace")
def set_workspace(body: Workspace) -> dict[str, Any]:
    """Pick one workspace folder: create the standard subfolders and point every path at them."""
    root = Path(body.root.strip())
    if not body.root.strip() or not root.anchor:
        raise HTTPException(400, "choose a full folder path, e.g. D:\\ClipKit")
    if not Path(root.anchor).exists():
        raise HTTPException(400, f"drive not found: {root.anchor}")
    cfg = _read_config()
    paths = {k: str(root / sub) for k, sub in WORKSPACE_LAYOUT.items()}
    for p in paths.values():
        Path(p).mkdir(parents=True, exist_ok=True)
    cfg.update(paths)
    cfg["workspace"] = str(root)
    if not cfg.get("capcut_drafts") or not Path(cfg["capcut_drafts"]).is_dir():
        found = capcut_default_drafts()
        if found:
            cfg["capcut_drafts"] = found
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"saved": True, "paths": paths, "capcut_drafts": cfg.get("capcut_drafts", "")}


class HyperframesRequest(BaseModel):
    file: str
    name: str
    start: float | None = None  # one story out of a long file
    end: float | None = None
    resolution: str = "portrait"  # portrait (footage zoomed to fill 9:16), landscape, square


HF_VERSION = "0.8.101"
HF_PORT = 3002
_hf_preview: dict[str, Any] = {}


@router.post("/hyperframes")
def hyperframes_project(body: HyperframesRequest) -> dict[str, Any]:
    """Create a HyperFrames project from a raw clip under <output_dir>/hyperframes and open its Studio."""
    import re
    import shutil
    src = Path(body.file)
    if not src.is_file():
        raise HTTPException(400, f"file not found: {src}")
    if not shutil.which("npx"):
        raise HTTPException(400, "Node.js (npx) is not installed - run setup or install Node.js 22+")
    cfg = _read_config()
    base = cfg.get("output_dir") or cfg.get("work_root")
    if not base or not Path(base).is_dir():
        raise HTTPException(400, "set the workspace folder in Settings first")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", body.name).strip("-").lower() or "clip"
    root = Path(base) / "hyperframes"
    root.mkdir(parents=True, exist_ok=True)
    if body.resolution not in ("portrait", "landscape", "square"):
        raise HTTPException(400, f"unknown frame shape: {body.resolution}")
    if body.resolution != "portrait":  # each shape gets its own folder; the time range stays last
        slug = f"{slug}-{body.resolution}"
    if body.start is not None and body.end is not None:
        slug = f"{slug}-{int(body.start)}-{int(body.end)}"
    project = root / slug
    if not project.exists() and body.start is not None and body.end is not None:
        from video_edit import FFMPEG
        part = root / f"{slug}-source{src.suffix}"
        cut = subprocess.run([FFMPEG, "-y", "-ss", str(body.start), "-to", str(body.end), "-i", str(src),
                              "-c", "copy", str(part)], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if cut.returncode != 0 or not part.is_file():
            raise HTTPException(500, "cutting the story failed: " + cut.stderr.strip()[-400:])
        src = part
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    npx = shutil.which("npx")
    if not project.exists():
        p = subprocess.run([npx, "--yes", f"hyperframes@{HF_VERSION}", "init", slug, "--example", "blank",
                            "--video", str(src), "--resolution", body.resolution, "--skip-transcribe", "--non-interactive"],
                           cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode != 0 or not (project / "index.html").is_file():
            raise HTTPException(500, "hyperframes init failed: " + (p.stderr or p.stdout).strip()[-600:])
        if src.name.endswith("-source" + src.suffix) and src.parent == root:
            src.unlink()  # init copied the cut into the project; the loose copy would outlive a deleted project
    (project / "clipkit.json").write_text(json.dumps({"raw": body.file, "start": body.start, "end": body.end},
                                                     ensure_ascii=False), encoding="utf-8")
    _stop_studio()
    _hf_preview["proc"] = subprocess.Popen(
        [npx, "--yes", f"hyperframes@{HF_VERSION}", "preview", "--port", str(HF_PORT), "--no-open"],
        cwd=str(project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import urllib.request
    for _ in range(60):  # wait until the studio answers
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{HF_PORT}/", timeout=1)
            break
        except Exception:
            time.sleep(1)
    else:
        raise HTTPException(500, f"HyperFrames studio did not start on port {HF_PORT}")
    return {"project": str(project), "url": f"http://localhost:{HF_PORT}/#project/{slug}"}


class CoverRequest(BaseModel):
    source: str            # an image, or a video (a frame is taken at `at` seconds)
    at: float = 1.0
    l1: str
    l2: str = ""
    c1: str = "#ff7d00"
    s1: str = "#ffffff"
    c2: str = "#ffffff"
    s2: str = "#111111"
    font: str = ""
    layout: str = "cover-a"


@router.post("/cover")
def make_cover(body: CoverRequest) -> dict[str, Any]:
    """Render a 1080x1920 cover PNG from a motion/cover-* template into <output_dir>/covers."""
    import re
    import shutil
    import tempfile
    src = Path(body.source)
    if not src.is_file():
        raise HTTPException(400, f"file not found: {src}")
    tpl = KIT / "motion" / body.layout
    if not (tpl / "index.html").is_file() or not body.layout.startswith("cover-"):
        raise HTTPException(400, f"unknown cover layout: {body.layout}")
    if not body.l1.strip():
        raise HTTPException(400, "headline (l1) is empty")
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "Node.js (npx) is not installed - run setup")
    cfg = _read_config()
    out_dir = Path(cfg.get("output_dir") or cfg.get("work_root") or "") / "covers"
    if not out_dir.parent.is_dir():
        raise HTTPException(400, "set the workspace folder in Settings first")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clipkit_cover_"))
    shutil.copytree(tpl, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("preview-bg*"))
    bg = work / "preview-bg.jpg"
    if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", str(bg)]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(body.at), "-i", str(src), "-frames:v", "1",
               "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", str(bg)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not bg.is_file():
        raise HTTPException(500, "could not read the background: " + p.stderr.strip()[-300:])
    page = (work / "index.html").read_text(encoding="utf-8")
    bad = [v for v in (body.c1, body.s1, body.c2, body.s2) if not re.fullmatch(r"#[0-9a-fA-F]{6}", v)]
    if bad:
        raise HTTPException(400, f"colour must look like #ff7d00: {bad}")
    cover = json.dumps({"l1": body.l1, "l2": body.l2, "bg": "preview-bg.jpg",
                        "c1": body.c1, "s1": body.s1, "c2": body.c2, "s2": body.s2, "font": body.font}, ensure_ascii=False)
    page, n = re.subn(r"const COVER = \{.*?\};", lambda _: f"const COVER = {cover};", page, count=1, flags=re.S)  # COVER spans several lines in the template
    if not n:
        raise HTTPException(500, "template has no COVER line")
    (work / "index.html").write_text(page, encoding="utf-8")
    frames = work / "_frames"
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    r = subprocess.run([npx, "--yes", f"hyperframes@{HF_VERSION}", "render", "--format", "png-sequence", "--quiet",
                        "-o", str(frames)], cwd=str(work), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    first = sorted(frames.glob("*.png"))[:1] if frames.is_dir() else []
    if r.returncode != 0 or not first:
        raise HTTPException(500, "cover render failed: " + (r.stderr or r.stdout).strip()[-500:])
    name = re.sub(r'[\\/:*?"<>|]+', "", body.l1).strip()[:40] or "cover"
    target = out_dir / f"{name}.png"
    shutil.copy2(first[0], target)
    shutil.rmtree(work, ignore_errors=True)
    return {"file": str(target)}


def motion_templates() -> dict[str, dict[str, Any]]:
    """Every motion/<name>/index.html that carries a <script id="clipkit-motion"> spec (label, fields, length).
    A new effect pushed to GitHub shows up on the motion page after 'update', with no code change."""
    import re
    out = {}
    for page in sorted((KIT / "motion").glob("*/index.html")):
        m = re.search(r'<script type="application/json" id="clipkit-motion">(.*?)</script>',
                      page.read_text(encoding="utf-8"), re.S)
        if not m:
            continue   # covers and work-in-progress templates
        try:
            spec = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            raise HTTPException(500, f"motion/{page.parent.name}: bad clipkit-motion JSON: {exc}")
        out[page.parent.name] = spec
    return out


@router.get("/library")
def library() -> dict[str, Any]:
    """Everything the motion library page shows: our own templates (ready to use) and the sorted HyperFrames
    registry (motion/library/hyperframes.json, rebuilt by scripts/catalog_hf.py)."""
    ours = [{"name": k, "title": v.get("label", k), "role": v.get("role", "ข้อความ"), "mood": v.get("mood", ""),
             "source": "clipkit"} for k, v in motion_templates().items()]
    ours += [{"name": d.name, "title": "ปก " + d.name.split("-")[-1].upper(), "role": "ปก", "mood": "", "source": "clipkit"}
             for d in sorted((KIT / "motion").glob("cover-*")) if (d / "index.html").is_file()]
    hf_file = KIT / "motion" / "library" / "hyperframes.json"
    hf = json.loads(hf_file.read_text(encoding="utf-8")) if hf_file.is_file() else []
    # making new templates is the owner's job: only a machine whose config.json has "creator": true
    # (set by hand, not from the Settings page) sees the create buttons
    return {"ours": ours, "hyperframes": hf, "can_create": bool(_read_config().get("creator"))}


@router.get("/motion-templates")
def list_motion_templates() -> dict[str, Any]:
    return {"templates": motion_templates()}


class MotionRequest(BaseModel):
    template: str
    name: str              # output file name (no extension)
    params: dict[str, Any]


@router.post("/motion")
def make_motion(body: MotionRequest) -> dict[str, Any]:
    """Render a motion template with the user's words to a transparent MOV in <output_dir>/motion."""
    import re
    import shutil
    import tempfile
    specs = motion_templates()
    if body.template not in specs:
        raise HTTPException(400, f"unknown motion template: {body.template}")
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "Node.js (npx) is not installed - run setup")
    cfg = _read_config()
    out_dir = Path(cfg.get("output_dir") or cfg.get("work_root") or "") / "motion"
    if not out_dir.parent.is_dir():
        raise HTTPException(400, "set the workspace folder in Settings first")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clipkit_motion_"))
    shutil.copytree(KIT / "motion" / body.template, work, dirs_exist_ok=True)
    page = (work / "index.html").read_text(encoding="utf-8")
    kit = json.dumps(body.params, ensure_ascii=False).replace("</", "<\\/")
    page, n = re.subn(r"<head>", lambda _: f"<head>\n<script>window.KIT = {kit};</script>", page, count=1)
    if not n:
        raise HTTPException(500, "template has no <head>")
    # the renderer reads the clip length from the HTML attributes, not from the script
    try:
        p = body.params
        how = specs[body.template].get("length", "duration")
        length = max(float(x[1]) for x in p["pairs"]) if how == "pairs" else float(p[how])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise HTTPException(400, f"missing or bad length in params: {exc}")
    if not 0.5 <= length <= 120:
        raise HTTPException(400, f"length must be 0.5-120 s, got {length}")
    page, n = re.subn(r'data-duration="[\d.]+"', f'data-duration="{length:g}"', page)
    if not n:
        raise HTTPException(500, "template has no data-duration")
    (work / "index.html").write_text(page, encoding="utf-8")
    name = re.sub(r'[\\/:*?"<>|]+', "", body.name).strip()[:40] or body.template
    target = out_dir / f"{name}.mov"
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    r = subprocess.run([npx, "--yes", f"hyperframes@{HF_VERSION}", "render", "--format", "mov", "--quiet",
                        "--workers", "4", "-o", str(target)], cwd=str(work), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=900)
    shutil.rmtree(work, ignore_errors=True)
    if r.returncode != 0 or not target.is_file():
        raise HTTPException(500, "motion render failed: " + (r.stderr or r.stdout).strip()[-500:])
    return {"file": str(target)}


class FramesRequest(BaseModel):
    source: str
    count: int = 6


_FRAME_DIR = Path(__import__("tempfile").gettempdir()) / "clipkit_cover_frames"


@router.post("/cover-frames")
def cover_frames(body: FramesRequest) -> dict[str, Any]:
    """Suggest cover frames: sample the clip, score each frame (sharp + bright enough + a face, bigger is better),
    return the best `count` spread across the clip."""
    import shutil
    try:
        import cv2
    except ImportError as exc:
        raise HTTPException(400, "opencv-python is not installed - run setup") from exc
    src = Path(body.source)
    if not src.is_file():
        raise HTTPException(400, f"file not found: {src}")
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        raise HTTPException(400, "cannot read this video")
    face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    samples = min(48, max(body.count * 4, total // int(fps)))  # about one per second, at most 48
    cands = []
    for i in range(samples):
        idx = int((i + 0.5) * total / samples)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        small = cv2.resize(frame, (360, int(360 * frame.shape[0] / frame.shape[1])))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
        bright = gray.mean()
        faces = face.detectMultiScale(gray, 1.15, 5, minSize=(40, 40))
        area = max((w * h for (_x, _y, w, h) in faces), default=0) / (gray.shape[0] * gray.shape[1])
        score = sharp * (1.0 if 60 < bright < 200 else 0.4) * (1 + 20 * area) * (1.0 if len(faces) else 0.5)
        cands.append((score, idx / fps, small))
    cap.release()
    if not cands:
        raise HTTPException(400, "no readable frames in this video")
    # best first, but at least ~1.5 s apart so the choices are different moments
    chosen = []
    for c in sorted(cands, key=lambda c: -c[0]):
        if all(abs(c[1] - k[1]) >= 1.5 for k in chosen):
            chosen.append(c)
        if len(chosen) == body.count:
            break
    shutil.rmtree(_FRAME_DIR, ignore_errors=True)
    _FRAME_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for n, (_s, t, img) in enumerate(sorted(chosen, key=lambda c: c[1])):
        f = _FRAME_DIR / f"f{n}_{t:.2f}.jpg"
        cv2.imwrite(str(f), img)
        out.append({"at": round(t, 2), "thumb": f"/api/kit/cover-frame/{f.name}"})
    return {"frames": out}


@router.get("/frame")
def frame(source: str, at: float = 0.0):
    """One 1080x1920 JPEG from a video (or the image itself) for the live cover preview."""
    from fastapi.responses import Response
    src = Path(source)
    if not src.is_file():
        raise HTTPException(404, "file not found")
    seek = [] if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") else ["-ss", str(max(at, 0))]
    p = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                        "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"], capture_output=True, timeout=60)
    if p.returncode != 0 or not p.stdout:
        raise HTTPException(500, "could not read a frame: " + p.stderr.decode(errors="replace")[-200:])
    return Response(p.stdout, media_type="image/jpeg")


VIDEO_TYPES = {".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska",
               ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
               ".aac": "audio/aac", ".flac": "audio/flac", ".png": "image/png", ".jpg": "image/jpeg"}  # audio too: music is auditioned on the review tab


@router.get("/media")
def media(path: str):
    """Play a local clip in the editor (FileResponse answers Range requests, so seeking works)."""
    from fastapi.responses import FileResponse
    p = Path(path)
    if p.suffix.lower() not in VIDEO_TYPES or not p.is_file():
        raise HTTPException(404, f"not a video file: {path}")
    return FileResponse(p, media_type=VIDEO_TYPES[p.suffix.lower()])


@router.get("/cover-frame/{name}")
def cover_frame(name: str):
    from fastapi.responses import FileResponse
    p = _FRAME_DIR / Path(name).name
    if not p.is_file():
        raise HTTPException(404, "frame not found")
    return FileResponse(str(p))


@router.get("/cover-file")
def cover_file(path: str):
    """Serve a rendered cover so the page can show it (only files under a covers folder)."""
    from fastapi.responses import FileResponse
    p = Path(path)
    if p.parent.name != "covers" or p.suffix.lower() != ".png" or not p.is_file():
        raise HTTPException(404, "not a cover file")
    return FileResponse(str(p))


@router.get("/capcut-detect")
def capcut_detect() -> dict[str, str]:
    return {"path": capcut_default_drafts()}


@router.post("/browse")
def browse(start: str = "") -> dict[str, str]:
    """Folder pick (settings): same Windows Open dialog; a picked file means its folder."""
    r = pick(start)
    return {"path": r["path"] if r["kind"] != "file" else str(Path(r["path"]).parent)}


@router.post("/browse-file")
def browse_file(start: str = "") -> dict[str, str]:
    """File pick (cover background): same Windows Open dialog; a folder is not an answer here."""
    r = pick(start)
    if r["kind"] == "folder":
        raise HTTPException(400, "เลือกไฟล์วิดีโอหรือรูป ไม่ใช่โฟลเดอร์")
    return {"path": r["path"]}


PICK_PS = r"""
# sharp text on scaled screens (otherwise Windows stretches a 96-dpi bitmap and it looks blurry)
Add-Type -Namespace W -Name U -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[void][W.U]::SetProcessDPIAware()
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'เลือกไฟล์วิดีโอ หรือเข้าไปในโฟลเดอร์แล้วกด Open เพื่อเลือกทั้งโฟลเดอร์'
$d.Filter = 'วิดีโอ|*.mov;*.mp4;*.mkv;*.m4v;*.avi|ทุกไฟล์|*.*'
$d.CheckFileExists = $false; $d.ValidateNames = $false
$d.FileName = 'เลือกโฟลเดอร์นี้'
if ($args[0]) { $d.InitialDirectory = $args[0] }
# a background process may not take the foreground, so own the dialog with a tiny topmost window that is
# actually shown: the dialog then opens above the browser instead of behind it
$f = New-Object System.Windows.Forms.Form
$f.TopMost = $true; $f.ShowInTaskbar = $false; $f.FormBorderStyle = 'None'; $f.Size = New-Object System.Drawing.Size(1,1)
$f.StartPosition = 'CenterScreen'; $f.Opacity = 0
$f.Show(); $f.Activate()
if ($d.ShowDialog($f) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.FileName }
"""


@router.post("/pick")
def pick(start: str = "") -> dict[str, str]:
    """Windows' own Open dialog: pick a video file, or open a folder and press Open to take the whole folder
    (the classic 'file name = select this folder' trick, since Windows has no single file-or-folder dialog)."""
    script = Path(tempfile_dir()) / "clipkit_pick.ps1"
    script.write_text(PICK_PS, encoding="utf-8-sig")   # BOM: Windows PowerShell reads Thai text correctly
    # CREATE_NO_WINDOW: no black console window behind the dialog (only the Open dialog shows)
    p = subprocess.run(["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                        "-File", str(script), start],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = p.stdout.strip()
    if not out:
        return {"path": "", "kind": ""}
    path = Path(out)
    if path.is_file():
        return {"path": str(path), "kind": "file"}
    folder = path if path.is_dir() else path.parent    # "เลือกโฟลเดอร์นี้" does not exist: its folder is the pick
    if not folder.is_dir():
        raise HTTPException(400, f"not found: {out}")
    return {"path": str(folder), "kind": "folder"}


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


class LocateRequest(BaseModel):
    name: str
    size: int = -1      # bytes for a file, -1 for a folder


@router.post("/locate")
def locate(body: LocateRequest) -> dict[str, Any]:
    """Find a file/folder dropped onto the page. Browsers hand the page only the name and size, never the
    path, so look for an exact name (+ size) match under the workspace folders. Several matches or none = say so."""
    cfg = _read_config()
    roots = [Path(cfg[k]) for k in ("work_root", "output_dir", "workspace", "stock_video") if cfg.get(k)]
    roots = [r for r in dict.fromkeys(roots) if r.is_dir()]
    if not roots:
        raise HTTPException(400, "set the workspace folder in Settings first")
    # Windows user folders people drag from (Desktop, Downloads...), searched only 2 levels deep:
    # Downloads can hold whole repos and a full walk there would take minutes
    home = Path.home()
    shallow = [home / d for d in ("Desktop", "Downloads", "Videos", "Documents") if (home / d).is_dir()]
    hits: list[str] = []
    for r in shallow:
        for pat in (body.name, "*/" + body.name, "*/*/" + body.name):
            for p in r.glob(pat):
                ok = p.is_dir() if body.size < 0 else (p.is_file() and p.stat().st_size == body.size)
                if ok and str(p) not in hits:
                    hits.append(str(p))
    for r in roots:
        for p in r.rglob(body.name):
            ok = p.is_dir() if body.size < 0 else (p.is_file() and p.stat().st_size == body.size)
            if ok and str(p) not in hits:
                hits.append(str(p))
            if len(hits) > 5:
                break
    if not hits:
        raise HTTPException(404, f"ไม่เจอ {body.name} ในโฟลเดอร์งาน, Desktop หรือ Downloads — กดช่องสร้างใหม่เพื่อเลือกไฟล์แทน")
    if len(hits) > 1:
        raise HTTPException(409, f"เจอ {body.name} หลายที่ ({len(hits)}) — กดปุ่มเลือกไฟล์แทน")
    p = Path(hits[0])
    return {"path": str(p), "kind": "folder" if p.is_dir() else "file"}


class DraftPath(BaseModel):
    path: str
    name: str = ""


def _project_roots() -> dict[str, Path]:
    cfg = _read_config()
    base = cfg.get("output_dir") or cfg.get("work_root") or ""
    return {"capcut": Path(cfg.get("capcut_drafts") or ""), "hyperframes": Path(base) / "hyperframes" if base else Path("")}


def _project(path: str) -> tuple[str, Path]:
    """Only folders directly inside the CapCut drafts folder or <output>/hyperframes may be opened, renamed or removed."""
    p = Path(path)
    for kind, root in _project_roots().items():
        marker = "draft_content.json" if kind == "capcut" else "index.html"
        if str(root) not in ("", ".") and root.is_dir() and p.parent.resolve() == root.resolve() and (p / marker).is_file():
            return kind, p
    raise HTTPException(400, f"not a ClipKit project folder: {path}")


def _stop_studio() -> None:
    # npx starts node underneath: kill the whole tree, or the studio keeps the project files open
    old = _hf_preview.get("proc")
    if old and old.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(old.pid)], capture_output=True)
        old.wait(timeout=10)
    # studios left from earlier runs (the old code stopped only npx, so node kept running and kept the files
    # open): stop every HyperFrames preview process, nothing else
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -match "
          "'hyperframes' -and $_.CommandLine -match ' preview ' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


def _raw_of(folder: Path, sources: list[str]) -> str:
    """The raw file a project came from: clipkit.json when ClipKit made it, else its biggest video source."""
    meta = folder / "clipkit.json"
    if meta.is_file():
        return json.loads(meta.read_text(encoding="utf-8"))["raw"]
    vids = [Path(s) for s in sources if Path(s).suffix.lower() in VIDEO_EXT and Path(s).is_file()]
    return str(max(vids, key=lambda p: p.stat().st_size)) if vids else ""


class BugReport(BaseModel):
    text: str
    page: str = ""
    errors: list[str] = []


@router.post("/bug-report")
def bug_report(body: BugReport) -> dict[str, Any]:
    """Keep the report on this machine and hand back a GitHub issue link with the same text."""
    import platform
    import urllib.parse
    if not body.text.strip():
        raise HTTPException(400, "เขียนอาการก่อน")
    v = version()
    report = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": body.text.strip(), "page": body.page,
              "version": v.get("version"), "commit": v.get("commit"), "machine": platform.node(),
              "errors": body.errors[-10:]}
    folder = KIT / "bug_reports"
    folder.mkdir(exist_ok=True)
    f = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    md = chr(10).join([report["text"], "", f"- page: {report['page']}",
                     f"- version: {report['version']} ({report['commit']})",
                     f"- machine: {report['machine']}", f"- time: {report['time']}", "", "errors:", "```",
                     *report["errors"], "```"])
    title = report["text"].splitlines()[0][:80]
    url = "https://github.com/poomiiz/ClipKit/issues/new?" + urllib.parse.urlencode({"title": "[bug] " + title, "body": md[:6000]})
    return {"saved": str(f), "issue_url": url}


@router.get("/raw-groups")
def raw_groups() -> dict[str, Any]:
    """Every project grouped under the raw file it was cut from (newest group first)."""
    import capcut_edit
    groups: dict[str, dict[str, Any]] = {}
    items = [("capcut", d) for d in capcut_edit.list_drafts()] + [("hyperframes", p) for p in hf_projects()["projects"]]
    for kind, d in items:
        raw = _raw_of(Path(d["path"]), d.get("sources") or [])
        key = str(Path(raw)).lower() if raw else ""
        g = groups.setdefault(key, {"raw": raw, "name": Path(raw).stem if raw else "ไม่ทราบไฟล์ดิบ",
                                    "exists": bool(raw) and Path(raw).is_file(), "modified": 0, "projects": []})
        g["projects"].append({"kind": kind, "name": d["name"], "path": d["path"], "modified": d.get("modified") or 0})
        g["modified"] = max(g["modified"], d.get("modified") or 0)
    out = sorted(groups.values(), key=lambda g: (g["raw"] == "", -g["modified"]))
    for g in out:
        g["projects"].sort(key=lambda x: -x["modified"])
    return {"groups": out}


def _draft_dir(path: str) -> Path:
    return _project(path)[1]


@router.get("/hf-projects")
def hf_projects() -> dict[str, Any]:
    root = _project_roots()["hyperframes"]
    out = []
    if root.is_dir():
        for d in root.iterdir():
            if (d / "index.html").is_file():
                out.append({"name": d.name, "path": str(d), "modified": int((d / "index.html").stat().st_mtime)})
    return {"projects": sorted(out, key=lambda x: -x["modified"])}


def _capcut_running() -> bool:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"], capture_output=True, text=True)
    return "CapCut.exe" in r.stdout


@router.post("/draft-rename")
def draft_rename(body: DraftPath) -> dict[str, str]:
    """Rename a project folder. CapCut keeps its own project list (root_meta_info.json), so CapCut must be closed
    and that list is updated too (a .bak copy is kept next to it)."""
    import re
    import shutil
    kind, p = _project(body.path)
    new = re.sub(r'[\/:*?"<>|]+', " ", body.name).strip()
    if not new:
        raise HTTPException(400, "ชื่อว่าง")
    target = p.parent / new
    if target.exists():
        raise HTTPException(409, f"มีโปรเจกต์ชื่อ {new} อยู่แล้ว")
    if kind == "capcut":
        if _capcut_running():
            raise HTTPException(409, "ปิด CapCut ก่อนเปลี่ยนชื่อ (CapCut จะเขียนรายชื่อโปรเจกต์ทับ)")
        sys.path.insert(0, str(KIT / "scripts" / "capcut"))
        import kitconfig
        rm = Path(kitconfig.ROOT_META)
        p.rename(target)
        meta = target / "draft_meta_info.json"
        if meta.is_file():
            m = json.loads(meta.read_text(encoding="utf-8"))
            m["draft_name"] = new
            m["draft_fold_path"] = target.as_posix()
            meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        if rm.is_file():
            shutil.copy2(rm, rm.with_suffix(".json.bak"))
            r = json.loads(rm.read_text(encoding="utf-8"))
            old_posix = p.as_posix()
            for e in r.get("all_draft_store", []):
                if Path(e.get("draft_fold_path", "")).resolve() == p.resolve():
                    e["draft_name"] = new
                    for k in ("draft_fold_path", "draft_json_file", "draft_cover"):
                        if isinstance(e.get(k), str):
                            e[k] = e[k].replace(old_posix, target.as_posix()).replace(str(p), str(target))
            rm.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    else:
        p.rename(target)
    return {"path": str(target), "name": new}


@router.post("/draft-open")
def draft_open(body: DraftPath) -> dict[str, str]:
    """Bring up CapCut (it opens on its project list; CapCut has no command line to open one project)."""
    import os
    kind, p = _project(body.path)
    if kind == "hyperframes":
        return _open_hf_studio(p)
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps" / "CapCut.exe"
    if not exe.is_file():
        raise HTTPException(400, f"CapCut not found at {exe}")
    subprocess.Popen([str(exe)])
    return {"opened": "CapCut"}


def _open_hf_studio(project: Path) -> dict[str, str]:
    import shutil
    import urllib.request
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "Node.js (npx) is not installed - run setup")
    _stop_studio()
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    _hf_preview["proc"] = subprocess.Popen([npx, "--yes", f"hyperframes@{HF_VERSION}", "preview", "--port", str(HF_PORT),
                                            "--no-open", "--foreground"], cwd=str(project), env=env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{HF_PORT}/", timeout=1)
            break
        except Exception:
            time.sleep(1)
    else:
        raise HTTPException(500, f"HyperFrames studio did not start on port {HF_PORT}")
    return {"opened": "hyperframes", "url": f"http://localhost:{HF_PORT}/#project/{project.name}"}


@router.post("/draft-delete")
def draft_delete(body: DraftPath) -> dict[str, str]:
    """Move one CapCut project folder to the Recycle Bin (restorable), never a permanent delete."""
    p = _draft_dir(body.path)
    import os
    if (p / "index.html").is_file():
        _stop_studio()
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
          "$env:CLIPKIT_TARGET, 'OnlyErrorDialogs', 'SendToRecycleBin')")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env={**os.environ, "CLIPKIT_TARGET": str(p)})
    if r.returncode != 0 or p.exists():
        err = (r.stderr or r.stdout).strip()
        if "IOException" in err or "being used" in err:
            raise HTTPException(409, f"ลบ {p.name} ไม่ได้ เพราะไฟล์ยังเปิดอยู่ (HyperFrames Studio หรือ CapCut) ปิดก่อนแล้วลองใหม่")
        raise HTTPException(500, "could not move to Recycle Bin: " + err[-300:])
    return {"recycled": str(p)}


@router.post("/setup")
def run_setup() -> dict[str, Any]:
    return _start("setup", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(KIT / "scripts" / "setup.ps1")])


@router.post("/envato-login")
def envato_login() -> dict[str, Any]:
    return _start("envato-login", [sys.executable, str(KIT / "scripts" / "envato.py"), "login"])


@router.post("/update")
def update() -> dict[str, Any]:
    if not (KIT / ".git").is_dir():
        raise HTTPException(400, "this copy is not a git clone - download a fresh copy from GitHub")
    return _start("update", ["git", "pull", "--ff-only"])


@router.get("/job/{name}")
def job(name: str) -> dict[str, Any]:
    if name not in _jobs:
        return {"job": name, "status": "idle", "log": ""}
    j = _jobs[name]
    return {"job": name, "status": j["status"], "log": j["log"][-6000:]}


@router.get("/fonts")
def fonts() -> dict[str, Any]:
    """Font families installed on this machine (what the browser and the renderer can use by name)."""
    ps = ("Add-Type -AssemblyName System.Drawing; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "(New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object { $_.Name }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    names = sorted({n.strip() for n in r.stdout.splitlines() if n.strip()}, key=str.lower)
    if not names:
        raise HTTPException(500, "could not read installed fonts: " + r.stderr.strip()[-200:])
    return {"fonts": names}


@router.get("/update-check")
def update_check() -> dict[str, Any]:
    """Ask GitHub whether a newer ClipKit exists (git fetch, then count commits we do not have yet)."""
    def git(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(KIT), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    f = git("fetch", "-q")
    if f.returncode != 0:
        return {"available": False, "error": (f.stderr or "git fetch failed").strip()[-200:]}
    behind = git("rev-list", "--count", "HEAD..@{u}")
    n = int(behind.stdout.strip() or 0) if behind.returncode == 0 else 0
    log = git("log", "--format=%s", "-8", "HEAD..@{u}").stdout.splitlines() if n else []
    return {"available": n > 0, "count": n, "changes": log}


@router.get("/version")
def version() -> dict[str, Any]:
    meta = json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    commit = subprocess.run(["git", "-C", str(KIT), "log", "-1", "--format=%h %cs"], capture_output=True,
                            text=True).stdout.strip()
    return {"version": meta.get("version"), "commit": commit}
