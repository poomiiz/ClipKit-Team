"""Download the Whisper model once, ahead of the first transcription (about 3 GB for large-v3).

    python scripts/fetch_model.py            # model from config.json (whisper_model, default large-v3)
Already downloaded = returns at once. Raises on any download error.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "capcut"))
import kitconfig  # noqa: E402
from faster_whisper.utils import download_model  # noqa: E402

name = kitconfig.CFG.get("whisper_model") or "large-v3"
root = kitconfig.CFG.get("models_dir") or None  # chosen drive (workspace\models); None = default cache on C:
try:
    path = download_model(name, local_files_only=True, cache_dir=root)
    print(f"Whisper {name} already on this machine: {path}")
except Exception:
    print(f"Downloading Whisper {name} (about 3 GB, first time only)...", flush=True)
    path = download_model(name, cache_dir=root)
    print(f"Whisper {name} ready: {path}")
