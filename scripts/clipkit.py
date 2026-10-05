"""clipkit - talking-head short-clip toolkit for agents.

Three steps, one JSON contract (the edit plan) between them:

    python clipkit.py transcribe IN.mov            -> IN.words.json
    python clipkit.py plan IN.mov                  -> IN.plan.json  (auto draft: silence cuts + captions)
    (agent edits IN.plan.json: trims keep ranges, rewrites captions, adds inserts + music)
    python clipkit.py render IN.plan.json          -> IN.mp4 (1080x1920, burned Thai subs)

Judgment lives in the plan JSON; this script only does the deterministic parts.
Every failure raises - no silent defaults.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))}\n{p.stderr[-2000:]}")
    return p


def probe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout.strip()
    try:
        return float(out)
    except ValueError:
        raise RuntimeError(f"ffprobe could not read duration of {path}: {out!r}")


# ---------- transcribe ----------

def add_cuda_dll_dirs():
    """ctranslate2 needs CUDA 12 cuBLAS/cuDNN; on Windows they ship in the nvidia-* pip wheels."""
    if os.name != "nt":
        return
    import site
    for sp in site.getsitepackages():
        for sub in ("cublas", "cudnn"):
            d = Path(sp) / "nvidia" / sub / "bin"
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]


def cmd_transcribe(a):
    if a.device == "cuda":
        add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    src = Path(a.input)
    model = WhisperModel(a.model, device=a.device, compute_type="int8_float16" if a.device == "cuda" else "int8")  # half the VRAM of float16
    segments, info = model.transcribe(str(src), language=a.language, vad_filter=True)
    # Thai: whisper "words" are sub-character byte tokens, so keep segment text (clean) and
    # re-split it on real word boundaries with pythainlp; time each word by its share of characters.
    from pythainlp.tokenize import word_tokenize

    words = []
    for seg in segments:
        toks = [t for t in word_tokenize(seg.text.strip(), engine="newmm") if t.strip()]
        n = sum(len(t) for t in toks)
        if not n:
            continue
        t0, span, acc = seg.start, seg.end - seg.start, 0
        for t in toks:
            s = t0 + span * acc / n
            acc += len(t)
            words.append({"start": round(s, 3), "end": round(t0 + span * acc / n, 3), "text": t.strip()})
    if not words:
        raise RuntimeError(f"no speech found in {src}")
    out = Path(a.out or src.with_suffix(".words.json"))
    out.write_text(json.dumps({"source": str(src.resolve()), "language": info.language, "words": words},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out}  ({len(words)} words)")


# ---------- plan ----------

def detect_silences(src, noise_db, min_sil):
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-vn",
             "-af", f"silencedetect=n={noise_db}dB:d={min_sil}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    if len(ends) < len(starts):  # file ends in silence
        ends.append(probe_duration(src))
    return list(zip(starts, ends))


def keep_from_silences(silences, total, pad):
    keep, cur = [], 0.0
    for s, e in silences:
        if s - cur > 0.15:
            keep.append([round(max(0, cur - pad), 3), round(min(total, s + pad), 3)])
        cur = e
    if total - cur > 0.15:
        keep.append([round(max(0, cur - pad), 3), round(total, 3)])
    return keep


def map_time(t, keep):
    """Source time -> output time, or None if t was cut."""
    off = 0.0
    for s, e in keep:
        if s <= t <= e:
            return off + (t - s)
        off += e - s
    return None


def group_captions(words, keep, max_chars, max_gap):
    caps, cur = [], None
    for w in words:
        s, e = map_time(w["start"], keep), map_time(w["end"], keep)
        if s is None or e is None:
            continue
        text = w["text"]
        if cur and (len(cur["text"]) + len(text) > max_chars or s - cur["end"] > max_gap):
            caps.append(cur)
            cur = None
        if cur is None:
            cur = {"start": round(s, 3), "end": round(e, 3), "text": text}
        else:
            sep = " " if re.match(r"[A-Za-z0-9]", text) and re.search(r"[A-Za-z0-9]$", cur["text"]) else ""
            cur["text"] += sep + text
            cur["end"] = round(e, 3)
    if cur:
        caps.append(cur)
    return caps


def cmd_plan(a):
    src = Path(a.input)
    words_path = Path(a.words or src.with_suffix(".words.json"))
    if not words_path.exists():
        raise RuntimeError(f"{words_path} not found - run transcribe first")
    words = json.loads(words_path.read_text(encoding="utf-8"))["words"]
    total = probe_duration(src)
    keep = keep_from_silences(detect_silences(src, a.noise_db, a.min_silence), total, a.pad)
    if not keep:
        raise RuntimeError("silence detection kept nothing - check --noise-db")
    plan = {
        "source": str(src.resolve()),
        "keep": keep,
        # captions stay in SOURCE time so editing `keep` never desyncs them; render maps them.
        "captions": group_captions([w for w in words if map_time(w["start"], keep) is not None],
                                   [[0, total]], a.max_chars, 0.6),
        "inserts": [],
        "music": None,
        "style": {"font": "DB Heavent", "size": 96, "bold": True, "color": "FFFFFF",
                  "outline": 6, "y": 1350, "main_volume": 1.0},
    }
    out = Path(a.out or src.with_suffix(".plan.json"))
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    kept = sum(e - s for s, e in keep)
    print(f"{out}  keep {len(keep)} ranges, {kept:.1f}s of {total:.1f}s, {len(plan['captions'])} captions")


# ---------- render ----------

def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_color(hex_rgb):
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H00{b}{g}{r}".upper()


def write_ass(path, captions, st):
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        "Style: Default,%s,%d,%s,&H00000000,&H80000000,%d,1,%d,2,2,60,60,%d\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    ) % (W, H, st["font"], st["size"], ass_color(st["color"]), -1 if st["bold"] else 0,
         st["outline"], H - st["y"])
    lines = []
    for c in captions:
        color = f"{{\\c{ass_color(c['color'])}&}}" if c.get("color") else ""
        text = c["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Default,{color}{text}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8-sig")


def fill_916(label_in, label_out):
    return f"[{label_in}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}[{label_out}]"


def cmd_render(a):
    plan_path = Path(a.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    src, keep = plan["source"], plan["keep"]
    if not keep:
        raise RuntimeError("plan.keep is empty")
    st = plan["style"]
    out = Path(a.out or plan_path.with_name(plan_path.name.replace(".plan.json", "") + ".mp4")).resolve()
    total = sum(e - s for s, e in keep)

    inputs = ["-i", src]
    f = []
    for i, (s, e) in enumerate(keep):
        f.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS[v{i}]")
        f.append(f"[0:a]atrim={s}:{e},asetpts=PTS-STARTPTS[a{i}]")
    f.append("".join(f"[v{i}][a{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[vc][ac]")
    f.append(fill_916("vc", "base"))
    vlast = "base"

    idx = 1  # ffmpeg input index; 0 is the source
    for k, ins in enumerate(plan.get("inserts") or []):
        if not Path(ins["file"]).exists():
            raise RuntimeError(f"insert file missing: {ins['file']}")
        inputs += ["-ss", str(ins.get("src_start", 0)), "-t", str(ins["end"] - ins["start"]), "-i", ins["file"]]
        f.append(fill_916(f"{idx}:v", f"i{k}r"))
        f.append(f"[i{k}r]setpts=PTS-STARTPTS+{ins['start']}/TB[i{k}]")
        f.append(f"[{vlast}][i{k}]overlay=eof_action=pass:enable='between(t,{ins['start']},{ins['end']})'[o{k}]")
        vlast = f"o{k}"
        idx += 1

    tmp = Path(tempfile.mkdtemp(prefix="clipkit_"))
    caps = []
    for c in plan.get("captions") or []:
        s = map_time(c["start"], keep)
        if s is None:
            continue  # caption sits in a cut range
        e = map_time(c["end"], keep)
        if e is None:  # end was cut: stop at the end of the range holding the start
            r = next(r for r in keep if r[0] <= c["start"] <= r[1])
            e = map_time(r[1], keep)
        caps.append({**c, "start": s, "end": e})
    write_ass(tmp / "subs.ass", caps, st)
    f.append(f"[{vlast}]subtitles=subs.ass[vout]")

    f.append(f"[ac]volume={st.get('main_volume', 1.0)}[voice]")
    alast = "voice"
    m = plan.get("music")
    if m:
        if not Path(m["file"]).exists():
            raise RuntimeError(f"music file missing: {m['file']}")
        inputs += ["-stream_loop", "-1", "-i", m["file"]]
        f.append(f"[{idx}:a]volume={m.get('volume', 0.15)},atrim=0:{total}[mus]")
        if m.get("duck", True):
            f.append("[voice]asplit[vo1][vo2]")
            f.append("[mus][vo2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[musd]")
            f.append("[vo1][musd]amix=inputs=2:duration=first:normalize=0[aout]")
        else:
            f.append("[voice][mus]amix=inputs=2:duration=first:normalize=0[aout]")
        alast = "aout"

    cmd = ["ffmpeg", "-y", "-hide_banner", *inputs, "-filter_complex", ";".join(f),
           "-map", "[vout]", "-map", f"[{alast}]", "-c:v", "libx264", "-preset", a.preset, "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
           "-movflags", "+faststart", str(out)]
    run(cmd, cwd=tmp)  # cwd=tmp so the subtitles filter gets a plain relative path on Windows
    got = probe_duration(out)
    if abs(got - total) > 0.5:
        raise RuntimeError(f"rendered {got:.2f}s but plan says {total:.2f}s")
    print(f"{out}  {got:.1f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe")
    t.add_argument("input")
    t.add_argument("--out")
    t.add_argument("--model", default="large-v3")
    t.add_argument("--device", default="cuda")
    t.add_argument("--language", default="th")
    t.set_defaults(fn=cmd_transcribe)

    p = sub.add_parser("plan")
    p.add_argument("input")
    p.add_argument("--words")
    p.add_argument("--out")
    p.add_argument("--noise-db", type=float, default=-35)
    p.add_argument("--min-silence", type=float, default=0.45)
    p.add_argument("--pad", type=float, default=0.08)
    p.add_argument("--max-chars", type=int, default=18)
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("render")
    r.add_argument("plan")
    r.add_argument("--out")
    r.add_argument("--preset", default="veryfast")
    r.set_defaults(fn=cmd_render)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
