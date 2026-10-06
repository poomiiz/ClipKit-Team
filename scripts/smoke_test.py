"""Quick checks that run before every sync to the team repo (scripts/sync_team.py). No video, no network.

    python scripts/smoke_test.py

Each check guards a bug that once reached a machine. Exit 1 = do not ship.
"""
import json
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))
FAILED = []


def check(name, fn):
    try:
        fn()
        print("ok  ", name)
    except Exception as exc:  # noqa: BLE001 - every failure is reported, none is skipped
        FAILED.append(name)
        print("FAIL", name, "-", exc)


def python_compiles():
    for f in list((ROOT / "app").glob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        py_compile.compile(str(f), doraise=True)


def pages_parse():
    # a typo in one <script> leaves the whole page blank
    if not shutil.which("node"):
        raise RuntimeError("node not installed")
    for page in (ROOT / "app" / "static").glob("*.html"):
        for k, js in enumerate(re.findall(r"<script>([\s\S]*?)</script>", page.read_text(encoding="utf-8"))):
            r = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0,'utf8'))"],
                               input=js, capture_output=True, text=True, encoding="utf-8")
            if r.returncode:
                raise RuntimeError(f"{page.name} script {k}: {r.stderr.strip().splitlines()[-1]}")


def json_files_valid():
    # config.example.json once shipped with an unescaped backslash
    for f in [ROOT / "config.example.json", ROOT / "app" / "capcut_templates.json", *(ROOT / "presets").glob("*.json")]:
        json.loads(f.read_text(encoding="utf-8"))


def _fake_project(folder: Path, tid: str) -> None:
    (folder / "Timelines" / tid).mkdir(parents=True)
    (folder / "Timelines" / "project.json").write_text(json.dumps({"main_timeline_id": tid}), encoding="utf-8")
    body = json.dumps({"id": tid, "duration": 1, "tracks": [], "materials": {}})
    (folder / "draft_content.json").write_text(body, encoding="utf-8")
    (folder / "Timelines" / tid / "draft_content.json").write_text(body, encoding="utf-8")


def capcut_ids_unique_and_synced():
    # CapCut 9.6 would not open or hung on a copy that kept its source's timeline id
    import capcut_edit
    with tempfile.TemporaryDirectory() as t:
        a, b = Path(t) / "a", Path(t) / "b"
        _fake_project(a, "SAME")
        shutil.copytree(a, b)
        ida, idb = capcut_edit.fresh_ids(a), capcut_edit.fresh_ids(b)
        assert ida != idb, "two copies share a timeline id"
        for f, tid in ((a, ida), (b, idb)):
            assert json.loads((f / "Timelines" / "project.json").read_text())["main_timeline_id"] == tid
            assert (f / "Timelines" / tid / "draft_content.json").read_text() == (f / "draft_content.json").read_text(), \
                "Timelines copy differs from draft_content.json"


def outside_edit_is_caught():
    import capcut_edit
    from video_edit import VideoEditError
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "p"
        _fake_project(f, "X")
        capcut_edit._save(f, capcut_edit._load(f), "smoke")
        (f / "draft_content.json").write_text('{"id": "X", "tracks": [], "materials": {}, "edited": 1}', encoding="utf-8")
        try:
            capcut_edit._load(f)
        except VideoEditError:
            return
        raise AssertionError("an edit made outside ClipKit was not caught")


def pictures_are_stills():
    # a picture probed as a 0.04 s video showed for one frame
    import video_edit
    assert video_edit.is_image("a.JPG") and video_edit.is_image("b.png") and not video_edit.is_image("c.mp4")


def name_check_works():
    import sync_team
    assert sync_team.names_in("style from Nina 07".encode()), "a client name was not found"
    assert not sync_team.names_in('icon "ClipKit Nina.lnk"'.encode()), "the allowed icon name was flagged"


for name, fn in list(globals().items()):
    if callable(fn) and fn.__module__ == "__main__" and not name.startswith("_") and name != "check":
        check(name, fn)
print(f"\n{len(FAILED)} failed" if FAILED else "\nall checks passed")
sys.exit(1 if FAILED else 0)
