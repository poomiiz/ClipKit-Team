"""Work on CapCut projects that already exist: read a draft, put subtitles on
it, restyle them, trim its pauses, or copy a colour grade from another draft.

Every write backs the draft up first (draft_content.json.bak_<op>) and CapCut
must be closed while these run, otherwise it overwrites the file on exit.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import video_edit as _ve  # noqa: F401  (puts scripts/capcut on sys.path)
import kitconfig
from video_edit import (capcut_drafts_root, US, VideoEditError,
                                 detect_pauses, quiet_spans, suggest_cuts)

GRADE_KEYS = ("effects", "hsl", "color_curves")


def _drafts_root(root: str | None = None) -> Path:
    path = Path(root or capcut_drafts_root())
    if not path.is_dir():
        raise VideoEditError(f"CapCut drafts folder not found: {path}")
    return path


SEAL = "clipkit_seal.json"


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _seal(folder: Path, text: str) -> None:
    """Remember what ClipKit last wrote, so an edit made outside ClipKit is caught before ClipKit builds on it."""
    (folder / SEAL).write_text(json.dumps({"draft_content": _digest(text)}), encoding="utf-8")


def accept(folder: str) -> None:
    """The person changed the project on purpose (in CapCut): ClipKit carries on from that version."""
    f = Path(folder)
    _seal(f, (f / "draft_content.json").read_text(encoding="utf-8"))


def _load(folder: Path) -> dict[str, Any]:
    content = folder / "draft_content.json"
    if not content.is_file():
        raise VideoEditError(f"not a CapCut draft: {folder}")
    text = content.read_text(encoding="utf-8")
    seal = folder / SEAL
    if seal.is_file() and json.loads(seal.read_text(encoding="utf-8")).get("draft_content") != _digest(text):
        raise VideoEditError(
            f"โปรเจกต์ {folder.name} ถูกแก้นอก ClipKit (CapCut หรือ AI ตัวอื่นแก้ไฟล์ draft_content.json เอง) "
            "ClipKit จะไม่ทำต่อบนไฟล์นี้ เพราะอาจได้งานเพี้ยน: สร้างโปรเจกต์ใหม่จากไฟล์ดิบ หรือถ้าตั้งใจแก้ใน CapCut เอง "
            f'สั่ง  python scripts/accept_edit.py "{folder}"  แล้วทำต่อได้')
    return json.loads(text)


def sync_timeline(folder: Path, text: str) -> None:
    """CapCut 9.x keeps the open timeline in Timelines/<main_timeline_id>/draft_content.json as well as the
    project's own draft_content.json; it loads that copy, so every edit is written to both."""
    proj = folder / "Timelines" / "project.json"
    if proj.is_file():
        tid = json.loads(proj.read_text(encoding="utf-8")).get("main_timeline_id")
        inner = folder / "Timelines" / str(tid)
        if tid and inner.is_dir():
            (inner / "draft_content.json").write_text(text, encoding="utf-8")


def fresh_ids(folder: Path) -> str:
    """A cloned project gets its own timeline id (project.json, the Timelines/<id> folder, draft_content id):
    two projects sharing one id make CapCut open the other project's timeline or hang."""
    proj = folder / "Timelines" / "project.json"
    content = folder / "draft_content.json"
    new = str(uuid.uuid4()).upper()
    if proj.is_file():
        old = json.loads(proj.read_text(encoding="utf-8")).get("main_timeline_id")
        if old:
            proj.write_text(proj.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            if (folder / "Timelines" / old).is_dir():
                (folder / "Timelines" / old).rename(folder / "Timelines" / new)
    if content.is_file():
        draft = json.loads(content.read_text(encoding="utf-8"))
        draft["id"] = new
        text = json.dumps(draft, ensure_ascii=False, indent=2)
        content.write_text(text, encoding="utf-8")
        sync_timeline(folder, text)
        _seal(folder, text)
    return new


def _save(folder: Path, draft: dict[str, Any], op: str) -> None:
    content = folder / "draft_content.json"
    backup = content.with_suffix(f".json.bak_{op}")
    if not backup.exists():
        shutil.copy2(content, backup)
    text = json.dumps(draft, ensure_ascii=False, indent=2)
    content.write_text(text, encoding="utf-8")
    sync_timeline(folder, text)
    _seal(folder, text)
    meta_path = folder / "draft_meta_info.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if "tm_duration" in meta:
            meta["tm_duration"] = draft.get("duration", meta["tm_duration"])
            meta["tm_draft_modified"] = int(time.time() * US)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _text_track(draft: dict[str, Any]) -> dict[str, Any] | None:
    return next((t for t in draft["tracks"] if t["type"] == "text"), None)


def _video_track(draft: dict[str, Any]) -> dict[str, Any]:
    track = next((t for t in draft["tracks"] if t["type"] == "video"), None)
    if track is None or not track["segments"]:
        raise VideoEditError("draft has no video track")
    return track


def _index(draft: dict[str, Any]) -> dict[str, tuple[str, dict]]:
    index: dict[str, tuple[str, dict]] = {}
    for key, value in draft["materials"].items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "id" in item:
                    index[item["id"]] = (key, item)
    return index


def list_drafts(root: str | None = None) -> list[dict[str, Any]]:
    """Every draft in the folder with the numbers that matter for editing."""
    out = []
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        video = next((t for t in draft.get("tracks", []) if t["type"] == "video"), None)
        text = next((t for t in draft.get("tracks", []) if t["type"] == "text"), None)
        sources = {m.get("path") for m in draft.get("materials", {}).get("videos", [])}
        sources = {source for source in sources if isinstance(source, str) and Path(source).is_file()}
        out.append({
            "name": folder.name,
            "path": str(folder),
            "duration": round(draft.get("duration", 0) / US, 2),
            "segments": len(video["segments"]) if video else 0,
            "subtitles": len(text["segments"]) if text else 0,
            "has_grade": bool(draft.get("materials", {}).get("color_curves")),
            "sources": [s for s in sources if s],
            "modified": int((folder / "draft_content.json").stat().st_mtime),
        })
    return sorted(out, key=lambda d: -d["modified"])


def read_draft(path: str) -> dict[str, Any]:
    """Timeline of one draft: its cuts, its subtitles, and where the video came from."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    video = _video_track(draft)
    text = _text_track(draft)

    clips = [{
        "source_start": round(s["source_timerange"]["start"] / US, 2),
        "duration": round(s["source_timerange"]["duration"] / US, 2),
        "timeline_start": round(s["target_timerange"]["start"] / US, 2),
    } for s in video["segments"]]

    subs = []
    if text:
        for seg in sorted(text["segments"], key=lambda s: s["target_timerange"]["start"]):
            material = index.get(seg["material_id"], (None, None))[1]
            if not material:
                continue
            body = json.loads(material["content"])
            subs.append({
                "start": round(seg["target_timerange"]["start"] / US, 2),
                "end": round((seg["target_timerange"]["start"]
                              + seg["target_timerange"]["duration"]) / US, 2),
                "text": body.get("text", ""),
                "size": material.get("font_size"),
                "color": material.get("text_color"),
                "stroke": material.get("border_width"),
                "y": round(seg["clip"]["transform"]["y"], 3),
                "x": round(seg["clip"]["transform"].get("x", 0.0), 3),
                "rotation": round(seg["clip"].get("rotation", 0.0), 1),
                "id": seg["id"],
            })

    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    return {
        "name": folder.name,
        "path": str(folder),
        "duration": round(draft.get("duration", 0) / US, 2),
        "source": source,
        "clips": clips,
        "subtitles": subs,
    }


def timeline_to_source(draft: dict[str, Any]) -> list[tuple[float, float, float]]:
    """(timeline_start, timeline_end, source_start) for every clip on the track."""
    pieces = []
    for seg in _video_track(draft)["segments"]:
        tl_start = seg["target_timerange"]["start"] / US
        tl_dur = seg["target_timerange"]["duration"] / US
        src_start = seg.get("source_timerange", {}).get("start", 0) / US
        pieces.append((tl_start, tl_start + tl_dur, src_start))
    return pieces


def set_subtitles(path: str, subs: list[dict[str, Any]], size: float | None = None,
                  y: float | None = None, color: str | None = None,
                  stroke: float | None = None, replace: bool = True) -> dict[str, Any]:
    """Put a subtitle list onto an existing draft, keeping its styling unless
    new values are given. Times are on the draft's own timeline."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    if text is None:
        text = {"type": "text", "attribute": 0, "flag": 0, "segments": [],
                "id": str(uuid.uuid4()).upper(), "is_default_name": True}
        draft["tracks"].append(text)

    if text["segments"]:
        template_seg = copy.deepcopy(text["segments"][0])
        template_mat = copy.deepcopy(index[template_seg["material_id"]][1])
    else:
        # A project created straight from a video file has no subtitle yet;
        # borrow the look from any project that does rather than refusing.
        template_seg, template_mat = _borrow_text_style()
    keep = [] if replace else list(text["segments"])
    if replace:
        kept_ids = set()
    else:
        kept_ids = {s["material_id"] for s in keep}
    draft["materials"]["texts"] = [m for m in draft["materials"]["texts"]
                                   if m["id"] in kept_ids]

    limit = draft.get("duration", 0) / US
    made = []
    for sub in sorted(subs, key=lambda s: s["start"]):
        body_text = str(sub["text"]).strip()
        if not body_text:
            continue
        start = max(0.0, float(sub["start"]))
        end = min(float(sub["end"]), limit - 0.07)
        if end - start < 0.3:
            continue

        # a line may carry its own look (the emphasised ones do)
        own = sub.get("style") or {}
        line_size = own.get("size", size)
        line_color = own.get("color", color)
        line_stroke = own.get("stroke", stroke)
        line_y = own.get("y", y)

        material = copy.deepcopy(template_mat)
        material["id"] = str(uuid.uuid4()).upper()
        material["name"] = "Subtitle"
        body = json.loads(material["content"])
        body["text"] = body_text
        for style in body["styles"]:
            style["range"] = [0, len(body_text)]
            if line_size is not None:
                style["size"] = line_size
            if line_color:
                style["fill"]["content"]["solid"]["color"] = [
                    int(line_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            if line_stroke is not None:
                for line in style.get("strokes", []):
                    line["width"] = line_stroke
        material["content"] = json.dumps(body, ensure_ascii=False)
        if line_size is not None:
            material["font_size"] = float(line_size)
            material["text_size"] = line_size
        if line_color:
            material["text_color"] = line_color
        if line_stroke is not None:
            material["border_width"] = line_stroke
        draft["materials"]["texts"].append(material)

        segment = copy.deepcopy(template_seg)
        segment["id"] = str(uuid.uuid4()).upper()
        segment["material_id"] = material["id"]
        segment["target_timerange"] = {"start": int(round(start * US)),
                                       "duration": int(round((end - start) * US))}
        segment["clip"] = copy.deepcopy(template_seg["clip"])
        if line_y is not None:
            segment["clip"]["transform"] = {"x": 0.0, "y": line_y}
        segment["render_index"] = 14001
        made.append(segment)

    text["segments"] = sorted(keep + made, key=lambda s: s["target_timerange"]["start"])
    _save(folder, draft, "subs")
    return {"name": folder.name, "subtitles": len(text["segments"]), "added": len(made)}


def _borrow_text_style(root: str | None = None) -> tuple[dict, dict]:
    """A subtitle segment + material to model new subtitles on."""
    for folder in sorted(_drafts_root(root).iterdir(),
                         key=lambda f: -(f / "draft_content.json").stat().st_mtime
                         if (f / "draft_content.json").is_file() else 0):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            other = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        track = _text_track(other)
        if not track or not track["segments"]:
            continue
        index = _index(other)
        seg = copy.deepcopy(track["segments"][0])
        material = index.get(seg["material_id"], (None, None))[1]
        if material:
            return seg, copy.deepcopy(material)
    raise VideoEditError("no project on this machine has a subtitle to copy the "
                         "styling from — add one subtitle in CapCut once")


def restyle_subtitles(path: str, size: float | None = None, y: float | None = None,
                      color: str | None = None, stroke: float | None = None,
                      line_h: float | None = None) -> dict[str, Any]:
    """Change how the existing subtitles look without touching their text or timing. line_h (line height in font
    sizes) goes to CapCut as line_spacing and to clipkit_style.json for the MP4 export."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    if text is None:
        raise VideoEditError("this draft has no subtitles")

    changed = 0
    for segment in text["segments"]:
        material = index.get(segment["material_id"], (None, None))[1]
        if not material:
            continue
        body = json.loads(material["content"])
        for style in body["styles"]:
            if size is not None:
                style["size"] = size
            if color:
                style["fill"]["content"]["solid"]["color"] = [
                    int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            if stroke is not None:
                for line in style.get("strokes", []):
                    line["width"] = stroke
        material["content"] = json.dumps(body, ensure_ascii=False)
        if size is not None:
            material["font_size"] = float(size)
            material["text_size"] = size
        if color:
            material["text_color"] = color
        if stroke is not None:
            material["border_width"] = stroke
        if line_h is not None:
            material["line_spacing"] = round(line_h - 1.18, 3)
        if y is not None:
            segment["clip"]["transform"] = {"x": 0.0, "y": y}
        changed += 1

    _save(folder, draft, "style")
    if line_h is not None:
        style_file = folder / "clipkit_style.json"
        style = json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {}
        style_file.write_text(json.dumps({**style, "line_h": line_h}, ensure_ascii=False), encoding="utf-8")
    return {"name": folder.name, "restyled": changed}


TIDY_MODES = ("lines", "split", "shrink")


def tidy_subtitles(path: str, mode: str = "lines") -> dict[str, Any]:
    """Make long subtitles readable before export (render.split_sub): two lines broken where the Thai reads right
    ("lines"), a new subtitle from that point ("split", also when two lines still overflow), or one smaller line
    ("shrink"). First, lines that run straight on hand a dangling joining word to the next line (render.carry_joiners).
    Each line keeps its own look and position; its word times (clipkit_words.json) follow the new text."""
    import render
    if mode not in TIDY_MODES:
        raise VideoEditError(f"unknown mode: {mode} (use {', '.join(TIDY_MODES)})")
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    track = _text_track(draft)
    if track is None or not track["segments"]:
        raise VideoEditError("ยังไม่มีซับ — ถอดเสียงก่อน")
    W, H = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    said_file, style_file = folder / "clipkit_words.json", folder / "clipkit_style.json"
    said = json.loads(said_file.read_text(encoding="utf-8")) if said_file.is_file() else {}
    box_w = render.safe_box(json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {})["w"]
    rows = []
    for seg in sorted(track["segments"], key=lambda s: s["target_timerange"]["start"]):
        material = index.get(seg["material_id"], (None, None))[1]
        if not material:
            continue
        text = json.loads(material["content"]).get("text", "")
        start = seg["target_timerange"]["start"] / US
        rows.append({"seg": seg, "material": material, "start": start, "spoken": said.get(text),
                     "end": start + seg["target_timerange"]["duration"] / US, "text": text})
    moved = render.carry_joiners(rows)
    segments, words, two, split, shrunk = [], dict(said), 0, 0, 0
    for row in rows:
        fdir, px = render.text_px(row["material"], row["seg"], W, H)
        pieces = render.split_sub(row["text"], row["spoken"], row["start"], row["end"], fdir, px, W * box_w, mode)
        split += len(pieces) - 1
        for k, (a, b, text, spoken, factor) in enumerate(pieces):
            seg, material = row["seg"], row["material"]
            if k:
                seg, material = copy.deepcopy(seg), copy.deepcopy(material)
                seg["id"], material["id"] = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
                seg["material_id"] = material["id"]
                draft["materials"]["texts"].append(material)
            body = json.loads(material["content"])
            body["text"] = text
            for style in body["styles"]:
                style["range"] = [0, len(text)]
                if factor < 1:
                    style["size"] = round(style.get("size", material.get("font_size") or 15) * factor, 2)
            if factor < 1:
                material["font_size"] = material["text_size"] = round((material.get("font_size") or 15) * factor, 2)
                shrunk += 1
            material["content"] = json.dumps(body, ensure_ascii=False)
            seg["target_timerange"] = {"start": int(round(a * US)), "duration": int(round((b - a) * US))}
            two += "\n" in text
            if spoken:
                words[text] = spoken
            segments.append(seg)
    track["segments"] = segments
    _save(folder, draft, "tidy")
    said_file.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return {"name": folder.name, "mode": mode, "subtitles": len(segments), "two_lines": two, "split": split,
            "shrunk": shrunk, "moved_words": moved}


class NeedsAgent(VideoEditError):
    """This step is done by the agent in chat; the message is the command to paste there."""


SUBS_PRESET = {"size": 14.0, "y": -0.44, "color": "#ffffff", "stroke": 0.08}   # plain white; two lines stay inside render.SAFE_BOTTOM


def subtitles_language(path: str, lang: str) -> dict[str, Any]:
    """Plain white subtitles in Thai or English with the recommended look. English is translated from the
    Thai lines (kept in clipkit_subs_th.json beside the draft, so switching back restores the original)."""
    folder = Path(path)
    current = read_draft(path)["subtitles"]
    if not current:
        raise VideoEditError("ยังไม่มีซับ — ถอดเสียงก่อน")
    keep = folder / "clipkit_subs_th.json"
    if lang == "en":
        if not keep.is_file():
            keep.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        thai = json.loads(keep.read_text(encoding="utf-8"))
        en_file = folder / "clipkit_subs_en.json"   # written by the agent: one English line per Thai line
        if not en_file.is_file():
            raise NeedsAgent(f'ClipKit: แปลซับเป็นอังกฤษ "{folder}"')
        texts = json.loads(en_file.read_text(encoding="utf-8-sig"))
        if not isinstance(texts, list) or len(texts) != len(thai):
            raise VideoEditError(f"clipkit_subs_en.json has {len(texts) if isinstance(texts, list) else '?'} lines "
                                 f"for {len(thai)} Thai lines - ask the agent to translate again")
        subs = [{"start": s["start"], "end": s["end"], "text": str(t)} for s, t in zip(thai, texts)]
    elif lang == "th":
        src = json.loads(keep.read_text(encoding="utf-8")) if keep.is_file() else current
        subs = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in src]
    else:
        raise VideoEditError(f"unknown language: {lang}")
    out = set_subtitles(path, subs, replace=True, **SUBS_PRESET)
    return {**out, "lang": lang, "lines": len(subs)}


def set_text_layout(path: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Place each subtitle where the editor put it on the 9:16 preview: position (x, y in -1..1),
    tilt in degrees and font size. Only the listed segments change; a backup is kept as .bak_layout."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    if text is None:
        raise VideoEditError("this draft has no subtitles")
    by_id = {s["id"]: s for s in text["segments"]}
    missing = [i["id"] for i in items if i["id"] not in by_id]
    if missing:
        raise VideoEditError(f"subtitles not in this draft (reload the project): {missing[:3]}")
    for item in items:
        seg = by_id[item["id"]]
        seg["clip"]["transform"] = {"x": float(item["x"]), "y": float(item["y"])}
        seg["clip"]["rotation"] = float(item.get("rotation", 0.0))
        if item.get("size"):
            material = index.get(seg["material_id"], (None, None))[1]
            if material:
                body = json.loads(material["content"])
                for style in body["styles"]:
                    style["size"] = float(item["size"])
                material["content"] = json.dumps(body, ensure_ascii=False)
                material["font_size"] = float(item["size"])
                material["text_size"] = float(item["size"])
    _save(folder, draft, "layout")
    return {"name": folder.name, "placed": len(items)}


WORD_PAD = 0.10  # silence left before and after a word at a cut


def trim_pauses(path: str, keep: float = 0.25, min_gain: float = 0.30,
                apply: bool = True, ranges: list[tuple[float, float]] | None = None) -> dict[str, Any]:
    """Find the pauses left in a draft's timeline and cut them out, sliding the
    subtitles along so they stay on the words. ranges = cut exactly these timeline spans instead (the editor's
    "cut this out"); a subtitle wholly inside one goes with it."""
    folder = Path(path)
    draft = _load(folder)
    video = _video_track(draft)
    text = _text_track(draft)
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")

    cuts: list[dict[str, float]] = []
    total = sum(s["source_timerange"]["duration"] for s in video["segments"]) / US
    words_file = folder / "clipkit_words.json"
    # breaths and pauses against the clip's own noise floor (a fixed silence gate finds nothing under café
    # noise), ~0.1 s left each side. Tuned on café footage: 41 cuts / 20 s out of 118 s with every word
    # still there on re-transcription - a hand edit of the same talk has 40.
    cursor = 0.0
    for a, b in ranges or []:
        a, b = max(0.0, a), min(total, b)
        if b - a > 0.05:
            cuts.append({"start": round(a, 3), "end": round(b, 3), "gain": round(b - a, 3)})
    for segment in ([] if ranges else video["segments"]):
        start = segment["source_timerange"]["start"] / US
        length = segment["source_timerange"]["duration"] / US
        for q in quiet_spans(source, start, start + length):
            if q["length"] - 2 * WORD_PAD >= 0.1:
                cuts.append({"start": round(cursor + q["start"] + WORD_PAD, 2), "end": round(cursor + q["end"] - WORD_PAD, 2),
                             "gain": round(q["length"] - 2 * WORD_PAD, 2)})
        cursor += length
    said = json.loads(words_file.read_text(encoding="utf-8")) if words_file.is_file() else {}
    removed = round(sum(c["gain"] for c in cuts), 2)

    preview = {"name": folder.name, "length": round(total, 2), "cuts": cuts,
               "removed": removed, "result_length": round(total - removed, 2)}
    if not apply or not cuts:
        return preview

    pieces = timeline_to_source(draft)
    base = copy.deepcopy(video["segments"][0])
    index = _index(draft)
    grade_template = [index[r] for r in base["extra_material_refs"] if r in index]

    keeps = []
    at = 0.0
    for cut in cuts:
        if cut["start"] > at:
            keeps.append((at, cut["start"]))
        at = max(at, cut["end"])
    if at < total:
        keeps.append((at, total))

    segments = []
    timeline = 0
    for a, b in keeps:
        for p0, p1, src0 in pieces:
            lo, hi = max(a, p0), min(b, p1)
            if hi - lo <= 0.01:
                continue
            segment = copy.deepcopy(base)
            segment["id"] = str(uuid.uuid4()).upper()
            segment["source_timerange"] = {"start": int(round((src0 + (lo - p0)) * US)),
                                           "duration": int(round((hi - lo) * US))}
            segment["target_timerange"] = {"start": timeline,
                                           "duration": int(round((hi - lo) * US))}
            timeline += int(round((hi - lo) * US))
            refs = []
            for key, item in grade_template:
                clone = copy.deepcopy(item)
                clone["id"] = str(uuid.uuid4()).upper()
                draft["materials"].setdefault(key, []).append(clone)
                refs.append(clone["id"])
            segment["extra_material_refs"] = refs
            segments.append(segment)

    for old in video["segments"]:
        for ref in old["extra_material_refs"]:
            key = index.get(ref, (None, None))[0]
            if key:
                draft["materials"][key] = [m for m in draft["materials"][key]
                                           if m.get("id") != ref]
    video["segments"] = segments

    def shifted(t: float) -> float:
        gone = 0.0
        for cut in cuts:
            if t >= cut["end"]:
                gone += cut["end"] - cut["start"]
            elif t > cut["start"]:
                gone += t - cut["start"]
        return t - gone

    if text and ranges:  # a subtitle wholly inside a cut-out span is cut out with it
        text["segments"] = [s for s in text["segments"] if not any(
            c["start"] - 0.05 <= s["target_timerange"]["start"] / US and
            (s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US <= c["end"] + 0.05 for c in cuts)]
    if text:
        for segment in text["segments"]:
            span = segment["target_timerange"]
            a = shifted(span["start"] / US)
            b = shifted((span["start"] + span["duration"]) / US)
            span["start"] = int(round(a * US))
            span["duration"] = max(int(round((b - a) * US)), int(0.4 * US))
        ordered = sorted(text["segments"], key=lambda s: s["target_timerange"]["start"])
        for first, second in zip(ordered, ordered[1:]):
            end = first["target_timerange"]["start"] + first["target_timerange"]["duration"]
            if end > second["target_timerange"]["start"]:
                first["target_timerange"]["duration"] = max(
                    second["target_timerange"]["start"] - first["target_timerange"]["start"]
                    - int(0.03 * US), int(0.4 * US))
        text["segments"] = ordered

    if words_file.is_file():  # word times are relative to their line, and cuts inside a line move them too
        for sub in read_draft(path)["subtitles"]:
            if sub["text"] in said:
                s0 = shifted(sub["start"])
                said[sub["text"]] = [[round(shifted(sub["start"] + a) - s0, 2), round(shifted(sub["start"] + b) - s0, 2), w]
                                     for a, b, w in said[sub["text"]]]
        words_file.write_text(json.dumps(said, ensure_ascii=False), encoding="utf-8")
    draft["duration"] = timeline
    _save(folder, draft, "trim")
    preview.update(applied=True, segments=len(segments),
                   result_length=round(timeline / US, 2))
    return preview


def copy_grade(source_path: str, target_path: str) -> dict[str, Any]:
    """Copy the colour grade (adjust effects, HSL, curves) from one draft onto
    every clip of another."""
    src_folder, dst_folder = Path(source_path), Path(target_path)
    src, dst = _load(src_folder), _load(dst_folder)
    src_index = _index(src)
    src_seg = _video_track(src)["segments"][0]
    template = [src_index[r] for r in src_seg["extra_material_refs"]
                if r in src_index and src_index[r][0] in GRADE_KEYS]
    if not template:
        raise VideoEditError(f"{src_folder.name} has no colour grade to copy")

    # The on/off switches matter as much as the values: a reference can attach
    # adjust effects but leave the adjust chain disabled, and copying only the
    # numbers would then apply a grade the reference never shows.
    flags = {k: src_seg.get(k) for k in
             ("enable_adjust", "enable_color_curves", "enable_smart_color_adjust",
              "enable_lut", "enable_hsl", "enable_hsl_curves", "enable_color_wheels",
              "enable_color_match_adjust", "enable_color_correct_adjust")
             if k in src_seg}

    dst_index = _index(dst)
    for segment in _video_track(dst)["segments"]:
        kept = []
        for ref in segment["extra_material_refs"]:
            key = dst_index.get(ref, (None, None))[0]
            if key in GRADE_KEYS:
                dst["materials"][key] = [m for m in dst["materials"][key] if m.get("id") != ref]
                continue
            kept.append(ref)
        for key, item in template:
            clone = copy.deepcopy(item)
            clone["id"] = str(uuid.uuid4()).upper()
            dst["materials"].setdefault(key, []).append(clone)
            kept.append(clone["id"])
        segment["extra_material_refs"] = kept
        segment.update(flags)

    _save(dst_folder, dst, "grade")
    return {"from": src_folder.name, "to": dst_folder.name,
            "clips": len(_video_track(dst)["segments"]),
            "copied": len(template), "flags": flags}


def transcribe_draft(path: str) -> dict[str, Any]:
    """Transcribe what the draft actually plays — segment by segment, with the
    times already converted to the draft's timeline."""
    from video_edit import transcribe

    folder = Path(path)
    draft = _load(folder)
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")

    lines: list[dict[str, Any]] = []
    words: dict[str, list[list[Any]]] = {}
    cursor = 0.0
    for segment in _video_track(draft)["segments"]:
        start = segment["source_timerange"]["start"] / US
        length = segment["source_timerange"]["duration"] / US
        for phrase in transcribe(source, start, start + length):
            lines.append({"start": round(cursor + phrase["start"], 2),
                          "end": round(cursor + phrase["end"], 2),
                          "text": phrase["text"]})
            # word times relative to the line start, keyed by the line: they stay right after trims move the line
            words[phrase["text"]] = [[round(a - phrase["start"], 2), round(b - phrase["start"], 2), w]
                                     for a, b, w in phrase.get("words", [])]
        cursor += length
    # spoken word times per subtitle line, for the word-by-word highlight in the MP4 export
    (folder / "clipkit_words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    lines.sort(key=lambda p: p["start"])
    for first, second in zip(lines, lines[1:]):
        if first["end"] > second["start"]:
            first["end"] = round(max(second["start"] - 0.03, first["start"] + 0.3), 2)
    return {"name": folder.name, "count": len(lines), "subtitles": lines,
            "timeline": round(cursor, 2)}


# ── animations, reused from the projects that already have them ──────

def list_animations(root: str | None = None) -> list[dict[str, Any]]:
    """Animations available locally, learned from the projects on this machine.

    CapCut animations reference a file in its own cache, so only ones already
    downloaded here can be reused — hence reading them out of real drafts.
    """
    seen: dict[str, dict[str, Any]] = {}
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        track_of = {}
        for track in draft.get("tracks", []):
            for segment in track.get("segments", []):
                for ref in segment.get("extra_material_refs", []):
                    track_of[ref] = track["type"]
        for material in draft["materials"].get("material_animations") or []:
            for animation in material.get("animations") or []:
                name = animation.get("name")
                if not name or not animation.get("path"):
                    continue
                target = track_of.get(material["id"], "video")
                key = f"{name}|{target}"
                entry = seen.setdefault(key, {
                    "name": name, "target": target,
                    "kind": animation.get("type"),
                    "category": animation.get("category_name"),
                    "duration": round(animation.get("duration", 500000) / US, 2),
                    "source": folder.name, "used": 0,
                    "available": Path(animation["path"]).exists(),
                })
                entry["used"] += 1
    return sorted(seen.values(), key=lambda a: (-a["used"], a["name"]))


def _find_animation(name: str, target: str, root: str | None = None) -> dict[str, Any]:
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        track_of = {}
        for track in draft.get("tracks", []):
            for segment in track.get("segments", []):
                for ref in segment.get("extra_material_refs", []):
                    track_of[ref] = track["type"]
        for material in draft["materials"].get("material_animations") or []:
            names = [a.get("name") for a in material.get("animations") or []]
            if name in names and track_of.get(material["id"], "video") == target:
                return copy.deepcopy(material)
    raise VideoEditError(f"animation '{name}' for {target} not found in any project — "
                         f"add it once in CapCut, then it can be reused")


def apply_animation(path: str, name: str, target: str = "text",
                    duration: float | None = None) -> dict[str, Any]:
    """Put one animation on every clip (or every subtitle) of a draft."""
    if target not in ("text", "video"):
        raise VideoEditError("target must be 'text' or 'video'")

    folder = Path(path)
    draft = _load(folder)
    template = _find_animation(name, target)
    if duration is not None:
        for animation in template.get("animations") or []:
            animation["duration"] = int(round(duration * US))

    index = _index(draft)
    track = _text_track(draft) if target == "text" else _video_track(draft)
    if track is None or not track["segments"]:
        raise VideoEditError(f"draft has nothing on the {target} track")

    applied = 0
    for segment in track["segments"]:
        kept = []
        for ref in segment["extra_material_refs"]:
            if index.get(ref, (None,))[0] == "material_animations":
                draft["materials"]["material_animations"] = [
                    m for m in draft["materials"]["material_animations"] if m["id"] != ref]
                continue
            kept.append(ref)
        clone = copy.deepcopy(template)
        clone["id"] = str(uuid.uuid4()).upper()
        draft["materials"].setdefault("material_animations", []).append(clone)
        # CapCut keeps the animation slot right after speed/placeholder
        kept.insert(min(2, len(kept)), clone["id"])
        segment["extra_material_refs"] = kept
        applied += 1

    _save(folder, draft, "anim")
    return {"name": folder.name, "animation": name, "target": target,
            "applied": applied,
            "duration": round((template["animations"][0]["duration"]) / US, 2)
            if template.get("animations") else None}


def clear_animations(path: str, target: str = "text") -> dict[str, Any]:
    """Take the animations back off, in case the look is not wanted."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    track = _text_track(draft) if target == "text" else _video_track(draft)
    if track is None:
        raise VideoEditError(f"draft has no {target} track")
    removed = 0
    for segment in track["segments"]:
        kept = []
        for ref in segment["extra_material_refs"]:
            if index.get(ref, (None,))[0] == "material_animations":
                draft["materials"]["material_animations"] = [
                    m for m in draft["materials"]["material_animations"] if m["id"] != ref]
                removed += 1
                continue
            kept.append(ref)
        segment["extra_material_refs"] = kept
    _save(folder, draft, "animclear")
    return {"name": folder.name, "target": target, "removed": removed}


# ── sound effects, cloned from a project that already uses them ──────

def list_sounds(root: str | None = None) -> list[dict[str, Any]]:
    """Sound effects used in the projects here, with whether the file still exists."""
    seen: dict[str, dict[str, Any]] = {}
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        for audio in draft["materials"].get("audios") or []:
            name = audio.get("name") or Path(audio.get("path", "")).name
            if not name or not audio.get("path"):
                continue
            entry = seen.setdefault(name, {
                "name": name, "path": audio["path"],
                "duration": round(audio.get("duration", 0) / US, 2),
                "kind": audio.get("type"), "source": folder.name, "used": 0,
                "available": Path(audio["path"]).exists(),
            })
            entry["used"] += 1
    return sorted(seen.values(), key=lambda a: -a["used"])


def _borrow_sound(name: str, root: str | None = None) -> tuple[dict, dict, list[dict]]:
    """An audio material + segment shape + its little helper materials."""
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            other = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        match = next((a for a in other["materials"].get("audios") or []
                      if (a.get("name") or Path(a.get("path", "")).name) == name), None)
        if not match:
            continue
        index = _index(other)
        for track in other["tracks"]:
            if track["type"] != "audio":
                continue
            seg = next((s for s in track["segments"] if s["material_id"] == match["id"]), None)
            if not seg:
                continue
            helpers = [copy.deepcopy(index[r][1]) for r in seg["extra_material_refs"] if r in index]
            helper_keys = [index[r][0] for r in seg["extra_material_refs"] if r in index]
            return (copy.deepcopy(match), copy.deepcopy(seg),
                    list(zip(helper_keys, helpers)))
    raise VideoEditError(f"sound '{name}' is not used in any project here")


def add_sound_on_subtitles(path: str, sound: str, volume: float = 0.35,
                           every_line: bool = True, offset: float = 0.0) -> dict[str, Any]:
    """Drop a sound effect at the start of each subtitle — the pairing used in
    hand-edited projects, where a text animation gets a pop underneath it."""
    folder = Path(path)
    draft = _load(folder)
    text = _text_track(draft)
    if text is None or not text["segments"]:
        raise VideoEditError("this project has no subtitles yet")

    material, seg_shape, helpers = _borrow_sound(sound)
    if not Path(material.get("path", "")).exists():
        raise VideoEditError(f"sound file missing on disk: {material.get('path')}")

    material["id"] = str(uuid.uuid4()).upper()
    draft["materials"].setdefault("audios", []).append(material)
    length = material.get("duration", int(0.7 * US))

    starts = []
    previous_end = -99.0
    for seg in sorted(text["segments"], key=lambda s: s["target_timerange"]["start"]):
        start = seg["target_timerange"]["start"] / US + offset
        if not every_line and start - previous_end < 2.0:
            continue
        starts.append(max(0.0, start))
        previous_end = seg["target_timerange"]["start"] / US + \
            seg["target_timerange"]["duration"] / US

    limit = draft.get("duration", 0)
    segments = []
    for start in starts:
        begin = int(round(start * US))
        if begin >= limit:
            continue
        seg = copy.deepcopy(seg_shape)
        seg["id"] = str(uuid.uuid4()).upper()
        seg["material_id"] = material["id"]
        seg["source_timerange"] = {"start": 0, "duration": min(length, limit - begin)}
        seg["target_timerange"] = {"start": begin, "duration": min(length, limit - begin)}
        seg["volume"] = volume
        seg["last_nonzero_volume"] = volume
        refs = []
        for key, helper in helpers:
            clone = copy.deepcopy(helper)
            clone["id"] = str(uuid.uuid4()).upper()
            draft["materials"].setdefault(key, []).append(clone)
            refs.append(clone["id"])
        seg["extra_material_refs"] = refs
        segments.append(seg)

    track = {"type": "audio", "attribute": 0, "flag": 0, "is_default_name": False,
             "id": str(uuid.uuid4()).upper(), "segments": segments}
    draft["tracks"].append(track)
    _save(folder, draft, "sfx")
    return {"name": folder.name, "sound": sound, "added": len(segments),
            "volume": volume, "file": material.get("path")}


def clear_sounds(path: str) -> dict[str, Any]:
    """Remove audio tracks this tool added (keeps the clip's own sound)."""
    folder = Path(path)
    draft = _load(folder)
    before = len(draft["tracks"])
    draft["tracks"] = [t for t in draft["tracks"] if t["type"] != "audio"]
    _save(folder, draft, "sfxclear")
    return {"name": folder.name, "removed_tracks": before - len(draft["tracks"])}


# ── background music library ─────────────────────────────────────────

def music_dirs() -> list[str]:
    """Music library folders; read on use so the app starts before Settings sets stock_music."""
    return [d for d in os.environ.get("MUSIC_DIRS", "").split(";") if d] or [kitconfig.need("stock_music")]
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
_MOODS = {
    "epic": "อลังการ", "cinematic": "ภาพยนตร์", "uplifting": "ฮึกเหิม", "inspiring": "สร้างแรงบันดาลใจ",
    "piano": "เปียโน", "lofi": "โลไฟ ชิล", "chill": "โลไฟ ชิล", "ambient": "บรรยากาศ ชิล",
    "corporate": "องค์กร", "minimal": "มินิมอล", "technology": "เทคโนโลยี",
    "emotional": "ซึ้ง", "hopeful": "มีความหวัง", "calm": "สงบ",
    "luxury": "หรู", "elegant": "เรียบหรู", "upbeat": "จังหวะสนุก",
    "team": "ทีมเวิร์ก", "motivation": "ปลุกใจ", "warm": "อบอุ่น",
}


def _music_meta(path: Path) -> dict[str, Any]:
    from video_edit import FFPROBE, NO_WINDOW
    import subprocess
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, creationflags=NO_WINDOW, text=True)
    try:
        duration = round(float(out.stdout.strip()), 1)
    except ValueError as exc:
        raise VideoEditError(f"ffprobe could not read duration of {path}: {out.stderr.strip()[-300:]}") from exc
    name = path.stem.lower()
    moods = sorted({thai for key, thai in _MOODS.items() if key in name})
    bpm = None
    match = re.search(r"(\d{2,3})bpm", name)
    if match:
        bpm = int(match.group(1))
    return {"name": path.name, "path": str(path), "duration": duration,
            "moods": moods, "bpm": bpm, "folder": path.parent.name}


def list_music(dirs: list[str] | None = None) -> list[dict[str, Any]]:
    """Every music file in the known folders, with a rough mood read off the name."""
    found: list[dict[str, Any]] = []
    for base in (dirs or music_dirs()):
        root = Path(base)
        if not root.is_dir():
            continue
        for entry in sorted(root.rglob("*")):
            if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES:
                if "_ไม่ผ่าน" in entry.parts or entry.parent.name == "sfx":
                    continue
                found.append(_music_meta(entry))
    return found


def add_music(path: str, music_path: str, volume: float = 0.10,
              fade_out: float = 5.0, start: float = 0.0,
              fade_in: float | None = None) -> dict[str, Any]:
    """Lay a music bed under the whole project, the way finished clips
    were done by hand: about 10% volume, a ~5s fade at the end, and a ~3s fade
    in when the song is entered part-way rather than from its first bar."""
    if fade_in is None:
        fade_in = 3.0 if start > 0 else 0.0
    folder = Path(path)
    music = Path(music_path)
    if not music.is_file():
        raise VideoEditError(f"music file not found: {music_path}")

    draft = _load(folder)
    timeline = draft.get("duration", 0)
    if timeline <= 0:
        raise VideoEditError("project timeline is empty")

    meta = _music_meta(music)
    source_len = int(round(meta["duration"] * US)) or timeline
    material, seg_shape, helpers = _borrow_sound_any()

    material = copy.deepcopy(material)
    material.update({
        "id": str(uuid.uuid4()).upper(),
        "name": music.stem,
        "path": str(music.as_posix()),
        "duration": source_len,
        # what CapCut itself writes for a song imported from disk
        "type": "extract_music",
        "category_name": "local",
        "local_material_id": str(uuid.uuid4()).upper(),
    })
    draft["materials"].setdefault("audios", []).append(material)

    begin = int(round(start * US))
    length = min(timeline, source_len - begin) if source_len > begin else timeline
    seg = copy.deepcopy(seg_shape)
    seg["id"] = str(uuid.uuid4()).upper()
    seg["material_id"] = material["id"]
    seg["source_timerange"] = {"start": begin, "duration": length}
    seg["target_timerange"] = {"start": 0, "duration": length}
    seg["volume"] = volume
    seg["last_nonzero_volume"] = volume
    refs = []
    for key, helper in helpers:
        clone = copy.deepcopy(helper)
        clone["id"] = str(uuid.uuid4()).upper()
        draft["materials"].setdefault(key, []).append(clone)
        refs.append(clone["id"])

    if fade_out > 0 or fade_in > 0:
        fade = {"id": str(uuid.uuid4()).upper(), "type": "audio_fade",
                "fade_in_duration": int(round(fade_in * US)),
                "fade_out_duration": int(round(fade_out * US)),
                "fade_type": 0}
        draft["materials"].setdefault("audio_fades", []).append(fade)
        # CapCut keeps the fade third, after speeds and placeholder_infos
        refs.insert(min(2, len(refs)), fade["id"])
    seg["extra_material_refs"] = refs

    draft["tracks"].append({"type": "audio", "attribute": 0, "flag": 0,
                            "is_default_name": False,
                            "id": str(uuid.uuid4()).upper(), "segments": [seg]})
    _save(folder, draft, "music")
    return {"name": folder.name, "music": music.name, "volume": volume,
            "covers": round(length / US, 2), "timeline": round(timeline / US, 2),
            "looped": source_len < timeline}


def _borrow_sound_any(root: str | None = None) -> tuple[dict, dict, list]:
    """Any audio material/segment shape to model a new one on."""
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            other = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        index = _index(other)
        for track in other["tracks"]:
            if track["type"] != "audio" or not track["segments"]:
                continue
            seg = track["segments"][0]
            material = index.get(seg["material_id"], (None, None))[1]
            if not material:
                continue
            helpers = [(index[r][0], copy.deepcopy(index[r][1]))
                       for r in seg["extra_material_refs"]
                       if r in index and index[r][0] != "audio_fades"]
            return copy.deepcopy(material), copy.deepcopy(seg), helpers
    raise VideoEditError("no project here has an audio clip to model on")


# ── filling in the subtitles for footage added after the first pass ───

def subtitle_gaps(path: str, min_gap: float = 1.0, pad: float = 0.15) -> list[dict[str, Any]]:
    """Stretches of the timeline that play sound but carry no subtitle.

    This is what happens after someone lengthens a clip or drops a new one in:
    the old lines keep their place, and the new footage is left bare.
    """
    folder = Path(path)
    draft = _load(folder)
    video = _video_track(draft)
    max_video = max(((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US)
                    for s in video.get("segments", [])) if video and video.get("segments") else 0.0
    limit = max(draft.get("duration", 0) / US, max_video)

    covered: list[tuple[float, float]] = []
    texts_dict = {t["id"]: t.get("content", "") for t in draft.get("materials", {}).get("texts", [])}

    for track in draft.get("tracks", []):
        if track.get("type") == "text":
            for seg in track.get("segments", []):
                mat_id = seg.get("material_id")
                content = texts_dict.get(mat_id, "")
                has_text = True
                if content:
                    try:
                        data = json.loads(content)
                        if not str(data.get("text", "")).strip():
                            has_text = False
                    except Exception:
                        pass
                if not has_text:
                    continue
                start = seg["target_timerange"]["start"] / US
                end = start + seg["target_timerange"]["duration"] / US
                covered.append((max(0.0, start - pad), end + pad))
    covered.sort()

    merged: list[list[float]] = []
    for start, end in covered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    gaps = []
    cursor = 0.0
    for start, end in merged + [[limit, limit]]:
        if start - cursor >= min_gap:
            gaps.append({"start": round(cursor, 2), "end": round(start, 2),
                         "duration": round(start - cursor, 2)})
        cursor = max(cursor, end)
    return gaps


def fill_subtitle_gaps(path: str, min_gap: float = 1.0, preview: bool = False,
                       pad: float = 0.15) -> dict[str, Any]:
    """Transcribe only the bare stretches and add those lines, leaving every
    subtitle that is already on the timeline exactly where it is."""
    from video_edit import transcribe

    folder = Path(path)
    draft = _load(folder)
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")

    gaps = subtitle_gaps(path, min_gap=min_gap, pad=pad)
    pieces = timeline_to_source(draft)

    lines: list[dict[str, Any]] = []
    for gap in gaps:
        for tl_start, tl_end, src_start in pieces:
            lo = max(gap["start"], tl_start)
            hi = min(gap["end"], tl_end)
            if hi - lo < 0.3:
                continue
            offset = src_start + (lo - tl_start)
            for phrase in transcribe(source, offset, offset + (hi - lo)):
                start = lo + phrase["start"]
                end = min(lo + phrase["end"], hi)
                if end - start < 0.3:
                    continue
                lines.append({"start": round(start, 2), "end": round(end, 2),
                              "text": phrase["text"]})

    lines.sort(key=lambda p: p["start"])
    for first, second in zip(lines, lines[1:]):
        if first["end"] > second["start"]:
            first["end"] = round(max(second["start"] - 0.03, first["start"] + 0.3), 2)

    result = {"name": folder.name, "gaps": gaps, "lines": lines,
              "found": len(lines), "added": 0}
    if preview or not lines:
        return result

    written = set_subtitles(path, lines, replace=False)
    result["added"] = written["added"]
    result["subtitles"] = written["subtitles"]
    return result


def set_line_time(path: str, text: str, start: float, end: float) -> dict[str, Any]:
    """Move / stretch one subtitle line on the timeline (the timeline editor)."""
    if end - start < 0.3:
        raise VideoEditError("a subtitle line must last at least 0.3 s")
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    for seg in (_text_track(draft) or {}).get("segments", []):
        m = index.get(seg["material_id"], (None, None))[1]
        if m and json.loads(m["content"]).get("text") == text:
            seg["target_timerange"] = {"start": int(round(start * US)), "duration": int(round((end - start) * US))}
            _save(folder, draft, "linetime")
            return {"start": start, "end": end}
    raise VideoEditError(f"subtitle line not found: {text}")


def set_line_text(path: str, old: str, new: str) -> dict[str, Any]:
    """Change one subtitle line's words (the timeline editor). The ClipKit side files keyed by the line text
    (word times, punch picks) are moved to the new text so they stay attached."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    hit = 0
    for seg in (text or {}).get("segments", []):
        m = index.get(seg["material_id"], (None, None))[1]
        if not m:
            continue
        body = json.loads(m["content"])
        if body.get("text") != old:
            continue
        body["text"] = new
        for st in body.get("styles", []):
            st["range"] = [0, len(new)]
        m["content"] = json.dumps(body, ensure_ascii=False)
        hit += 1
    if not hit:
        raise VideoEditError(f"subtitle line not found: {old}")
    _save(folder, draft, "linetext")
    for name in ("clipkit_words.json", "clipkit_punch.json"):
        f = folder / name
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            if old in data:
                data[new] = data.pop(old)
                f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"changed": hit}
