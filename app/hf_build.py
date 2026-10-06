"""A ClipKit project as a HyperFrames composition: what the timeline editor shows live in <hyperframes-player>
and what `hyperframes render` turns into the MP4, so the preview and the file are drawn by the same engine.

Built from the renderer's dry run (cuts, crop, subtitle phrases, b-roll, music, effects, colour) into
<project>/clipkit_hf/index.html. Media is not copied (raw clips can be gigabytes): it is served by this app.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import render
import video_edit


def _local(out: Path, path: str, name: str | None = None) -> str:
    """A file the composition points at, inside its own folder (the renderer takes only local or https files).
    Small files (b-roll, music, effects, font) are copied once; same name = same file."""
    import shutil
    src = Path(path)
    dst = out / "media" / (name or src.name)
    dst.parent.mkdir(exist_ok=True)
    if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dst)
    return "media/" + quote(dst.name)


def _proxy(out: Path, src: str, segs: list[dict], skin: float = 0) -> str:
    """The kept pieces of the raw clip joined into one light H.264 file (raw files can be 4 GB HEVC a browser
    may not play, and the renderer fails on dozens of clips cut from one file), skin smoothing baked in so the
    live player shows it: made once per cut list and skin strength."""
    import hashlib
    import subprocess
    key = hashlib.md5(json.dumps([[(s["media_start"], s["dur"]) for s in segs], round(skin, 2), render.SKIN_FILTER, render.EDGE_FADE, 2]).encode()).hexdigest()[:10]
    dst = out / "media" / f"footage_{key}.mp4"
    dst.parent.mkdir(exist_ok=True)
    if not dst.is_file():
        for old in dst.parent.glob("footage_*.mp4"):
            try:
                old.unlink()
            except OSError:  # still open in the editor's player: removed on a later build
                pass
        a, b = min(s["media_start"] for s in segs), max(s["media_start"] + s["dur"] for s in segs)
        parts = "".join(f"[0:v]trim={s['media_start'] - a:.3f}:{s['media_start'] - a + s['dur']:.3f},setpts=PTS-STARTPTS[v{i}];"
                        f"[0:a]atrim={s['media_start'] - a:.3f}:{s['media_start'] - a + s['dur']:.3f},asetpts=PTS-STARTPTS,{render.edge_fades(s['dur'])}[a{i}];"
                        for i, s in enumerate(segs))
        graph = parts + "".join(f"[v{i}][a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=1[cv][ca];[cv]scale='if(gt(iw,ih),min(1920,iw),-2)':'if(gt(iw,ih),-2,min(1920,ih))'" + \
            (f",{render.SKIN_FILTER.format(skin=skin)}" if skin > 0 else "") + "[sv]"  # smoothing after the size-down: 4x less work
        r = subprocess.run([render.FFMPEG, "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
                            "-filter_complex", graph, "-map", "[sv]", "-map", "[ca]", "-r", "30", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "18", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                            str(dst)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode or not dst.is_file():
            raise render.VideoEditError("could not prepare the footage: " + r.stderr[-300:])
    return "media/" + dst.name


def _css_color(rgb: list[float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])


def build(path: str) -> Path:
    t = render.render_draft(path, "", dry=True)
    W, H, D = t["width"], t["height"], t["duration"]
    st = t["style"] or {}
    k = min(W, H) / 1080 * render.CAPCUT_PX
    box, lh = render.safe_box(st), float(st.get("line_h", render.LINE_H))
    fit = t["fit"]
    c = st.get("color") or {}
    w = float(c.get("warmth", 0))
    # white balance gains and temperature as one per-channel matrix (SVG), then the CSS tone filters
    wb = (float(c.get("r", 1)) * (1 + w * 0.5), float(c.get("g", 1)), float(c.get("b", 1)) * (1 - w * 0.5))
    flt = (f"url(#wb) brightness({1 + float(c.get('brightness', 0)) * 1.6:.3f}) contrast({float(c.get('contrast', 1)):.3f}) "
           f"saturate({float(c.get('saturation', 1)):.3f})") if c else "none"
    svg = (f'<svg width="0" height="0" style="position:absolute"><filter id="wb" color-interpolation-filters="sRGB">'
           f'<feColorMatrix type="matrix" values="{wb[0]:.3f} 0 0 0 0 0 {wb[1]:.3f} 0 0 0 0 0 {wb[2]:.3f} 0 0 0 0 0 1 0"/>'
           f'</filter></svg>')
    els, anim = [], []
    n = 0
    out = Path(path) / "clipkit_hf"
    out.mkdir(exist_ok=True)
    segs = t["segments"]
    foot = _proxy(out, t["source"], segs, float(st.get("skin", 0)))

    def nid(p: str) -> str:
        nonlocal n
        n += 1
        return f"{p}{n}"

    # the talking footage, already cut: one clip for the whole timeline
    els.append(f'<video id="{nid("v")}" class="clip cam" src="{foot}" muted playsinline data-start="0" '
               f'data-duration="{D:.3f}" data-media-start="0" data-track-index="0" style="position:absolute;'
               f'left:{fit["x"]}px;top:{fit["y"]}px;width:{fit["w"]}px;height:{fit["h"]}px;filter:{flt}"></video>')
    els.append(f'<audio id="{nid("a")}" src="{foot}" data-start="0" data-duration="{D:.3f}" '
               f'data-media-start="0" data-track-index="1" data-volume="1"></audio>')
    # zoom cut: punched in between real cuts (render.zoom_spans), same as the ffmpeg export
    for a, b in t["zoom"]:
        anim.append(f'tl.set(".cam",{{scale:{render.ZOOM}}},{a:.3f});tl.set(".cam",{{scale:1}},{b:.3f});')
    # b-roll over the speaker
    for b in t["broll"]:
        if b.get("file") and video_edit.is_image(b["file"]):
            els.append(f'<img id="{nid("b")}" class="clip" src="{_local(out, b["file"])}" data-start="{b["start"]:.3f}" '
                       f'data-duration="{b["dur"]:.3f}" data-track-index="2" '
                       f'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">')
        elif b.get("file"):
            els.append(f'<video id="{nid("b")}" class="clip" src="{_local(out, b["file"])}" muted playsinline data-start="{b["start"]:.3f}" '
                       f'data-duration="{b["dur"]:.3f}" data-media-start="0" data-track-index="2" '
                       f'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>')

    def text(words: str, size: float, y: float, fill, stroke, width: float, a: float, b: float, grow: str = "both", floor: float = 0, one: bool = False) -> str:
        i = nid("t")
        # same fitting as the ffmpeg export: one line when it fits, two balanced lines, then smaller
        lines, px = render._fit(words, render.DEFAULT_FONT, size * k, W * box["w"], one)
        # a wrapped lead grows upward and a wrapped punch downward, so the pair never covers each other
        top = H / 2 - y * H / 2 + {"up": px * 0.625, "down": -px * 0.625}.get(grow, 0)
        top = max(top, floor) if grow == "down" else top
        shift = {"up": "-100%", "down": "0"}.get(grow, "-50%")
        # same safe box as the ffmpeg export (render.safe_box): clear of the apps' top bar, buttons and caption
        block = len(lines) * px * lh
        upper = top - {"-100%": block, "0": 0}.get(shift, block / 2)
        top += min(max(upper, H * box["top"]), H * box["bottom"] - block) - upper
        tops[i] = top
        els.append(f'<div id="{i}" class="clip txt" data-start="{a:.3f}" data-duration="{max(0.05, b - a - 0.034):.3f}" data-track-index="3" '  # one frame short: the next phrase never shares a frame
                   f'style="top:{top:.0f}px;transform:translateY({shift});font-size:{px:.0f}px;line-height:{lh};color:{_css_color(fill)};'
                   f'-webkit-text-stroke:{px * width * 1.2 + 2:.1f}px {_css_color(stroke)}">{"<br>".join(html.escape(x) for x in lines)}</div>')
        return i

    tops: dict[str, float] = {}
    look = t["look"]
    if look == "pair":
        P = render.pair_look(st)
        held = ""
        en = t.get("caption_en") or {}
        if P["caption"] and en:  # the preset's small translated caption: one per spoken line, also under the title
            lines_at: dict[str, list[float]] = {}
            for p in t["phrases"]:
                s = lines_at.setdefault(p["line"], [p["start"], p["end"]])
                s[1] = max(s[1], p["end"])
            for line, (a, b) in lines_at.items():
                if en.get(line):
                    text(en[line], *P["caption"], a, b)
        for p in t["phrases"]:
            a, b = p["start"], p["end"]
            white, colour, rgb = render._shown(p["lead"], p["punch"], p.get("opts"), held)
            held = white or held
            if t["hook"] and a < render.HOOK_DUR:  # the clip title owns the first seconds
                continue
            lead_id = ""
            if white:
                i = lead_id = text(white, *P["lead"], a, b, "up", one=bool(colour))
                if (p.get("opts") or {}).get("look") != "hold":
                    anim.append(f'tl.fromTo("#{i}",{{opacity:0}},{{opacity:1,duration:0.08}},{a:.3f});')
            if colour:
                hit = min(b - 0.05, max(a + 0.2, a + 0.6)) if white else a
                part = P["punch"] if rgb is None else P["punch"][:2] + (rgb,) + P["punch"][3:]
                i = text(colour, *part, hit, b, "down", tops.get(lead_id, 0) + 8, one=bool(white))
                anim.append(f'tl.fromTo("#{i}",{{scale:1.3}},{{scale:1,duration:0.14}},{hit:.3f});')
            if P["caption"] and not en:
                text((p["lead"] + " " + p["punch"]).strip(), *P["caption"], a, b)
    else:
        for p in t["phrases"]:
            if t["hook"] and p["start"] < render.HOOK_DUR:
                continue
            i = text(p["lead"], 16, -0.6, [1, 1, 1], [0, 0, 0], 0.08, p["start"], p["end"])
            if look == "pop":
                anim.append(f'tl.fromTo("#{i}",{{scale:0.7}},{{scale:1,duration:0.2,ease:"back.out(2)"}},{p["start"]:.3f});')
    # clip title: each line punches in, the second a beat after the first
    first = ""
    for n, h in enumerate(t["hook"]):
        i = text(h["text"], h["size"], h["y"], h["fill"], h["stroke"], h["width"], h["start"], h["end"],
                 h["grow"], tops.get(first, 0) + 8, one=True)
        first = first or i
        anim.append(f'tl.fromTo("#{i}",{{scale:1.2}},{{scale:1,duration:0.16}},{n * 0.15:.2f});')
    # sound: quiet steady music bed, pops on punches, whooshes as b-roll comes in
    mu = t["music"]
    if mu.get("file"):
        els.append(f'<audio id="{nid("m")}" src="{_local(out, mu["file"])}" data-start="0" data-duration="{D:.3f}" '
                   f'data-media-start="0" data-track-index="4" data-volume="{mu["volume"]}"></audio>')
    fx = t["sfx"]
    if fx.get("on"):
        for tp in t["pops"]:
            els.append(f'<audio id="{nid("s")}" src="{_local(out, render._sfx("pop"))}" data-start="{tp:.3f}" data-duration="0.2" '
                       f'data-track-index="5" data-volume="{0.5 * fx["volume"]:.2f}"></audio>')
        for b in t["broll"]:
            if b.get("file"):
                els.append(f'<audio id="{nid("s")}" src="{_local(out, render._sfx("whoosh"))}" data-start="{max(0, b["start"] - 0.15):.3f}" '
                           f'data-duration="0.5" data-track-index="5" data-volume="{0.35 * fx["volume"]:.2f}"></audio>')
    font = _local(out, render.DEFAULT_FONT)
    # the HyperFrames runtime (clip windows, media sync, seeking) for the live player; the renderer brings its own
    runtime = _local(out, str(Path(__file__).with_name("hyperframe.runtime.iife.js")))
    page = f"""<!doctype html>
<html lang="th"><head><meta charset="UTF-8"><meta name="viewport" content="width={W}, height={H}">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script src="{runtime}"></script>
<style>
@font-face{{font-family:"ClipKit Thai";src:url("{font}")}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#000}}
.cam{{transform-origin:{W / 2 - fit["x"]}px {H / 2 - fit["y"]}px}}
.txt{{position:absolute;left:5%;width:90%;white-space:nowrap;transform:translateY(-50%);text-align:center;font-family:"ClipKit Thai",sans-serif;
  font-weight:700;line-height:1.25;paint-order:stroke fill;text-shadow:0 3px 8px rgba(0,0,0,.45)}}
</style></head><body>
{svg}
<div id="root" data-composition-id="main" data-start="0" data-duration="{D:.3f}" data-width="{W}" data-height="{H}">
{chr(10).join(els)}
</div>
<script>
const tl = gsap.timeline({{paused: true}});
{chr(10).join(anim)}
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
</script>
</body></html>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    if not (out / "hyperframes.json").is_file():
        (out / "hyperframes.json").write_text(json.dumps({"name": Path(path).name}), encoding="utf-8")
    return out / "index.html"
