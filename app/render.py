"""Export a ClipKit project to MP4 without opening CapCut: the kept pieces of the raw clip joined in timeline
order, on the project's own canvas, with its subtitles drawn in (text, size, colour, outline, place, tilt).

Round 1 covers what ClipKit itself makes: the main video track and the main text track. Extra tracks
(b-roll, music, effects added by hand in CapCut) are reported, not silently dropped.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import capcut_edit
import video_edit
from video_edit import FFMPEG, VideoEditError, probe

US = 1_000_000
CAPCUT_PX = 5.2  # pixels per CapCut font-size unit per 1080 px of the canvas short side (same number the editor preview uses)
FONTS = Path(__file__).resolve().parents[1] / "fonts"
DEFAULT_FONT = FONTS / "Kanit-Bold.ttf"  # Google Fonts, OFL: shipped with ClipKit so Thai always shapes


def _font(path: str | None) -> tuple[str, Path]:
    """(family name, file) for libass: the project's own font when the file exists, else the bundled Kanit."""
    from fontTools.ttLib import TTFont
    f = Path(path) if path and Path(path).is_file() else DEFAULT_FONT
    if not f.is_file():
        raise VideoEditError(f"font file missing: {f}")
    return TTFont(str(f), fontNumber=0)["name"].getDebugName(1), f


def _fit(text: str, font_file: Path, size: float, max_w: float, one: bool = False) -> tuple[list[str], float]:
    """Keep a subtitle inside the frame: one line when it fits, else two lines split at the Thai word break
    that balances them best, and only if the longer of the two still overflows, a smaller size."""
    from PIL import ImageFont
    from pythainlp.tokenize import word_tokenize
    font = ImageFont.truetype(str(font_file), 100)
    width = lambda t: font.getlength(t) * size / 100  # noqa: E731
    if one:  # white + colour pair: one line each so the screen never holds more than 2; smaller when long
        lines = [text.replace("\n", " ")]
    elif "\n" in text:  # the editor already chose the line breaks
        lines = text.split("\n")
    elif width(text) <= max_w:
        return [text], size
    else:
        words = word_tokenize(text, keep_whitespace=True)
        cuts = [("".join(words[:i]).strip(), "".join(words[i:]).strip()) for i in range(1, len(words))]
        lines = list(min(cuts, key=lambda c: max(width(c[0]), width(c[1])))) if cuts else [text]
    widest = max(width(t) for t in lines)
    return lines, size if widest <= max_w else size * max_w / widest


def _json(path: Path, empty: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else empty


def _clock(spoken: list | None, length: float):
    """Seconds into the line at a fraction of its text: from the heard words' times, else evenly by letters."""
    if not spoken:
        return lambda f: f * length
    marks, n, acc = [], sum(len(w) for _, _, w in spoken) or 1, 0
    for a, _, w in spoken:
        marks.append((acc / n, a))
        acc += len(w)
    marks.append((1.0, spoken[-1][1]))
    return lambda f: next((t for p, t in reversed(marks) if p <= f), 0.0)


# the sentence-pair look: each spoken phrase becomes a normal line plus a bigger emphasis line, optionally with a
# small translated caption at the bottom. How they look is a preset each user makes (presets/<name>.json, by chat
# or from one of their own CapCut projects: scripts/preset_from_capcut.py); "default" ships with ClipKit.
PRESETS = Path(__file__).resolve().parents[1] / "presets"


def _rgb(hexcolor: str) -> list[float]:
    h = hexcolor.lstrip("#")
    if len(h) != 6:
        raise VideoEditError(f"preset colour must be #rrggbb: {hexcolor}")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def preset_names() -> list[str]:
    return sorted(p.stem for p in PRESETS.glob("*.json"))


def pair_look(style: dict) -> dict[str, Any]:
    """The project's preset as (size, y, fill, outline colour, outline width) per role: lead (normal text),
    punch (emphasis), caption (or None)."""
    name = style.get("preset") or "default"
    f = PRESETS / f"{name}.json"
    if not f.is_file():
        raise VideoEditError(f"preset '{name}' not found in {PRESETS} (have: {', '.join(preset_names())})")
    try:
        p = json.loads(f.read_text(encoding="utf-8"))
        part = lambda r: (float(r["size"]), float(r["y"]), _rgb(r["color"]), _rgb(r["outline"]),  # noqa: E731
                          float(r["outline_width"]))
        return {"lead": part(p["normal"]), "punch": part(p["emphasis"]),
                "caption": part(p["caption"]) if p.get("caption") else None,
                "caption_lang": (p.get("caption") or {}).get("language", "en")}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoEditError(f"preset {f.name} is broken: {exc}") from exc


RED = [0.93, 0.11, 0.11]


def _shown(lead: str, punch: str, opts: dict | None, prev_white: str) -> tuple[str, str, list | None]:
    """What a phrase puts on screen, from the agent's choice in clipkit_punch.json (4th item, optional), the
    options measured from real hand-edited clips: "pair" white lead + coloured punch (default), "white" white
    only, "color" coloured only, "red" punch in red for the strongest point, "hold" keeps the previous white line
    while the coloured line changes (lists), "skip" no subtitle (filler). "show": [white, colour] = shorter
    rewritten words on screen (timing still comes from the spoken words). Returns (white, colour, colour rgb)."""
    o = opts or {}
    w, c = o.get("show") or (lead, punch)
    look = o.get("look", "pair")
    if look == "skip":
        return "", "", None
    if look == "white":
        return " ".join(x for x in (w, c) if x), "", None
    if look == "color":
        return "", " ".join(x for x in (w, c) if x), None
    return (prev_white if look == "hold" else w), c, (RED if look == "red" else None)


# the clip title over the first seconds, measured from hand-edited clips: one or two lines, each line white,
# orange or red (any mix: red/white, white/orange, orange/white ...), coloured text outlined white, white outlined black
HOOK_COLORS = {"white": ([1, 1, 1], [0, 0, 0], 0.05), "orange": ([1, 0.49, 0], [1, 1, 1], 0.06), "red": (RED, [1, 1, 1], 0.06)}
HOOK_DUR = 4.0
# skin smoothing like CapCut's "skin" slider (0.6 = a typical hand-edited setting): an edge-keeping (bilateral) blur
# mixed over the footage at that strength
SKIN_FILTER = "split[sk1][sk2];[sk2]bilateral=sigmaS=8:sigmaR=0.08[sk3];[sk1][sk3]blend=all_opacity={skin:.2f}"
SKIN_DEFAULT = 0.6


def _hook(folder: Path) -> list[dict[str, Any]]:
    """clipkit_hook.json = [{"text": ..., "color": "white|orange|red"}] (1-2 lines, written by the agent) as
    lines to draw: text, size, y, fill, stroke, width. No file = no title; a broken file raises."""
    raw = _json(folder / "clipkit_hook.json", None)
    if raw is None:
        return []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2 or any(
            not isinstance(x, dict) or not str(x.get("text", "")).strip() or x.get("color") not in HOOK_COLORS for x in raw):
        raise VideoEditError('clipkit_hook.json must be 1-2 lines of {"text": ..., "color": "white|orange|red"}')
    # one centre line at y -0.37: a single title sits on it; with two, the first sits above it and the second
    # (bigger) hangs below it
    place = [(36, "mid")] if len(raw) == 1 else [(30, "up"), (36, "down")]
    return [{"text": x["text"].strip(), "size": s, "y": -0.37, "grow": g, "fill": HOOK_COLORS[x["color"]][0],
             "stroke": HOOK_COLORS[x["color"]][1], "width": HOOK_COLORS[x["color"]][2], "start": 0.0, "end": HOOK_DUR}
            for x, (s, g) in zip(raw, place)]


ZOOM, ZOOM_GAP = 1.08, 2.5


def zoom_spans(segs: list) -> list[tuple[float, float]]:
    """Zoom cut: the punch-in changes only on a real cut (a removed pause), so the jump looks meant; it flips
    100% <-> 108% at a cut only when the current framing has held for at least ZOOM_GAP s (no flicker on
    breath cuts close together). Returns the zoomed-in spans in timeline seconds."""
    starts = sorted(s["target_timerange"]["start"] / US for s in segs)
    end = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US for s in segs)
    out, zoomed, since = [], False, 0.0
    for t in starts[1:] + [end]:
        if t - since >= ZOOM_GAP or t == end:
            if zoomed:
                out.append((round(since, 3), round(t, 3)))
            zoomed, since = not zoomed, t
    return out


JOINERS = {"แต่", "และ", "ก็", "คือ", "เพราะ", "ซึ่ง", "แล้วก็", "หรือ", "ถ้า", "เลยทำให้", "ดังนั้น", "ส่วน"}


def _pair(text: str, spoken: list | None, t0: float, t1: float, W: int, H: int, fam: str, font_file: Path,
          look: dict, picked: list | None = None, max_chars: int = 16, marks: list | None = None, held: dict | None = None) -> list[str]:
    """Split a spoken line into phrases of about two seconds, each shown as lead + punch (look = pair_look()).
    picked = the agent's [[lead, punch], ...] for this line (the words that carry the point); without it the
    phrases break at breaths / ~16 letters / Thai joining words and the punch is the phrase's last words."""
    from pythainlp.tokenize import word_tokenize
    held = held if held is not None else {"white": ""}  # a "hold" white line carries over into the next subtitle line
    flat = text.replace("\n", " ")
    at, total = _clock(spoken, t1 - t0), len(flat) or 1
    phrases = []  # (lead, punch, start, punch time, last word time)
    if picked:
        tight = lambda t: "".join(t.split())  # noqa: E731  (spaces may be dropped, nothing else)
        if tight("".join(p[0] + p[1] for p in picked)) != tight(flat):
            raise VideoEditError(f"clipkit_punch.json does not match the subtitle line: {flat}")
        # time of a position in the line, counting letters only (the agent may have dropped spaces)
        letters = [i for i, c in enumerate(flat) if not c.isspace()]
        when = lambda n: t0 + at(letters[min(n, len(letters) - 1)] / total)  # noqa: E731
        n = 0
        for lead, punch, *rest in picked:
            a, hit = when(n), when(n + len(tight(lead)))
            n += len(tight(lead)) + len(tight(punch))
            query = next((x for x in rest if isinstance(x, str)), "")
            opts = next((x for x in rest if isinstance(x, dict)), None)
            phrases.append((lead.strip(), punch.strip(), a, hit, when(n - 1), query, opts))
    else:
        timed, pos = [], 0
        for tok in word_tokenize(flat, keep_whitespace=True):
            timed.append((tok, t0 + at(pos / total)))
            pos += len(tok)
        chunks, cur = [], []
        for k, (tok, t) in enumerate(timed):
            gap = k and tok.strip() and t - timed[k - 1][1] > 0.6
            joiner = tok.strip() in JOINERS and sum(len(x) for x, _ in cur) >= 6  # a new thought starts here
            if cur and (gap or joiner or sum(len(x) for x, _ in cur) >= max_chars):
                chunks.append(cur)
                cur = []
            cur.append((tok, t))
        if cur:
            chunks.append(cur)
        for ch in chunks:
            words = [x for x, _ in ch]
            real = [i for i, w in enumerate(words) if w.strip()]
            if not real:
                continue
            # punch = trailing words worth ~40% of the letters (at least one word); lead = the rest
            cut, acc, size = real[-1], 0, sum(len(w) for w in words)
            for i in reversed(real):
                acc += len(words[i])
                cut = i
                if acc >= size * 0.4:
                    break
            phrases.append(("".join(words[:cut]).strip(), "".join(words[cut:]).strip(), ch[0][1], ch[cut][1], ch[-1][1], "", None))
    k = min(W, H) / 1080 * CAPCUT_PX

    def line(words: str, part: tuple, a: float, b: float, extra: str = "", one: bool = False) -> str:
        size, y, fill, stroke, width = part
        px = size * k
        lines, px = _fit(words, font_file, px, W * 0.9, one)
        return (f"Dialogue: 0,{_ts(a)},{_ts(b)},S,,0,0,0,,{{\\an5\\pos({W / 2:.0f},{H / 2 - y * H / 2:.0f})\\fn{fam}"
                f"\\fs{px:.0f}\\1c{_ass_color(fill)}\\3c{_ass_color(stroke)}\\bord{px * width * 0.6 + 2:.1f}"
                f"\\shad2{extra}}}" + "\\N".join(lines))

    out = []
    for n, (lead, punch, a, hit, last, query, opts) in enumerate(phrases):
        # stays until the next phrase, but not through a long pause after its last word
        b = min(phrases[n + 1][2] if n + 1 < len(phrases) else t1, last + 1.5)
        # the punch lands when it is said, but never leaves the lead alone on screen for long (slow talkers)
        hit = min(b - 0.05, max(a + 0.2, min(hit, a + 0.6)))
        white, colour, rgb = _shown(lead, punch, opts, held["white"])
        held["white"] = white or held["white"]
        if a < held.get("until", 0):  # the clip title owns the screen for its first seconds
            white = colour = ""
        if marks is not None:  # moments for sound effects, b-roll, and the editor's timeline
            marks.append(("phrase", round(a, 2), round(b, 2), lead, punch, text, n, query, opts))
            if colour:
                marks.append(("pop", hit if white else a))
            if query:
                marks.append(("broll", a, b, query))
        held_on = (opts or {}).get("look") == "hold"
        if white:
            out.append(line(white, look["lead"], a, b, "" if held_on else "\\fad(80,0)", bool(colour)))
        if colour:
            part = look["punch"] if rgb is None else look["punch"][:2] + (rgb,) + look["punch"][3:]
            out.append(line(colour, part, hit if white else a, b, "\\fscx130\\fscy130\\t(0,140,\\fscx100\\fscy100)", bool(white)))
        if look["caption"] and not held.get("en"):
            out.append(line((lead + " " + punch).strip(), look["caption"], a, b))
    # the preset's small bottom caption, one translated line per spoken line (clipkit_caption.json, by the agent)
    en = (held.get("en") or {}).get(text)
    if look["caption"] and en and phrases:  # shown under the title too
        out.append(line(en, look["caption"], phrases[0][2], t1))
    return out


def _karaoke(lines: list[str], spoken: list | None, length: float, base: str, hl: str) -> list[tuple[float, float, str]]:
    """The whole line shows; the word being said turns the highlight colour, then back.
    Returned as back-to-back pieces (start, end, text in seconds from the line start), one per word, each with
    plain colour switches: libass chains two \\t colour changes on one word wrongly (unsaid words lit up).
    Word times come from the speech model (relative to the line start); the shown words can differ from the
    heard ones (fixed typos, English terms), so they are matched by position in the text, not by spelling."""
    from pythainlp.tokenize import word_tokenize
    total = sum(len(t) for t in lines) or 1
    at = _clock(spoken, length)
    toks, pos = [], 0  # (line no, text, start, end)
    for li, line in enumerate(lines):
        for tok in word_tokenize(line, keep_whitespace=True):
            a = at(pos / total)
            pos += len(tok)
            toks.append((li, tok, a, max(a + 0.08, at(pos / total))))

    def text(lit: int) -> str:
        parts = []
        for k, (li, tok, _, _) in enumerate(toks):
            if k and li != toks[k - 1][0]:
                parts.append("\\N")
            parts.append(f"{{\\1c{hl}}}{tok}{{\\1c{base}}}" if k == lit and tok.strip() else tok)
        return "".join(parts)

    words = [k for k, t in enumerate(toks) if t[1].strip()]
    if not words:
        return [(0.0, length, text(-1))]
    out = [(0.0, toks[words[0]][2], text(-1))]
    for n, k in enumerate(words):
        end = toks[words[n + 1]][2] if n + 1 < len(words) else toks[k][3]
        out.append((toks[k][2], end, text(k)))
    out.append((toks[words[-1]][3], length, text(-1)))
    return [(a, min(b, length), t) for a, b, t in out if min(b, length) - a > 0.005]


def _ass_color(rgb: list[float]) -> str:
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb[:3])
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


SFX = FONTS.parent / "sfx_builtin"  # made here with ffmpeg on first use: no licence to worry about
SFX_RECIPE = {
    "pop": "aevalsrc=0.6*sin(2*PI*(500+1400*t)*t)*exp(-28*t):d=0.14:s=44100",
    "whoosh": "anoisesrc=d=0.5:c=pink:a=0.6:r=44100,bandpass=f=1600:w=1400,afade=t=in:d=0.22,afade=t=out:st=0.22:d=0.28",
}


def _sfx(kind: str) -> Path:
    f = SFX / f"{kind}.wav"
    if not f.is_file():
        SFX.mkdir(exist_ok=True)
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", SFX_RECIPE[kind], str(f)],
                           capture_output=True, text=True)
        if r.returncode or not f.is_file():
            raise VideoEditError(f"could not make sound effect {kind}: {r.stderr[-300:]}")
    return f


def _music(style: dict[str, Any]) -> str | None:
    """The bed picked in the editor; with none picked, the first track of the music library (same pick every
    export), or no music when there is no library. style['music'] = '' turns music off."""
    if "music" in style:
        if style["music"] and not Path(style["music"]).is_file():
            raise VideoEditError(f"music file missing: {style['music']}")
        return style["music"] or None
    tracks = sorted(p for d in capcut_edit.music_dirs() for p in Path(d).rglob("*")
                    if p.suffix.lower() in capcut_edit.AUDIO_SUFFIXES)
    return str(tracks[0]) if tracks else None


def render_draft(path: str, out_dir: str, preview: bool = False, dry: bool = False) -> dict[str, Any]:
    folder = Path(path)
    draft = capcut_edit._load(folder)
    index = capcut_edit._index(draft)
    W, H = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    video = capcut_edit._video_track(draft)
    text = capcut_edit._text_track(draft)
    skipped = sum(1 for t in draft["tracks"] if t is not video and t is not text and t["segments"])

    segs = sorted(video["segments"], key=lambda s: s["target_timerange"]["start"])
    if not segs:
        raise VideoEditError("project has no video on its timeline")
    mats = {m["id"]: m for m in draft["materials"].get("videos", [])}
    src = mats[segs[0]["material_id"]]["path"]
    if any(mats[s["material_id"]]["path"] != src for s in segs):
        names = sorted({Path(mats[s["material_id"]]["path"]).name for s in segs})
        raise VideoEditError("โปรเจกต์นี้แทร็กหลักมีหลายไฟล์ (" + ", ".join(names[:3]) + ") แบบที่ตัดมือใน CapCut: "
                             "หน้าแก้คลิปของ ClipKit เปิดได้เฉพาะคลิปที่ตัดจากไฟล์ดิบไฟล์เดียว ให้กดปุ่ม CapCut เพื่อแก้ต่อ "
                             "หรือสร้างโปรเจกต์ใหม่จากไฟล์ดิบ")
    if not Path(src).is_file():
        raise VideoEditError(f"raw file not found: {src}")
    info = probe(src)

    # footage placed the way CapCut shows it: fit inside the canvas, then the clip's own scale and offset
    clip = segs[0]["clip"]
    fit = min(W / info["width"], H / info["height"]) * clip["scale"]["x"]
    vw, vh = round(info["width"] * fit / 2) * 2, round(info["height"] * fit / 2) * 2
    ox = round((W - vw) / 2 + clip["transform"]["x"] * W / 2)
    oy = round((H - vh) / 2 - clip["transform"]["y"] * H / 2)

    parts, labels = [], []
    for i, s in enumerate(segs):
        a = s["source_timerange"]["start"] / US
        b = a + s["source_timerange"]["duration"] / US
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[vc][ac];" + \
        f"[vc]scale={vw}:{vh}[vs];color=black:s={W}x{H}:r={info.get("fps") or 30}[bg];[bg][vs]overlay={ox}:{oy}:shortest=1[vo]"

    # subtitles: one ASS event per line, positioned and tilted like the CapCut segment
    events, font_files, family = [], set(), None
    olist = folder / "clipkit_overlays.json"
    overlays = json.loads(olist.read_text(encoding="utf-8")) if olist.is_file() else []
    # subtitle look chosen in the editor: none (still), pop (bounce in), karaoke (the spoken word lights up)
    style = _json(folder / "clipkit_style.json", {})
    # colour: the editor's sliders (or the auto pick) on the footage only, never on text or b-roll
    skin = float(style.get("skin", 0))
    if skin > 0:  # smoother skin: an edge-keeping blur mixed in (eyes, hair and text edges stay sharp)
        graph = graph.replace("[vs];color=", f",{SKIN_FILTER.format(skin=skin)}[vs];color=", 1)  # after the size-down
    lab = "[vc]"
    c = style.get("color") or {}
    if c:
        w = float(c.get("warmth", 0))
        r, g, b = (float(c.get(k, 1)) for k in ("r", "g", "b"))  # white balance gains from the auto pick
        graph = graph.replace(f"{lab}scale=", f"{lab}colorchannelmixer=rr={r:.3f}:gg={g:.3f}:bb={b:.3f},"
                              f"eq=brightness={float(c.get('brightness', 0)):.3f}:contrast={float(c.get('contrast', 1)):.3f}"
                              f":saturation={float(c.get('saturation', 1)):.3f},colorbalance=rm={w:.3f}:bm={-w:.3f},scale=", 1)
    anim, highlight = style.get("anim", "none"), style.get("highlight", [1, 0.83, 0])
    if anim.startswith("pair-"):  # projects made before presets: that look is now the with-caption preset
        anim, style["preset"] = "pair", style.get("preset") or "with-caption"
    said = _json(folder / "clipkit_words.json", {})
    punches = _json(folder / "clipkit_punch.json", {})  # punch words picked by the agent (ClipKit: เลือกคำเน้น)
    marks: list = []  # ("pop", t) at each punch, ("broll", a, b, query) where the agent asked for b-roll
    shown = 0
    estimated = 0  # lines with no word times (typed by hand, or English): highlight paced by letters instead
    hook = _hook(folder)
    held = {"white": "", "until": HOOK_DUR if hook else 0, "en": _json(folder / "clipkit_caption.json", {})}
    for s in sorted((text or {}).get("segments", []), key=lambda s: s["target_timerange"]["start"]):
        m = index.get(s["material_id"], (None, None))[1]
        if not m:
            continue
        at = s["target_timerange"]["start"] / US
        if any(o["start"] - 0.05 <= at < o["start"] + o["duration"] for o in overlays):
            continue  # a motion shows this line's words already: no second copy as a subtitle
        body = json.loads(m["content"])
        st = (body.get("styles") or [{}])[0]
        fam, fdir = _font((st.get("font") or {}).get("path") or m.get("font_path"))
        family = family or fam
        font_files.add(fdir)
        size = (m.get("font_size") or st.get("size") or 15) * CAPCUT_PX * (min(W, H) / 1080) * s["clip"]["scale"]["x"]
        fill = ((st.get("fill") or {}).get("content") or {}).get("solid", {}).get("color", [1, 1, 1])
        strokes = st.get("strokes") or []
        outline = size * strokes[0].get("width", 0) * 0.6 if strokes else 0  # measured by eye against CapCut
        ocol = strokes[0]["content"]["solid"]["color"] if strokes else [0, 0, 0]
        x = W / 2 + s["clip"]["transform"].get("x", 0) * W / 2
        y = H / 2 - s["clip"]["transform"]["y"] * H / 2
        t0 = s["target_timerange"]["start"] / US
        t1 = t0 + s["target_timerange"]["duration"] / US
        if anim == "pair":
            spoken = said.get(body.get("text", ""))
            estimated += spoken is None
            events += _pair(body.get("text", ""), spoken, t0, t1, W, H, fam, fdir,
                            pair_look(style), punches.get(body.get("text", "")), marks=marks, held=held)
            shown += 1
            continue
        marks.append(("phrase", round(t0, 2), round(t1, 2), body.get("text", ""), "", body.get("text", ""), 0, ""))
        lines, size = _fit(body.get("text", ""), fdir, size, W * 0.9)
        if anim == "karaoke":
            spoken = said.get(body.get("text", ""))
            estimated += spoken is None
            pieces = [(t0 + a, t0 + b, w) for a, b, w in
                      _karaoke(lines, spoken, t1 - t0, _ass_color(fill), _ass_color(highlight))]
        else:
            pieces = [(t0, t1, "\\N".join(lines))]
        shown += 1
        pop = "\\fscx70\\fscy70\\t(0,120,\\fscx108\\fscy108)\\t(120,200,\\fscx100\\fscy100)" if anim == "pop" else ""
        for a, b, words in pieces:
            if a < held["until"]:
                continue
            events.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},S,,0,0,0,,{{\\an5\\pos({x:.0f},{y:.0f})\\fn{fam}"
                          f"\\fs{size:.0f}\\frz{-s['clip'].get('rotation', 0):.1f}\\1c{_ass_color(fill)}"
                          f"\\3c{_ass_color(ocol)}\\bord{outline:.1f}{pop}}}{words}")
    if hook:
        hfam, hfile = _font(None)
        family = family or hfam
        font_files.add(hfile)
        kk = min(W, H) / 1080 * CAPCUT_PX
        for n, h in enumerate(hook):
            lines, px = _fit(h["text"], hfile, h["size"] * kk, W * 0.9, True)
            an = {"up": 2, "down": 8}.get(h["grow"], 5)
            events.append(f"Dialogue: 1,{_ts(h['start'])},{_ts(h['end'])},S,,0,0,0,,{{\\an{an}\\pos({W / 2:.0f},{H / 2 - h['y'] * H / 2 + {2: -4, 8: 4}.get(an, 0):.0f})"
                          f"\\fn{hfam}\\fs{px:.0f}\\1c{_ass_color(h['fill'])}\\3c{_ass_color(h['stroke'])}"
                          f"\\bord{px * h['width'] * 0.6 + 2:.1f}\\shad2\\fad(0,150)\\fscx120\\fscy120"
                          f"\\t({n * 150},{n * 150 + 160},\\fscx100\\fscy100)}}" + lines[0])

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{folder.name}{' - ตัวอย่าง' if preview else ''}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        base = "[vo]"
        if style.get("zoomcut"):
            zin = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in zoom_spans(segs)) or "0"
            # a zoomed copy laid over the plain one only during those spans (zoompan dropped frames: 60 s -> 25 s)
            zw, zh = round(W * ZOOM / 2) * 2, round(H * ZOOM / 2) * 2
            graph += (f";[vo]split[zp][zq];[zq]scale={zw}:{zh},crop={W}:{H}[zz];"
                      f"[zp][zz]overlay=0:0:enable='{zin}'[vz]")
            base = "[vz]"
        ins: list[str] = []  # extra inputs; input 0 is the raw clip

        def add(*args: str) -> int:
            ins.extend(args)
            return sum(1 for x in ins if x == "-i")
        # b-roll: the editor's own list once reviewed (clipkit_placed.json); before that, the free clip the agent's
        # search found, over the speaker for the phrase (max 2.2 s), never closer than 5 s to the previous one
        placed = _json(folder / "clipkit_placed.json", None)
        if placed is None:
            found, plan, last_b = _json(folder / "clipkit_broll.json", {}), [], -99.0
            for _, a, b, q in (m for m in marks if m[0] == "broll"):
                if q in found and a - last_b >= 5:
                    plan.append({"start": round(a, 2), "dur": round(min(b, a + 2.2) - a, 2), "file": found[q]["file"],
                                 "query": q, "thumb": found[q].get("thumb", "")})
                    last_b = a
        else:
            plan = placed.get("broll", [])
        simple = video_edit.kitconfig.simple()  # simple mode: no b-roll, music or sound effects
        if simple:
            plan = []
        for k, it in enumerate(plan):
            if not Path(it["file"]).is_file():
                raise VideoEditError(f"b-roll file missing: {it['file']}")
            # a still picture is one frame: loop it so it stays up for the whole span
            n = add("-loop", "1", "-i", it["file"]) if video_edit.is_image(it["file"]) else add("-i", it["file"])
            a, e = float(it["start"]), float(it["start"]) + float(it["dur"])
            graph += (f";[{n}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                      f"trim=0:{e - a:.3f},setpts=PTS-STARTPTS+{a:.3f}/TB[br{k}];"
                      f"{base}[br{k}]overlay=0:0:eof_action=pass:enable='between(t,{a:.3f},{e:.3f})'[bo{k}]")
            base = f"[bo{k}]"
        if events:
            fam0 = family or _font(None)[0]
            (Path(tmp) / "subs.ass").write_text(
                "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, "
                "Shadow, Alignment, Encoding\nStyle: S,%s,60,&H00FFFFFF,&H00000000,1,0,0,5,1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n%s\n"
                % (W, H, fam0, "\n".join(events)), encoding="utf-8")
            # relative names + cwd=tmp: no Windows drive-letter escaping inside the filter string;
            # libass reads one fonts folder, so the project's font files and Kanit are copied into it
            import shutil
            fd = Path(tmp) / "fonts"
            fd.mkdir()
            for f in font_files | {DEFAULT_FONT}:
                shutil.copy2(f, fd / f.name)
            graph += f";{base}ass=subs.ass:fontsdir=fonts[vf]"
            vout = "[vf]"
        else:
            vout = base
        # motion clips (transparent MOV) on top, each from its own moment; scaled to the canvas height
        for k, o in enumerate(overlays):
            if not Path(o["file"]).is_file():
                raise VideoEditError(f"motion file missing: {o['file']}")
            n = add("-i", o["file"])
            s0 = float(o["start"])
            graph += (f";[{n}:v]scale=-2:{H},setpts=PTS-STARTPTS+{s0:.3f}/TB[m{k}];"
                      f"{vout}[m{k}]overlay=(W-w)/2:0:eof_action=pass:enable='between(t,{s0:.3f},{s0 + float(o['duration']):.3f})'[o{k}]")
            vout = f"[o{k}]"
        if preview:  # quick look for review: half size, fast encode
            graph += f";{vout}scale=trunc(iw/4)*2:-2[pv]"
            vout = "[pv]"

        # sound: a quiet music bed, a pop on each punch, a whoosh as b-roll comes in
        length = sum(s["target_timerange"]["duration"] for s in segs) / US
        mus = (placed or {}).get("music")  # the editor's pick: {"file": path or "", "volume": 0-1}
        mix, music_file = ["[voice]"], (mus["file"] or None) if mus is not None else _music(style)
        if music_file and not Path(music_file).is_file():
            raise VideoEditError(f"music file missing: {music_file}")
        music_vol = mus.get("volume", 0.12) if mus is not None else style.get("music_volume", 0.12)
        fx = (placed or {}).get("sfx", {"on": style.get("sfx", True), "volume": style.get("sfx_volume", 1.0)})
        if simple:
            music_file, fx = None, {"on": False, "volume": 0}
        if dry:  # what the export would contain, for the timeline editor; nothing is encoded
            length = sum(s["target_timerange"]["duration"] for s in segs) / US
            return {"duration": round(length, 2), "width": W, "height": H, "look": anim, "style": style,
                    "hook": hook, "caption_en": held["en"], "zoom": zoom_spans(segs) if style.get("zoomcut") else [],
                    "phrases": [{"start": m[1], "end": m[2], "lead": m[3], "punch": m[4], "line": m[5], "k": m[6],
                                 "query": m[7], "opts": m[8] if len(m) > 8 else None} for m in marks if m[0] == "phrase"],
                    "broll": plan, "motions": overlays, "music": {"file": music_file or "", "volume": music_vol},
                    "sfx": fx, "edited": placed is not None, "source": src, "src_w": info["width"], "src_h": info["height"],
                    "fit": {"w": vw, "h": vh, "x": ox, "y": oy},
                    "pops": [m[1] for m in marks if m[0] == "pop"],
                    "segments": [{"media_start": s["source_timerange"]["start"] / US, "dur": s["source_timerange"]["duration"] / US,
                                  "start": s["target_timerange"]["start"] / US} for s in segs]}
        graph += ";[ac]asplit=2[voice][key]"
        if music_file:
            n = add("-stream_loop", "-1", "-i", music_file)
            # one low steady level under the voice (no pumping up and down)
            graph += (f";[{n}:a]atrim=0:{length:.3f},volume={music_vol},"
                      f"afade=t=out:st={max(0, length - 1.5):.3f}:d=1.5[mus]")
            mix.append("[mus]")
        graph += ";[key]anullsink"
        sfx = [("pop", m[1]) for m in marks if m[0] == "pop"] + [("whoosh", float(it["start"]) - 0.15) for it in plan]
        if fx["on"]:
            for k, (kind, t) in enumerate(sfx):
                n = add("-i", str(_sfx(kind)))
                ms = max(0, round(t * 1000))
                graph += f";[{n}:a]adelay={ms}|{ms},volume={(0.5 if kind == 'pop' else 0.35) * fx['volume']:.3f}[s{k}]"
                mix.append(f"[s{k}]")
        graph += f";{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[aout]"
        cmd = [FFMPEG, "-y", "-i", src, *ins, "-filter_complex", graph, "-map", vout, "-map", "[aout]",
               "-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast", "-crf", "28" if preview else "20",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)]
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not target.is_file():
        raise VideoEditError("export failed: " + r.stderr.strip()[-800:])
    return {"file": str(target), "subtitles": shown, "motions": len(overlays), "sub_anim": anim,
            "broll": [it["query"] for it in plan], "broll_items": plan, "music": Path(music_file).name if music_file else None,
            "music_file": music_file or "", "music_volume": music_vol, "sfx_on": fx["on"], "sfx_volume": fx["volume"],
            "sfx": len(sfx) if fx["on"] else 0, "preview": preview,
            "karaoke_estimated": estimated, "pieces": len(segs), "skipped_tracks": skipped,
            "duration": probe(str(target))["duration"]}
