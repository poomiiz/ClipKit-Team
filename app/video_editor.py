"""Router: video_editor — scan a folder of footage, transcribe it, find the
pauses worth trimming, and hand the result to CapCut as a ready draft."""
from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import capcut_edit
import video_edit
from video_edit import VideoEditError


router = APIRouter(prefix="/api/video", tags=["video-editor"])
_transcribe_jobs: dict[str, subprocess.Popen[str]] = {}
_APP_DIR = Path(__file__).resolve().parent


class ScanRequest(BaseModel):
    path: str


class AnalyzeRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float | None = None
    threshold_db: int = Field(default=-33, ge=-60, le=-10)
    min_pause: float = Field(default=0.30, ge=0.1, le=3.0)
    keep: float = Field(default=0.25, ge=0.0, le=2.0)
    min_gain: float = Field(default=0.30, ge=0.05, le=3.0)


class TranscribeRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float | None = None
    language: str = "th"
    model: str | None = None


class SubtitleStyle(BaseModel):
    size: float | None = None
    y: float | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: float | None = None


class SubtitleIn(BaseModel):
    start: float
    end: float
    text: str
    style: SubtitleStyle | None = None


class CutIn(BaseModel):
    start: float
    end: float


class DraftRequest(BaseModel):
    file: str
    name: str
    start: float = 0.0
    end: float | None = None
    cuts: list[CutIn] = Field(default_factory=list)
    subs: list[SubtitleIn] = Field(default_factory=list)
    sub_size: int = Field(default=18, ge=6, le=60)
    sub_y: float = Field(default=-0.60, ge=-1.0, le=1.0)
    sub_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    sub_stroke: float = Field(default=0.05, ge=0.0, le=0.3)
    via: str = Field(default="capcut", pattern="^(clipkit|capcut)$")
    shape: str = Field(default="source", pattern="^(source|portrait|landscape|square)$")
    focus_x: float = Field(default=0.5, ge=0, le=1)  # where to keep in frame when cropping (0 left, 1 right)
    focus_y: float = Field(default=0.5, ge=0, le=1)  # which button made it: finish in ClipKit or in CapCut


class BriefRequest(BaseModel):
    path: str


class BrowseRequest(BaseModel):
    path: str | None = None


class DraftPath(BaseModel):
    path: str


class DraftSubsRequest(BaseModel):
    path: str
    subs: list[SubtitleIn]
    size: float | None = None
    y: float | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: float | None = None
    replace: bool = True


class FillGapsRequest(BaseModel):
    path: str
    min_gap: float = Field(default=1.0, ge=0.3, le=10.0)
    preview: bool = False


class DraftStyleRequest(BaseModel):
    path: str
    size: float | None = None
    y: float | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: float | None = None


class DraftTrimRequest(BaseModel):
    path: str
    keep: float = Field(default=0.25, ge=0.0, le=2.0)
    min_gain: float = Field(default=0.30, ge=0.05, le=3.0)
    apply: bool = True


class GradeRequest(BaseModel):
    source: str
    target: str


class AnimationRequest(BaseModel):
    path: str
    name: str
    target: str = Field(default="text", pattern="^(text|video)$")
    duration: float | None = Field(default=None, ge=0.1, le=5.0)


class AnimationClearRequest(BaseModel):
    path: str
    target: str = Field(default="text", pattern="^(text|video)$")


class MusicRequest(BaseModel):
    path: str
    music: str
    volume: float = Field(default=0.10, ge=0.0, le=1.0)
    fade_out: float = Field(default=5.0, ge=0.0, le=10.0)
    start: float = Field(default=0.0, ge=0.0)
    fade_in: float | None = Field(default=None, ge=0.0, le=10.0)


class SoundRequest(BaseModel):
    path: str
    sound: str = "sfx_pop"
    volume: float = Field(default=0.35, ge=0.0, le=2.0)
    every_line: bool = True
    offset: float = Field(default=0.0, ge=-1.0, le=1.0)


class TranslateRequest(BaseModel):
    lines: list[str]
    target: str = "en"


class EmphasisRequest(BaseModel):
    lines: list[str]
    ratio: float = Field(default=0.25, ge=0.05, le=0.8)


class TermsRequest(BaseModel):
    lines: list[str]
    per_line: int = Field(default=2, ge=1, le=4)


class StockSearchRequest(BaseModel):
    query: str
    count: int = Field(default=4, ge=1, le=8)
    kind: str = Field(default="stock-video")


def _clip_end(file: str, end: float | None) -> float:
    return float(end) if end is not None else video_edit.probe(file)["duration"]


@router.post("/scan")
def scan(req: ScanRequest) -> dict[str, Any]:
    try:
        files = video_edit.scan_folder(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": req.path, "count": len(files), "files": files}


@router.post("/projects/plan")
def project_plan(req: ScanRequest) -> dict[str, Any]:
    try:
        plans = video_edit.suggest_projects(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(plans), "plans": plans}


@router.post("/projects/transcribe")
def start_project_transcription(req: ScanRequest) -> dict[str, Any]:
    folder = Path(req.path).resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="folder not found")
    key = str(folder).lower()
    active = _transcribe_jobs.get(key)
    if active and active.poll() is None:
        raise HTTPException(status_code=409, detail="bot is already transcribing this project")
    output = folder / "transcripts"
    output.mkdir(parents=True, exist_ok=True)
    log = (output / "bot.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(_APP_DIR / "transcribe_folder.py"), str(folder)],
        cwd=str(_APP_DIR), stdout=log, stderr=subprocess.STDOUT, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _transcribe_jobs[key] = process
    return {"status": "running", "takes": len(video_edit.scan_folder(str(folder)))}


@router.get("/projects/transcribe/status")
def project_transcription_status(path: str = Query(...)) -> dict[str, Any]:
    folder = Path(path).resolve()
    key = str(folder).lower()
    process = _transcribe_jobs.get(key)
    output = folder / "transcripts"
    completed = len([file for file in output.glob("*.json") if file.name != "status.json"]) if output.is_dir() else 0
    return {"status": "running" if process and process.poll() is None else "idle",
            "completed": completed, "takes": len(video_edit.scan_folder(str(folder)))}


@router.get("/projects/transcribe/results")
def project_transcription_results(path: str = Query(...)) -> dict[str, Any]:
    output = Path(path).resolve() / "transcripts"
    results = []
    for file in sorted(output.glob("*.json")) if output.is_dir() else []:
        if file.name == "status.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        results.append({"file": file.stem, "source": data.get("source"),
                        "duration": data.get("duration"), "review": data.get("review", {}),
                        "markdown": file.with_suffix(".md").read_text(encoding="utf-8") if file.with_suffix(".md").is_file() else ""})
    return {"results": results}


@router.post("/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    try:
        end = _clip_end(req.file, req.end)
        pauses = video_edit.detect_pauses(req.file, req.start, end,
                                          req.threshold_db, req.min_pause)
        cuts = video_edit.suggest_cuts(pauses, req.keep, req.min_gain)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    removed = round(sum(c["gain"] for c in cuts), 2)
    length = round(end - req.start, 2)
    return {
        "length": length,
        "pauses": pauses,
        "cuts": cuts,
        "removed": removed,
        "result_length": round(length - removed, 2),
        "cuts_per_10s": round(10 * len(cuts) / max(length - removed, 0.1), 2),
    }


@router.post("/transcribe")
def transcribe(req: TranscribeRequest) -> dict[str, Any]:
    try:
        end = _clip_end(req.file, req.end)
        phrases = video_edit.transcribe(req.file, req.start, end, req.language, req.model)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # model load / runtime problems must surface, not hide
        raise HTTPException(status_code=500, detail=f"transcribe failed: {exc}") from exc
    return {"count": len(phrases), "phrases": phrases}


class StoryPlanRequest(BaseModel):
    file: str


_story_jobs: dict[str, dict[str, Any]] = {}


def _run_story_plan(file: str) -> None:
    """Transcribe once and save <file>.transcript.json; the agent (Claude / Codex with the ClipKit skill)
    reads it and writes <file>.stories.json, which the status call below picks up."""
    job = _story_jobs[file]
    try:
        tf = video_edit.transcript_file(file)
        if tf.is_file():
            phrases = json.loads(tf.read_text(encoding="utf-8"))
        else:
            duration = video_edit.probe(file)["duration"]
            job["step"] = "ถอดเสียง"
            phrases = video_edit.transcribe(file, 0, duration, "th", None)  # one model: video_edit.WHISPER_MODEL
            tf.write_text(json.dumps(phrases, ensure_ascii=False, indent=1), encoding="utf-8")
        job.update(status="waiting", step="รอ Claude แบ่งเรื่อง", transcript=str(tf),
                   command=video_edit.agent_command("stories", file),
                   duration=round(phrases[-1]["end"] if phrases else 0, 1),
                   speech=round(sum(p["end"] - p["start"] for p in phrases), 1))
    except Exception as exc:  # surface every failure to the page
        job.update(status="error", error=str(exc))


@router.post("/story-plan")
def story_plan(req: StoryPlanRequest) -> dict[str, Any]:
    """Transcribe a whole file and split it into stories before choosing CapCut or HyperFrames."""
    import threading
    if not Path(req.file).is_file():
        raise HTTPException(status_code=400, detail="file not found")
    job = _story_jobs.get(req.file)
    if not job or job["status"] == "error":
        _story_jobs[req.file] = {"status": "running", "step": "เริ่ม"}
        threading.Thread(target=_run_story_plan, args=(req.file,), daemon=True).start()
    return _story_jobs[req.file]


@router.get("/story-plan")
def story_plan_status(file: str = Query(...)) -> dict[str, Any]:
    job = _story_jobs.get(file)
    if not job:
        raise HTTPException(status_code=404, detail="no plan started for this file")
    if job["status"] == "waiting":
        try:
            phrases = json.loads(Path(job["transcript"]).read_text(encoding="utf-8"))
            stories = video_edit.stories_from_agent(file, phrases)
        except VideoEditError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if stories is not None:
            job.update(status="done", stories=stories)
    return job


@router.post("/capcut")
def capcut(req: DraftRequest) -> dict[str, Any]:
    try:
        end = _clip_end(req.file, req.end)
        result = video_edit.create_capcut_draft(
            video_path=req.file,
            project_name=req.name,
            clip_in=req.start,
            clip_out=end,
            cuts=[c.model_dump() for c in req.cuts],
            subs=[s.model_dump() for s in req.subs],
            sub_size=req.sub_size,
            sub_y=req.sub_y,
            sub_color=req.sub_color,
            sub_stroke=req.sub_stroke,
            shape=req.shape,
            focus=(req.focus_x, req.focus_y),
        )
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # which raw file this project came from: the sidebar groups projects under it
    (Path(result["draft_path"]) / "clipkit.json").write_text(
        json.dumps({"raw": req.file, "start": req.start, "end": end, "via": req.via}, ensure_ascii=False), encoding="utf-8")
    return result


class FocusRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float | None = None


@router.post("/focus")
def focus(req: FocusRequest) -> dict[str, Any]:
    """Where the speaker's face is, to place the crop."""
    try:
        return video_edit.find_focus(req.file, req.start, req.end)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/browse")
def browse(req: BrowseRequest) -> dict[str, Any]:
    """Folder picker data: drives, subfolders, and video counts."""
    try:
        return video_edit.browse(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/subtitle-presets")
def subtitle_presets() -> dict[str, Any]:
    """Subtitle presets (normal + emphasis text) in presets/, made by each user."""
    import render
    return {"presets": render.preset_names()}


@router.get("/presets")
def presets() -> dict[str, Any]:
    """Colour grade and subtitle styling taken from the projects already cut."""
    try:
        return video_edit.extract_presets()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/briefs")
def briefs(req: BriefRequest) -> dict[str, Any]:
    """Per-clip notes of a 'For Cutting' style folder (hook line, footage)."""
    try:
        items = video_edit.read_briefs(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "briefs": items}


@router.get("/frame")
def frame(path: str = Query(...), t: float = Query(1.0), width: int = Query(360)) -> Response:
    """A still from the clip, used as the backdrop of the subtitle preview."""
    try:
        data = video_edit.grab_frame(path, t, width)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=300"})


@router.get("/drafts")
def drafts() -> dict[str, Any]:
    """CapCut projects already on this machine."""
    try:
        items = capcut_edit.list_drafts()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for d in items:  # made by the ClipKit button, or a CapCut project (any made before the buttons split)
        meta = Path(d["path"]) / "clipkit.json"
        d["via"] = json.loads(meta.read_text(encoding="utf-8")).get("via", "capcut") if meta.is_file() else "capcut"
    return {"count": len(items), "drafts": items}


@router.post("/draft")
def draft(req: DraftPath) -> dict[str, Any]:
    try:
        return capcut_edit.read_draft(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/transcribe")
def draft_transcribe(req: DraftPath) -> dict[str, Any]:
    """Subtitles for an existing project, timed to its own timeline."""
    try:
        return capcut_edit.transcribe_draft(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"transcribe failed: {exc}") from exc


class ExportRequest(BaseModel):
    path: str
    preview: bool = False  # quick half-size file for review before the real export


@router.post("/draft/export")
def draft_export(req: ExportRequest) -> dict[str, Any]:
    """MP4 straight from ClipKit (no CapCut): <output_dir>/exports/<project>.mp4"""
    import kit_settings
    import render
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root")  # same place covers and motion files go
    if not out:
        raise HTTPException(400, "ยังไม่ได้ตั้งที่เก็บไฟล์ส่งออก — ไปที่ ตั้งค่า > โฟลเดอร์")
    try:
        return render.render_draft(req.path, str(Path(out) / "exports"), preview=req.preview)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/subs")
def draft_subs(req: DraftSubsRequest) -> dict[str, Any]:
    try:
        return capcut_edit.set_subtitles(
            req.path, [s.model_dump() for s in req.subs], size=req.size, y=req.y,
            color=req.color, stroke=req.stroke, replace=req.replace)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/subs/fill")
def draft_subs_fill(req: FillGapsRequest) -> dict[str, Any]:
    """Subtitle only the stretches that have none, for footage added later."""
    try:
        return capcut_edit.fill_subtitle_gaps(req.path, min_gap=req.min_gap,
                                              preview=req.preview)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SubsLangRequest(BaseModel):
    path: str
    lang: str = Field(pattern="^(th|en)$")


@router.post("/draft/subs-lang")
def draft_subs_lang(req: SubsLangRequest) -> dict[str, Any]:
    try:
        return capcut_edit.subtitles_language(req.path, req.lang)
    except capcut_edit.NeedsAgent as exc:
        raise HTTPException(status_code=409, detail={"command": str(exc)}) from exc
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class TextPlace(BaseModel):
    id: str
    x: float = Field(ge=-1.5, le=1.5)
    y: float = Field(ge=-1.5, le=1.5)
    rotation: float = Field(default=0.0, ge=-180, le=180)
    size: float | None = Field(default=None, ge=2, le=60)


class TextLayoutRequest(BaseModel):
    path: str
    items: list[TextPlace]


@router.post("/draft/layout")
def draft_layout(req: TextLayoutRequest) -> dict[str, Any]:
    try:
        return capcut_edit.set_text_layout(req.path, [i.model_dump() for i in req.items])
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/style")
def draft_style(req: DraftStyleRequest) -> dict[str, Any]:
    try:
        return capcut_edit.restyle_subtitles(req.path, size=req.size, y=req.y,
                                             color=req.color, stroke=req.stroke)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/trim")
def draft_trim(req: DraftTrimRequest) -> dict[str, Any]:
    try:
        return capcut_edit.trim_pauses(req.path, keep=req.keep,
                                       min_gain=req.min_gain, apply=req.apply)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/grade")
def draft_grade(req: GradeRequest) -> dict[str, Any]:
    try:
        return capcut_edit.copy_grade(req.source, req.target)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/emphasis")
def emphasis(req: EmphasisRequest) -> dict[str, Any]:
    """First guess at which lines should use the emphasised style."""
    return {"lines": video_edit.suggest_emphasis(req.lines, req.ratio)}


@router.post("/terms")
def terms(req: TermsRequest) -> dict[str, Any]:
    """English stock-footage search terms for Thai subtitle lines."""
    try:
        return {"terms": video_edit.search_terms(req.lines, req.per_line)}
    except VideoEditError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/stock/search")
def stock_search(req: StockSearchRequest) -> dict[str, Any]:
    """Ask the browser bot to search Envato in the logged-in Chrome."""
    try:
        return video_edit.stock_search(req.query, req.count, req.kind)
    except VideoEditError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/stock/result/{job_id}")
def stock_result(job_id: int) -> dict[str, Any]:
    try:
        return video_edit.stock_result(job_id)
    except VideoEditError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/animations")
def animations() -> dict[str, Any]:
    """Animations already downloaded on this machine, read from real projects."""
    try:
        items = capcut_edit.list_animations()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "animations": items}


@router.post("/draft/animation")
def draft_animation(req: AnimationRequest) -> dict[str, Any]:
    try:
        return capcut_edit.apply_animation(req.path, req.name, req.target, req.duration)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/animation/clear")
def draft_animation_clear(req: AnimationClearRequest) -> dict[str, Any]:
    try:
        return capcut_edit.clear_animations(req.path, req.target)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/file")
def file(path: str = Query(...)) -> FileResponse:
    """Serve one music file so it can be auditioned in the page."""
    from pathlib import Path as _Path
    target = _Path(path)
    if not target.is_file() or target.suffix.lower() not in capcut_edit.AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"not an audio file: {path}")
    known = any(str(target).lower().startswith(str(_Path(d)).lower())
                for d in capcut_edit.music_dirs())
    if not known:
        raise HTTPException(status_code=403, detail="file is outside the music folders")
    return FileResponse(str(target))


@router.get("/music")
def music() -> dict[str, Any]:
    """Background music already owned, from the known folders."""
    try:
        items = capcut_edit.list_music()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "music": items}


@router.post("/draft/music")
def draft_music(req: MusicRequest) -> dict[str, Any]:
    try:
        return capcut_edit.add_music(req.path, req.music, req.volume,
                                     req.fade_out, req.start, req.fade_in)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sounds")
def sounds() -> dict[str, Any]:
    """Sound effects available locally, learned from existing projects."""
    try:
        items = capcut_edit.list_sounds()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "sounds": items}


@router.post("/draft/sound")
def draft_sound(req: SoundRequest) -> dict[str, Any]:
    try:
        return capcut_edit.add_sound_on_subtitles(req.path, req.sound, req.volume,
                                                  req.every_line, req.offset)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/sound/clear")
def draft_sound_clear(req: DraftPath) -> dict[str, Any]:
    try:
        return capcut_edit.clear_sounds(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/config")
def config() -> dict[str, Any]:
    return {
        "drafts_root": video_edit.kitconfig.CFG.get("capcut_drafts", ""),
        "template_draft": video_edit.CAPCUT_TEMPLATE_DRAFT or "(newest draft in folder)",
        "whisper_model": video_edit.WHISPER_MODEL,
        "whisper_cpu_fallback": video_edit.WHISPER_CPU_FALLBACK,
        "gpu": video_edit._has_cuda(),
        "local_llm": video_edit.LOCAL_LLM_URL,
    }


# --- motion on top of the clip: pick a subtitle line, pick a template, it renders and sits at that time ---
OVERLAYS = "clipkit_overlays.json"
MOTION_TEMPLATES = {"hook-title": "หัวคลิป", "sentence-pair": "คำเน้น"}


def _overlays(path: str) -> list[dict[str, Any]]:
    f = Path(path) / OVERLAYS
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def _split2(text: str) -> tuple[str, str]:
    """Two halves at the Thai word break that balances them (lead / punch, key / sub)."""
    from pythainlp.tokenize import word_tokenize
    w = word_tokenize(text.replace("\n", " "), keep_whitespace=True)
    if len(w) < 2:
        return text, ""
    i = min(range(1, len(w)), key=lambda k: abs(len("".join(w[:k])) - len("".join(w[k:]))))
    return "".join(w[:i]).strip(), "".join(w[i:]).strip()


@router.get("/draft/overlays")
def draft_overlays(path: str = Query(...)) -> dict[str, Any]:
    try:
        d = capcut_edit.read_draft(path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"subs": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in d["subtitles"]],
            "overlays": _overlays(path), "templates": MOTION_TEMPLATES}


class OverlayRequest(BaseModel):
    path: str
    template: str
    start: float = Field(ge=0)
    end: float
    text: str


@router.post("/draft/overlay")
def draft_overlay_add(req: OverlayRequest) -> dict[str, Any]:
    import kit_settings
    if req.template not in MOTION_TEMPLATES:
        raise HTTPException(400, f"unknown template: {req.template}")
    a, b = _split2(req.text)
    dur = round(min(6.0, max(3.0, req.end - req.start)), 2)
    params = ({"key": a, "sub": b, "duration": dur} if req.template == "hook-title"
              else {"pairs": [[0, dur, a, b, 0.8]]})
    name = f"{Path(req.path).name[:40]} {req.template} {int(req.start * 1000)}ms"
    mov = kit_settings.make_motion(kit_settings.MotionRequest(template=req.template, name=name, params=params))["file"]
    items = [o for o in _overlays(req.path) if abs(o["start"] - req.start) > 0.05]  # one motion per moment
    items.append({"file": mov, "start": req.start, "duration": dur, "template": req.template, "text": req.text})
    (Path(req.path) / OVERLAYS).write_text(json.dumps(sorted(items, key=lambda o: o["start"]), ensure_ascii=False,
                                                      indent=1), encoding="utf-8")
    return {"overlays": _overlays(req.path)}


class OverlayDelete(BaseModel):
    path: str
    start: float


@router.post("/draft/overlay-remove")
def draft_overlay_remove(req: OverlayDelete) -> dict[str, Any]:
    items = [o for o in _overlays(req.path) if abs(o["start"] - req.start) > 0.05]
    (Path(req.path) / OVERLAYS).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"overlays": items}


class SubAnimRequest(BaseModel):
    path: str
    anim: str = Field(pattern="^(none|pop|karaoke|pair)$")


@router.post("/draft/sub-anim")
def draft_sub_anim(req: SubAnimRequest) -> dict[str, Any]:
    """How subtitles move in the MP4 export: still, pop in, or the spoken word lit up."""
    f = Path(req.path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    style["anim"] = req.anim
    f.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    words = Path(req.path) / "clipkit_words.json"
    return {"anim": req.anim, "zoomcut": bool(style.get("zoomcut")), "has_word_times": words.is_file()}


@router.get("/draft/sub-anim")
def draft_sub_anim_get(path: str = Query(...)) -> dict[str, Any]:
    f = Path(path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    return {"anim": style.get("anim", "none"), "zoomcut": bool(style.get("zoomcut")),
            "has_word_times": (Path(path) / "clipkit_words.json").is_file()}


class ZoomCut(BaseModel):
    path: str
    on: bool


@router.post("/draft/zoomcut")
def draft_zoomcut(req: ZoomCut) -> dict[str, Any]:
    """Every other subtitle line punched in (an editing effect for the MP4 export)."""
    f = Path(req.path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    style["zoomcut"] = req.on
    f.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return {"zoomcut": req.on}


@router.post("/draft/punch-request")
def draft_punch_request(req: DraftPath) -> dict[str, Any]:
    """Lines for the agent to pick punch words from; the editor pastes the returned command into chat."""
    lines = [s["text"] for s in capcut_edit.read_draft(req.path)["subtitles"]]
    if not lines:
        raise HTTPException(400, "ยังไม่มีซับ — ถอดเสียงก่อน")
    (Path(req.path) / "clipkit_lines.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    done = Path(req.path) / "clipkit_punch.json"
    picked = json.loads(done.read_text(encoding="utf-8")) if done.is_file() else {}
    return {"command": video_edit.agent_command("punch", req.path), "lines": len(lines),
            "picked": sum(1 for t in lines if t in picked)}


class FreeSearch(BaseModel):
    query: str
    count: int = Field(default=6, ge=1, le=20)
    portrait: bool = True


@router.post("/stock/free-search")
def stock_free_search(req: FreeSearch) -> dict[str, Any]:
    """Pixabay hits (free for commercial use)."""
    import free_stock
    import kit_settings
    try:
        return free_stock.search(kit_settings._read_config(), req.query, req.count, req.portrait)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FreeDownload(BaseModel):
    source: str
    id: str
    file: str


@router.post("/stock/free-download")
def stock_free_download(req: FreeDownload) -> dict[str, Any]:
    import free_stock
    import kit_settings
    cfg = kit_settings._read_config()
    folder = cfg.get("stock_video") or cfg.get("output_dir") or cfg.get("work_root")
    if not folder:
        raise HTTPException(400, "ยังไม่ได้ตั้งโฟลเดอร์สต็อกวิดีโอ — ไปที่ ตั้งค่า > โฟลเดอร์")
    try:
        return {"path": free_stock.download(folder, req.source, req.id, req.file)}
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def broll_fill(path: str) -> dict[str, Any]:
    """Free b-roll for the phrases the agent tagged with a search (3rd item in clipkit_punch.json):
    the first Pixabay hit for each search is saved and listed in clipkit_broll.json."""
    import free_stock
    import kit_settings
    folder = Path(path)
    punch = folder / "clipkit_punch.json"
    queries = [p[2] for pairs in (json.loads(punch.read_text(encoding="utf-8")).values() if punch.is_file() else [])
               for p in pairs if len(p) > 2 and isinstance(p[2], str) and p[2]]  # 4th item = display options
    out_file = folder / "clipkit_broll.json"
    chosen = json.loads(out_file.read_text(encoding="utf-8")) if out_file.is_file() else {}
    if not queries:
        return {"broll": chosen, "not_found": [], "queries": 0}
    cfg = kit_settings._read_config()
    store = cfg.get("stock_video") or cfg.get("output_dir") or cfg.get("work_root")
    if not store:
        raise VideoEditError("ยังไม่ได้ตั้งโฟลเดอร์สต็อกวิดีโอ — ไปที่ ตั้งค่า > โฟลเดอร์")
    missing = []
    for q in queries:
        if q in chosen and Path(chosen[q]["file"]).is_file():
            continue
        hits = free_stock.search(cfg, q, 3)["items"]
        if not hits:
            missing.append(q)
            continue
        h = hits[0]
        chosen[q] = {"file": free_stock.download(store, h["source"], h["id"], h["file"]), "source": h["source"],
                     "id": h["id"], "thumb": h["thumb"], "page": h["url"]}
    out_file.write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"broll": chosen, "not_found": missing, "queries": len(queries)}


@router.post("/draft/broll-auto")
def draft_broll_auto(req: DraftPath) -> dict[str, Any]:
    try:
        return broll_fill(req.path)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


class BrollRemove(BaseModel):
    path: str
    query: str


@router.post("/draft/broll-remove")
def draft_broll_remove(req: BrollRemove) -> dict[str, Any]:
    f = Path(req.path) / "clipkit_broll.json"
    chosen = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    chosen.pop(req.query, None)
    f.write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"broll": chosen}


class AutoRequest(BaseModel):
    path: str
    look: str = Field(default="pair", pattern="^(pair|karaoke|pop|none)$")


@router.post("/draft/auto")
def draft_auto(req: AutoRequest) -> dict[str, Any]:
    """Every step in one go, ending in a quick preview for a person to review before the real export:
    subtitles (if none yet) -> silence trimmed -> subtitle look + zoom cut -> b-roll (where the agent asked)
    -> music + sound effects -> half-size preview MP4."""
    import kit_settings
    import render
    steps = []
    try:
        if not capcut_edit.read_draft(req.path)["subtitles"]:
            got = capcut_edit.transcribe_draft(req.path)
            capcut_edit.set_subtitles(req.path, got["subtitles"])
            capcut_edit.subtitles_language(req.path, "th")
            steps.append(f"ถอดเสียงใส่ซับ {got['count']} บรรทัด")
        t = capcut_edit.trim_pauses(req.path)
        steps.append("ตัดช่วงเงียบแล้ว")
        f = Path(req.path) / "clipkit_style.json"
        style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        style.setdefault("anim", req.look)
        style.setdefault("zoomcut", True)
        style.setdefault("skin", render.SKIN_DEFAULT)
        f.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
        b = broll_fill(req.path)
        steps.append(f"ภาพประกอบ {len(b['broll'])} จุด" if b["queries"] else "ภาพประกอบ: ยังไม่ได้ให้ Claude เลือกคำค้น")
        cfg = kit_settings._read_config()
        out = cfg.get("output_dir") or cfg.get("work_root")
        if not out:
            raise HTTPException(400, "ยังไม่ได้ตั้งที่เก็บไฟล์ส่งออก — ไปที่ ตั้งค่า > โฟลเดอร์")
        r = render.render_draft(req.path, str(Path(out) / "exports"), preview=True)
        style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        if not style.get("cover") or not Path(style["cover"]).is_file():
            steps.append("ทำปกจากชื่อเรื่องแล้ว" if auto_cover(req.path) else "")
        placed = Path(req.path) / "clipkit_placed.json"
        if not placed.is_file():  # what the tool chose becomes the list the editor reviews and changes
            placed.write_text(json.dumps({"broll": r["broll_items"],
                                          "music": {"file": r["music_file"], "volume": r["music_volume"]},
                                          "sfx": {"on": r["sfx_on"], "volume": r["sfx_volume"]}},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail="; ".join(steps + [str(exc)])) from exc
    return {"steps": steps, "preview": r, "trim": t, "agent_command": video_edit.agent_command("punch", req.path),
            "punch_picked": (Path(req.path) / "clipkit_punch.json").is_file()}



@router.get("/draft/placed")
def draft_placed(path: str = Query(...)) -> dict[str, Any]:
    """The b-roll / music / effects list the editor reviews, plus the subtitle lines to add a b-roll at."""
    f = Path(path) / "clipkit_placed.json"
    subs = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in capcut_edit.read_draft(path)["subtitles"]]
    return {"placed": json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None, "subs": subs}


class BrollItem(BaseModel):
    start: float = Field(ge=0)
    dur: float = Field(gt=0.2, le=10)
    file: str
    query: str = ""
    thumb: str = ""


class MusicPick(BaseModel):
    file: str = ""  # "" = no music
    volume: float = Field(default=0.12, ge=0, le=1)


class SfxPick(BaseModel):
    on: bool = True
    volume: float = Field(default=1.0, ge=0, le=2)


class PlacedRequest(BaseModel):
    path: str
    broll: list[BrollItem]
    music: MusicPick
    sfx: SfxPick
    reset: bool = False  # True = forget the edits, let the tool choose again


@router.post("/draft/placed")
def draft_placed_save(req: PlacedRequest) -> dict[str, Any]:
    f = Path(req.path) / "clipkit_placed.json"
    if req.reset:
        f.unlink(missing_ok=True)
        return {"placed": None}
    for it in req.broll:
        if not Path(it.file).is_file():
            raise HTTPException(400, f"ไม่พบไฟล์ภาพประกอบ: {it.file}")
    if req.music.file and not Path(req.music.file).is_file():
        raise HTTPException(400, f"ไม่พบไฟล์เพลง: {req.music.file}")
    data = {"broll": [b.model_dump() for b in sorted(req.broll, key=lambda b: b.start)],
            "music": req.music.model_dump(), "sfx": req.sfx.model_dump()}
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"placed": data}


@router.get("/draft/timeline")
def draft_timeline(path: str = Query(...)) -> dict[str, Any]:
    """Everything the export will put on screen and in the sound, as tracks for the timeline editor."""
    import render
    try:
        t = render.render_draft(path, "", dry=True)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    d = capcut_edit.read_draft(path)
    t["subs"] = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in d["subtitles"]]
    t["name"], t["source"] = d["name"], d["source"]
    import kit_settings
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root") or ""
    pv = Path(out) / "exports" / f"{d['name']} - ตัวอย่าง.mp4"
    t["preview_file"] = str(pv) if out and pv.is_file() else ""
    meta = Path(path) / "clipkit.json"
    t["via"] = json.loads(meta.read_text(encoding="utf-8")).get("via", "capcut") if meta.is_file() else "capcut"
    t["agent_command"] = video_edit.agent_command("punch", path)
    t["punch_picked"] = (Path(path) / "clipkit_punch.json").is_file()
    return t


class PhraseEdit(BaseModel):
    path: str
    line: str                  # the subtitle line this phrase belongs to (current text)
    pairs: list[list[str | dict[str, Any]]]  # every phrase of that line: [lead, punch, query?, options?]


@router.post("/draft/phrases")
def draft_phrases(req: PhraseEdit) -> dict[str, Any]:
    """Save the editor's split / words / b-roll search for one subtitle line. New words also change the line."""
    folder = Path(req.path)
    new = "".join(p[0] + p[1] for p in req.pairs)
    if "".join(new.split()) != "".join(req.line.split()):
        capcut_edit.set_line_text(req.path, req.line, " ".join((p[0] + " " + p[1]).strip() for p in req.pairs))
        line = " ".join((p[0] + " " + p[1]).strip() for p in req.pairs)
    else:
        line = req.line
    f = folder / "clipkit_punch.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    data[line] = req.pairs
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"line": line}


class LookRequest(BaseModel):
    path: str
    style: dict[str, Any]   # merged into clipkit_style.json (anim, zoomcut, color, ...)


@router.post("/draft/look")
def draft_look(req: LookRequest) -> dict[str, Any]:
    f = Path(req.path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    style.update(req.style)
    f.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return {"style": style}


def auto_color(path: str) -> dict[str, Any]:
    """A first colour pick from the footage itself, over 6 frames of the clip: white balance (grey-world, 60 %
    of the way, so warm indoor light stays a little warm), brightness towards a mid level, contrast up when the
    picture is flat, a little more colour. The temperature slider (warmth) stays the person's."""
    import subprocess as sp
    import numpy as np
    d = capcut_edit.read_draft(path)
    src, info = d["source"], video_edit.probe(d["source"])
    segs = capcut_edit._video_track(capcut_edit._load(Path(path)))["segments"]
    a = segs[0]["source_timerange"]["start"] / video_edit.US
    z = (segs[-1]["source_timerange"]["start"] + segs[-1]["source_timerange"]["duration"]) / video_edit.US
    px = []
    for k in range(6):
        r = sp.run([video_edit.FFMPEG, "-v", "error", "-ss", f"{a + (z - a) * (k + 0.5) / 6:.2f}", "-i", src, "-frames:v", "1",
                    "-vf", "scale=96:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        if r.returncode or not r.stdout:
            raise VideoEditError("อ่านสีของภาพไม่ได้: " + r.stderr.decode("utf-8", "replace")[-200:])
        px.append(np.frombuffer(r.stdout, np.uint8).reshape(-1, 3) / 255)
    p = np.concatenate(px)
    luma = p @ [0.299, 0.587, 0.114]
    mean = p.mean(0)
    gains = [round(float(np.clip(1 + (mean.mean() / m - 1) * 0.6, 0.85, 1.15)), 3) for m in mean]
    spread = float(np.percentile(luma, 95) - np.percentile(luma, 5))
    y = float(luma.mean())
    color = {"r": gains[0], "g": gains[1], "b": gains[2],
             # gentle on purpose: +12 % contrast / +10 % colour looked too strong on real footage
             "brightness": round(max(-0.12, min(0.12, 0.47 - y)) * 0.3, 3),
             "contrast": round(min(1.05, max(1.0, 0.75 / max(spread, 0.1))), 3), "saturation": 1.0, "warmth": 0.0}
    f = Path(path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    color["warmth"] = float((style.get("color") or {}).get("warmth", 0))
    style["color"] = color
    f.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return {"color": color, "measured_luma": round(y, 3), "measured_rgb": [round(float(x), 3) for x in mean],
            "spread": round(spread, 3)}


@router.post("/draft/auto-color")
def draft_auto_color(req: DraftPath) -> dict[str, Any]:
    try:
        return auto_color(req.path)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc



class LineTime(BaseModel):
    path: str
    line: str
    start: float = Field(ge=0)
    end: float


@router.post("/draft/line-time")
def draft_line_time(req: LineTime) -> dict[str, Any]:
    try:
        return capcut_edit.set_line_time(req.path, req.line, req.start, req.end)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


class HookLine(BaseModel):
    text: str
    color: str = Field(pattern="^(white|orange|red)$")


class HookEdit(BaseModel):
    path: str
    lines: list[HookLine] = Field(max_length=2)   # none = no title


@router.post("/draft/hook")
def draft_hook(req: HookEdit) -> dict[str, Any]:
    """The clip title (first 4 s), edited in the editor: 1-2 lines, each white / orange / red."""
    import render
    f = Path(req.path) / "clipkit_hook.json"
    lines = [{"text": x.text.strip(), "color": x.color} for x in req.lines if x.text.strip()]
    if not lines:
        f.unlink(missing_ok=True)
        return {"hook": []}
    old = f.read_text(encoding="utf-8") if f.is_file() else None
    f.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    try:
        return {"hook": render._hook(Path(req.path))}
    except VideoEditError as exc:
        if old is None:
            f.unlink()
        else:
            f.write_text(old, encoding="utf-8")
        raise HTTPException(400, str(exc)) from exc


class CaptionEdit(BaseModel):
    path: str
    line: str
    text: str


@router.post("/draft/caption")
def draft_caption(req: CaptionEdit) -> dict[str, Any]:
    """The small translated caption of one spoken line (empty = none for that line)."""
    f = Path(req.path) / "clipkit_caption.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    if req.text.strip():
        data[req.line] = req.text.strip()
    else:
        data.pop(req.line, None)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"lines": len(data)}


class CutRange(BaseModel):
    path: str
    start: float = Field(ge=0)
    end: float


UNDO_FILES = ("draft_content.json", "clipkit_words.json", "clipkit_placed.json")


@router.post("/draft/cut")
def draft_cut(req: CutRange) -> dict[str, Any]:
    """Cut a stretch of the talk out of the clip (subtitles inside it go too, later ones move up); undoable."""
    import shutil
    import time
    if req.end - req.start < 0.1:
        raise HTTPException(400, "ช่วงที่จะตัดสั้นเกินไป")
    folder = Path(req.path)
    undo = folder / "clipkit_undo" / (time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}")
    undo.mkdir(parents=True)
    for name in UNDO_FILES:
        if (folder / name).is_file():
            shutil.copy2(folder / name, undo / name)
    try:
        r = capcut_edit.trim_pauses(req.path, ranges=[(req.start, req.end)])
    except VideoEditError as exc:
        shutil.rmtree(undo)
        raise HTTPException(400, str(exc)) from exc
    gone = req.end - req.start
    placed = folder / "clipkit_placed.json"
    if placed.is_file():  # b-roll after the cut moves up with the talk; b-roll inside it goes with it
        pl = json.loads(placed.read_text(encoding="utf-8"))
        keep = []
        for b in pl.get("broll", []):
            if b["start"] >= req.end:
                b["start"] = round(b["start"] - gone, 2)
                keep.append(b)
            elif b["start"] + b["dur"] <= req.start:
                keep.append(b)
        pl["broll"] = keep
        placed.write_text(json.dumps(pl, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"length": r.get("result_length"), "undo": len(list((folder / "clipkit_undo").iterdir()))}


@router.post("/draft/uncut")
def draft_uncut(req: DraftPath) -> dict[str, Any]:
    """Undo the last cut made in the editor."""
    import shutil
    box = Path(req.path) / "clipkit_undo"
    steps = sorted(box.iterdir()) if box.is_dir() else []
    if not steps:
        raise HTTPException(400, "ไม่มีการตัดให้ย้อนแล้ว")
    for name in UNDO_FILES:
        if (steps[-1] / name).is_file():
            shutil.copy2(steps[-1] / name, Path(req.path) / name)
    shutil.rmtree(steps[-1])
    return {"undo": len(steps) - 1}


def auto_cover(path: str) -> str:
    """A cover from the story title on the frame where the speaker's face is biggest; kept in clipkit_style.json."""
    import re
    import kit_settings
    meta = json.loads((Path(path) / "clipkit.json").read_text(encoding="utf-8")) if (Path(path) / "clipkit.json").is_file() else {}
    raw = meta.get("raw") or capcut_edit.read_draft(path)["source"]
    start, end = meta.get("start") or 0, meta.get("end")
    f = video_edit.find_focus(raw, start, end, frames=8)
    title = re.sub(r"^.* - \d+ ", "", Path(path).name).strip() or Path(path).name
    l1, l2 = _split2(title) if len(title) > 14 else (title, "")
    r = kit_settings.make_cover(kit_settings.CoverRequest(source=raw, at=f.get("best_at", start + 1), l1=l1, l2=l2))
    sf = Path(path) / "clipkit_style.json"
    style = json.loads(sf.read_text(encoding="utf-8")) if sf.is_file() else {}
    style["cover"] = r["file"]
    sf.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return r["file"]


@router.post("/draft/auto-cover")
def draft_auto_cover(req: DraftPath) -> dict[str, Any]:
    try:
        return {"file": auto_cover(req.path)}
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc



def _hf_key(path: str) -> str:
    import base64
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode().rstrip("=")


@router.post("/draft/hf-build")
def draft_hf_build(req: DraftPath) -> dict[str, Any]:
    """Write the project as a HyperFrames composition; the editor's player shows it live."""
    import hf_build
    try:
        hf_build.build(req.path)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"url": f"/api/video/hfp/{_hf_key(req.path)}/index.html"}


@router.get("/hfp/{key}/{file:path}")
def hf_page(key: str, file: str) -> FileResponse:
    """The composition folder for the live player (index.html and its media/)."""
    import base64
    from urllib.parse import unquote
    root = (Path(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode("utf-8")) / "clipkit_hf").resolve()
    f = (root / unquote(file)).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404, "not in the composition")
    return FileResponse(f, headers={"Cache-Control": "no-store"} if f.suffix == ".html" else None)


@router.post("/draft/hf-export")
def draft_hf_export(req: ExportRequest) -> dict[str, Any]:
    """MP4 from the same composition the player shows (hyperframes render)."""
    import shutil
    import time as _t
    import hf_build
    import kit_settings
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root")
    if not out:
        raise HTTPException(400, "ยังไม่ได้ตั้งที่เก็บไฟล์ส่งออก — ไปที่ ตั้งค่า > โฟลเดอร์")
    page = hf_build.build(req.path)
    target = Path(out) / "exports" / f"{Path(req.path).name}{' - ตัวอย่าง' if req.preview else ''}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    npx = shutil.which("npx")
    t0 = _t.time()
    args = [npx, "--yes", f"hyperframes@{kit_settings.HF_VERSION}", "render", "--quiet", "--sdr", "--workers", "4", "-o", str(target)]  # SDR: social clips; HDR pre-extraction needs ~20 GB temp
    if req.preview:
        args += ["--quality", "draft"]
    tmp = Path(out) / "exports" / ".render-tmp"  # frames go next to the exports, not onto a nearly full C: drive
    tmp.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(args, cwd=str(page.parent), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1", "TEMP": str(tmp), "TMP": str(tmp)},
                       timeout=3600)
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0 or not target.is_file():
        raise HTTPException(500, "render failed: " + (r.stderr or r.stdout).strip()[-800:])
    return {"file": str(target), "seconds": round(_t.time() - t0, 1)}
