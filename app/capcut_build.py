"""The finished clip as a CapCut project, laid out the way clips are built by hand, so a person can
open it in CapCut and keep editing: cut footage (zoom cuts as 108 % clips, skin effect), white and coloured
subtitle lines on their own tracks, the title, the small English caption, b-roll on an overlay track, a click
on every coloured word, and the music bed.

Built from the same timeline as the MP4 export (render.render_draft dry run) into a copy of the project,
"<project> · CapCut", so ClipKit's own project stays as it is. Styles come from app/capcut_templates.json
(scripts/capcut/make_templates.py, pulled from hand-made CapCut projects). Colour correction is left to CapCut.
"""
from __future__ import annotations

import copy
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import capcut_edit
import render
import video_edit
from video_edit import US, VideoEditError

TEMPLATES = Path(__file__).with_name("capcut_templates.json")


def _machine_paths(text: str) -> str:
    """The templates name CapCut's fonts and effects by {LOCALAPPDATA} / {CAPCUT_APP}: point them at this
    machine's folders (the newest installed CapCut version)."""
    import os
    local = Path(os.environ.get("LOCALAPPDATA", "")).as_posix()
    apps = sorted((Path(local) / "CapCut" / "Apps").glob("*.*.*"), key=lambda p: [int(x) for x in p.name.split(".") if x.isdigit()])
    app = apps[-1].as_posix() if apps else f"{local}/CapCut/Apps/missing"
    return text.replace("{CAPCUT_APP}", app).replace("{LOCALAPPDATA}", local)


def _id() -> str:
    return str(uuid.uuid4()).upper()


def _us(t: float) -> int:
    return int(round(t * US))


def _clone(draft: dict, tpl: dict, material: dict, kind: str) -> dict:
    """A new segment (and its material and helper materials) from a template block; returns the segment."""
    mat = copy.deepcopy(material)
    mat["id"] = _id()
    draft["materials"].setdefault(kind, []).append(mat)
    seg = copy.deepcopy(tpl["seg"])
    seg["id"] = _id()
    seg["material_id"] = mat["id"]
    refs = []
    for k, helper in tpl["refs"]:
        h = copy.deepcopy(helper)
        h["id"] = _id()
        draft["materials"].setdefault(k, []).append(h)
        refs.append(h["id"])
    seg["extra_material_refs"] = refs
    return seg


def _text(draft: dict, role: str, words: str, a: float, b: float, y: float, size: float,
          fill: list | None = None, stroke: list | None = None) -> dict:
    tpl = _T[role]
    mat = copy.deepcopy(tpl["mat"])
    body = json.loads(mat["content"])
    st = copy.deepcopy(body["styles"][0])
    st["range"] = [0, len(words)]
    st["size"] = size
    if fill:
        st["fill"]["content"]["solid"]["color"] = list(fill)
    if stroke and st.get("strokes"):
        st["strokes"][0]["content"]["solid"]["color"] = list(stroke)
    body.update(text=words, styles=[st])
    mat.update(content=json.dumps(body, ensure_ascii=False), font_size=float(size), name=words[:20])
    seg = _clone(draft, tpl, mat, "texts")
    seg["target_timerange"] = {"start": _us(a), "duration": max(_us(b - a) - _us(0.034), _us(0.1))}
    seg["clip"]["transform"] = {"x": 0.0, "y": y}
    seg["clip"]["scale"] = {"x": 1.0, "y": 1.0}
    return seg


def _track(draft: dict, kind: str, segs: list[dict], render_index: int) -> None:
    if not segs:
        return
    for s in segs:
        s["render_index"] = render_index
    draft["tracks"].append({"type": kind, "attribute": 0, "flag": 0, "is_default_name": True, "id": _id(),
                            "segments": sorted(segs, key=lambda s: s["target_timerange"]["start"])})


_T: dict[str, Any] = {}


def build(path: str) -> tuple[Path, list[str]]:
    """Returns the new project folder and what this machine lacks (shown to the person, never hidden)."""
    if not TEMPLATES.is_file():
        raise VideoEditError("app/capcut_templates.json is missing - run scripts/capcut/make_templates.py")
    raw = _machine_paths(TEMPLATES.read_text(encoding="utf-8"))
    warnings: list[str] = []
    probe = json.loads(raw)
    fonts = {st.get("font", {}).get("path", "") for k in ("white", "orange", "caption", "title")
             for st in json.loads(probe[k]["mat"]["content"]).get("styles", [])}
    kanit = render.DEFAULT_FONT.as_posix()
    for f in sorted(x for x in fonts if x and not Path(x).is_file()):
        # a font path that does not exist makes CapCut fail on the text layer: use the Thai font ClipKit ships
        raw = raw.replace(f, kanit)
        warnings.append(f"font {Path(f).name} not on this machine: using Kanit (change it in CapCut if you like)")
    _T.update(json.loads(raw))
    t = render.render_draft(path, "", dry=True)
    st = t["style"] or {}
    src = Path(path)
    dst = src.with_name(src.name + " · CapCut")
    if dst.exists():  # a new build replaces the old copy
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("clipkit*", "*.bak*", "draft_content.before_*"))
    meta_f = dst / "draft_meta_info.json"
    if meta_f.is_file():
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        meta.update(draft_name=dst.name, draft_fold_path=dst.as_posix(), draft_id=_id())
        meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    capcut_edit.fresh_ids(dst)  # its own timeline id: never the same as the project it was copied from
    draft = capcut_edit._load(dst)
    draft["tracks"] = [x for x in draft["tracks"] if x["type"] != "text"]

    # footage: zoom cuts as 108 % clips, skin effect on every clip
    main = capcut_edit._video_track(draft)
    skin = float(st.get("skin", 0))
    skin_id = None
    if skin > 0 and not Path(_T["skin"].get("path", "")).exists():
        warnings.append("CapCut skin effect not downloaded on this machine: add skin smoothing once by hand in CapCut")
        skin = 0
    if skin > 0:
        fx = copy.deepcopy(_T["skin"])
        fx["id"] = skin_id = _id()
        for f in fx.get("face_adjust_params", []):
            for p in f["adjust_params"]:
                if p["name"] == "face_adjust_skin_Intensity":
                    p["value"] = skin
        draft["materials"].setdefault("effects", []).append(fx)
    for seg in main["segments"]:
        a = seg["target_timerange"]["start"] / US
        if any(z0 - 0.01 <= a < z1 for z0, z1 in t["zoom"]):
            sc = seg["clip"]["scale"]
            seg["clip"]["scale"] = {"x": sc["x"] * render.ZOOM, "y": sc["y"] * render.ZOOM}
        if skin_id:
            seg["extra_material_refs"].append(skin_id)

    # subtitles: the same choices as the MP4 (render._shown), each role on its own track like a hand-made project
    look = t["look"]
    P = render.pair_look(st) if look == "pair" else None
    whites, colours, captions, titles = [], [], [], []
    held = ""
    for p in t["phrases"]:
        a, b = p["start"], p["end"]
        white, colour, rgb = render._shown(p["lead"], p["punch"], p.get("opts"), held)
        held = white or held
        if t["hook"] and a < render.HOOK_DUR:
            continue
        if look != "pair":
            whites.append(_text(draft, "white", p["lead"], a, b, -0.6, 16))
            continue
        if white:
            whites.append(_text(draft, "white", white, a, b, P["lead"][1], P["lead"][0], P["lead"][2], P["lead"][3]))
        if colour:
            hit = min(b - 0.05, a + 0.6) if white else a
            colours.append(_text(draft, "orange", colour, hit, b, P["punch"][1], P["punch"][0], rgb or P["punch"][2], P["punch"][3]))
    en = t.get("caption_en") or {}
    if P and P["caption"] and en:
        spans: dict[str, list[float]] = {}
        for p in t["phrases"]:
            s = spans.setdefault(p["line"], [p["start"], p["end"]])
            s[1] = max(s[1], p["end"])
        captions = [_text(draft, "caption", en[line], a, b, P["caption"][1], P["caption"][0])
                    for line, (a, b) in spans.items() if en.get(line)]
    for h in t["hook"]:
        y = h["y"] + {"up": 0.04, "down": -0.05}.get(h["grow"], 0)  # CapCut places a text by its centre
        titles.append(_text(draft, "title", h["text"], h["start"], h["end"], y, h["size"], h["fill"], h["stroke"]))
    _track(draft, "text", whites, 14001)
    _track(draft, "text", colours, 14002)
    _track(draft, "text", captions, 14003)
    _track(draft, "text", titles, 14004)

    # b-roll on an overlay track, filling the frame
    W, H = t["width"], t["height"]
    rolls = []
    for it in t["broll"]:
        if not it.get("file"):
            continue
        info = video_edit.probe(it["file"])
        mat = copy.deepcopy(_T["broll"]["mat"])
        mat.update(path=Path(it["file"]).as_posix(), material_name=Path(it["file"]).name, name=Path(it["file"]).name,
                   duration=_us(info["duration"]), width=info["width"], height=info["height"])
        seg = _clone(draft, _T["broll"], mat, "videos")
        dur = min(float(it["dur"]), info["duration"])
        seg["source_timerange"] = {"start": 0, "duration": _us(dur)}
        seg["target_timerange"] = {"start": _us(float(it["start"])), "duration": _us(dur)}
        cover = max(W / info["width"], H / info["height"]) / min(W / info["width"], H / info["height"])
        seg["clip"]["scale"] = {"x": cover, "y": cover}
        seg["clip"]["transform"] = {"x": 0.0, "y": 0.0}
        seg["volume"] = 0.0
        rolls.append(seg)
    _track(draft, "video", rolls, 1)

    # a click on every coloured word (one short click, ~30 % volume)
    fx = t["sfx"]
    clicks = []
    if fx.get("on"):
        click_file = _T["click"]["mat"]["path"]
        if not Path(click_file).is_file():  # this machine's CapCut never downloaded that click: use ours
            click_file = str(render._sfx("pop"))
        for tp in t["pops"]:
            mat = copy.deepcopy(_T["click"]["mat"])
            mat["path"] = Path(click_file).as_posix()
            seg = _clone(draft, _T["click"], mat, "audios")
            seg["target_timerange"] = {"start": _us(tp), "duration": seg["source_timerange"]["duration"]}
            seg["volume"] = 0.3 * float(fx.get("volume", 1))
            clicks.append(seg)
        # clicks closer than their own length would overlap on one track
        keep, last = [], -1
        for s in sorted(clicks, key=lambda s: s["target_timerange"]["start"]):
            if s["target_timerange"]["start"] >= last:
                keep.append(s)
                last = s["target_timerange"]["start"] + s["target_timerange"]["duration"]
        clicks = keep
    _track(draft, "audio", clicks, 0)
    capcut_edit._save(dst, draft, "capcut_build")

    mu = t["music"]
    if mu.get("file"):
        capcut_edit.add_music(str(dst), mu["file"], float(mu.get("volume", 0.12)))
    return dst, warnings
