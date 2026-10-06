#!/usr/bin/env python3
"""ClipKit Video -> CapCut. Local only: http://127.0.0.1:8770/video-editor.html"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# first run on a new machine: create config.json from the example (same as setup.ps1) so the app can open
# and the Settings page can show what is still missing; the folders stay unset until the person picks them
_CFG = HERE.parent / "config.json"
if not _CFG.is_file():
    import shutil
    shutil.copyfile(HERE.parent / "config.example.json", _CFG)

# Windows: every helper the app starts (ffmpeg, npx, powershell, python) runs without a console window,
# otherwise a black terminal flashes up for each cover, motion render, check or file dialog
if os.name == "nt":
    import subprocess
    _Popen_init = subprocess.Popen.__init__

    def _no_window(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        if not flags & subprocess.CREATE_NEW_CONSOLE:   # a window asked for on purpose (sign-in) stays visible
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        _Popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = _no_window

import kit_settings  # noqa: E402
import video_editor  # noqa: E402

PORT = int(os.environ.get("VIDEO_EDITOR_PORT", "8770"))

app = FastAPI(title="Video to CapCut", version="1.0.0")
app.include_router(video_editor.router)
app.include_router(kit_settings.router)


@app.middleware("http")
async def no_cache_html(request, call_next):
    response = await call_next(request)
    if request.url.path.endswith(".html") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/video-editor.html")


# motion / cover templates, so the cover page can show a live preview of the real template
app.mount("/motion", StaticFiles(directory=str(HERE.parent / "motion"), html=True), name="motion")
app.mount("/ckfonts", StaticFiles(directory=str(HERE.parent / "fonts")), name="ckfonts")  # Kanit for the live player
app.mount("/", StaticFiles(directory=str(HERE / "static"), html=True), name="static")

if __name__ == "__main__":
    if sys.stdout:  # pythonw (autostart) has no console
        print(f"Video -> CapCut: http://127.0.0.1:{PORT}/video-editor.html")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info",
                log_config=None if sys.stderr is None else uvicorn.config.LOGGING_CONFIG)
