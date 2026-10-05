r"""New machine without the ClipKit window: make config.json from one workspace folder, the same as Settings.

    python scripts/setup_workspace.py D:\ClipKit

Creates footage/, stock/video, stock/music, sfx/, output/, models/ under it and finds CapCut's drafts folder
(if CapCut was opened once). Then run  python scripts/doctor.py .
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts" / "capcut"))
sys.stdout.reconfigure(encoding="utf-8")
if len(sys.argv) != 2:
    sys.exit(__doc__)
import kit_settings  # noqa: E402

try:
    r = kit_settings.set_workspace(kit_settings.Workspace(root=sys.argv[1]))
except Exception as exc:  # HTTPException from the shared Settings code: show its message
    sys.exit(f"setup failed: {getattr(exc, 'detail', exc)}")
print(json.dumps(r, ensure_ascii=False, indent=1))
if not r.get("capcut_drafts"):
    print("CapCut drafts folder not found: open CapCut once, or set capcut_drafts in config.json")
