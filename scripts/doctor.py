"""Check this machine is ready for ClipKit. Prints one line per check; exits 1 if any check fails.

    python scripts/doctor.py           # quick checks
    python scripts/doctor.py --asr     # also transcribe 3 s of silence-free test tone to prove Whisper loads
"""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "capcut"))
results = []


def check(name, fn, optional=False):
    try:
        detail = fn()
        results.append((True, name, detail or ""))
    except Exception as e:  # report every failure, keep checking the rest
        results.append(("WARN" if optional else False, name, str(e)))


def py():
    if sys.version_info < (3, 10):
        raise RuntimeError(f"Python {sys.version.split()[0]}; need 3.10+")
    return sys.version.split()[0]


def modules():
    missing = [m for m in ("faster_whisper", "pythainlp", "soundfile", "ctranslate2", "fastapi", "cv2") if not importlib.util.find_spec(m)]
    if missing:
        raise RuntimeError("missing: " + ", ".join(missing) + " - run scripts/setup.ps1")


def tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise RuntimeError("not on PATH: " + ", ".join(missing))


def node():
    # the MP4 export and the live preview run HyperFrames through npx
    if not shutil.which("npx"):
        raise RuntimeError("Node.js not found (npx missing) - install Node.js LTS from nodejs.org")
    return subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()


def config():
    import kitconfig
    bad = [f"{k}={kitconfig.CFG.get(k)}" for k in ("capcut_drafts", "stock_video", "stock_music", "work_root")
           if not kitconfig.CFG.get(k) or not Path(kitconfig.CFG[k]).is_dir()]
    if bad:
        raise RuntimeError("folders not found: " + "; ".join(bad))
    return "config.json OK"


def font():
    import kitconfig
    return kitconfig.font_file()


def gpu():
    import kitconfig
    if kitconfig.CFG.get("whisper_device", "cuda") != "cuda":
        return "CPU mode (slow)"
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("no NVIDIA GPU found; set whisper_device to cpu in config.json")
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out


def model():
    import kitconfig
    from faster_whisper.utils import download_model
    name = kitconfig.CFG.get("whisper_model") or "large-v3"
    try:
        download_model(name, local_files_only=True)
    except Exception:
        raise RuntimeError(f"Whisper {name} not downloaded yet - press Install (or run scripts/fetch_model.py)")
    return name


def disk():
    low = []
    for d in {Path.home().anchor, *(Path(p).anchor for p in [ROOT])}:
        free = shutil.disk_usage(d).free / 2**30
        if free < 5:
            low.append(f"{d} {free:.1f} GB")
    if low:
        raise RuntimeError("less than 5 GB free: " + ", ".join(low))


def asr():
    import tempfile
    wav = Path(tempfile.gettempdir()) / "clipkit_doctor.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=3", str(wav)], check=True)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "clipkit.py"), "transcribe", str(wav), "--out",
                        str(wav.with_suffix(".json"))], capture_output=True, text=True)
    # a pure tone has no speech: "no speech found" proves the model loaded and ran
    if r.returncode != 0 and "no speech found" not in r.stderr:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr else "transcribe failed")
    return "Whisper loaded and ran"


def capcut():
    import os
    apps = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
    if not apps.is_dir():
        raise RuntimeError("CapCut for PC not installed (capcut.com) - needed for --capcut projects")
    import kitconfig
    d = kitconfig.CFG.get("capcut_drafts")
    if not d or not Path(d).is_dir():
        raise RuntimeError("CapCut drafts folder not set: open CapCut once, then run  clipkit repair")
    return "drafts: " + d


def fonts():
    f = ROOT / "fonts" / "Kanit-Bold.ttf"
    if not f.is_file():
        raise RuntimeError("fonts/Kanit-Bold.ttf missing - run  clipkit update")


def skill():
    missing = [p for p in ("skills/make-clip/SKILL.md", "AGENTS.md", "presets/default.json") if not (ROOT / p).is_file()]
    if missing:
        raise RuntimeError("missing: " + ", ".join(missing) + " - run  clipkit update")
    return "open Claude Code / Codex in " + str(ROOT)


for n, f in [("python", py), ("python packages", modules), ("ffmpeg", tools), ("node", node), ("config + folders", config),
             ("GPU", gpu), ("speech model", model), ("disk space", disk),
             ("CapCut", capcut), ("fonts", fonts), ("AI skill", skill)]:
    check(n, f)
# fonts are each editor's own choice; only the preview render needs "card_font" installed
check("preview font", font, optional=True)
if "--asr" in sys.argv:
    check("speech-to-text", asr)

sys.stdout.reconfigure(encoding="utf-8")
print("ClipKit Team")
for ok, name, detail in results:
    print(f"{'WARN' if ok == 'WARN' else 'OK  ' if ok else 'FAIL'} {name:18} {detail}")
ready = all(r[0] for r in results)
print("
READY" if ready else "
NOT READY - fix the FAIL lines (most are fixed by:  clipkit repair)")
sys.exit(0 if ready else 1)
