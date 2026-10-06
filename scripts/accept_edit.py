"""A project was changed in CapCut on purpose: let ClipKit carry on from that version.

    python scripts/accept_edit.py "<project folder>"

Only for edits a person made in CapCut. A project an AI edited by hand: make it again from the raw file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import capcut_edit  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)
capcut_edit.accept(sys.argv[1])
print("ok: ClipKit continues from the current version of", sys.argv[1])
