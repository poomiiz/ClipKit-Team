"""Machine paths for the CapCut tools, read from clip-kit config.json.

Looked up in order: $CLIP_KIT_CONFIG, <kit>/config.json, %USERPROFILE%/.clip-kit/config.json.
A missing config or key raises - never guess a path.
"""
import json
import os
import shutil
from pathlib import Path

_KIT = Path(__file__).resolve().parents[2]
_CANDIDATES = [os.environ.get("CLIP_KIT_CONFIG"), _KIT / "config.json", Path.home() / ".clip-kit" / "config.json"]


def _load():
    for c in _CANDIDATES:
        if c and Path(c).is_file():
            # utf-8-sig: Notepad and PowerShell save JSON with a BOM
            return json.loads(Path(c).read_text(encoding="utf-8-sig"))
    raise RuntimeError(f"clip-kit config.json not found; copy {_KIT / 'config.example.json'} to {_KIT / 'config.json'} and set your paths")


CFG = _load()


def need(key):
    # re-read: the Settings page may have just filled this in, no restart needed
    CFG.clear()
    CFG.update(_load())
    v = CFG.get(key)
    if not v:
        raise RuntimeError(f"config.json is missing '{key}'")
    return v


# folder paths are read when a tool asks for them (module __getattr__ below), so the app can start
# on a new machine before the Settings page has filled them in; a tool that needs an unset one still raises
# CapCut's project registry; default location for the current Windows user
ROOT_META = CFG.get("capcut_root_meta") or str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json")
FONT_NAME = CFG.get("card_font")  # optional: editors pick fonts themselves; only previews need one
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def enable_cuda_libs():
    """ctranslate2 (faster-whisper) needs CUDA 12 cuBLAS/cuDNN; on Windows they ship in the nvidia-* pip wheels."""
    if os.name != "nt":
        return
    import site
    for sp in site.getsitepackages():
        for sub in ("cublas", "cudnn"):
            d = Path(sp) / "nvidia" / sub / "bin"
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]


def font_file():
    """Absolute path of the card font, for ffmpeg previews."""
    if not FONT_NAME:
        raise RuntimeError("set \"card_font\" in config.json to a font installed on this machine to render previews")
    for d in (Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts", Path(r"C:\Windows\Fonts")):
        for f in d.glob(FONT_NAME + ".*"):
            return str(f)
    raise RuntimeError(f"font '{FONT_NAME}' is not installed")


def __getattr__(name):
    if name == "DRAFTS":
        return need("capcut_drafts")
    if name == "STOCK":
        return need("stock_video")
    if name == "OLD_BUILDS":
        return CFG.get("capcut_old_builds") or str(Path(need("capcut_drafts")).parent / "_old_builds")
    raise AttributeError(name)


def is_team() -> bool:
    """A clone of the team repo (ClipKit-Team)."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(_KIT), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return "ClipKit-Team" in r.stdout


def simple() -> bool:
    """Simple mode: cut + subtitles + title only, no b-roll, music or sound effects.
    config.json "simple": true/false wins; unset = on for team clones, off elsewhere."""
    v = CFG.get("simple")
    return is_team() if v in (None, "") else bool(v)
