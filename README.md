# ClipKit Team

Talking-head video in, finished vertical short clips out: Thai subtitles in your own preset, a title, breaths cut,
b-roll, music, an MP4 and/or a CapCut project to keep editing. You talk to your AI agent (Claude Code or Codex);
it follows `skills/make-clip/SKILL.md` and runs one command.

## Setup (once per machine)
```
git clone https://github.com/poomiiz/ClipKit-Team ClipKit
cd ClipKit
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Workspace D:\ClipKit
```
`D:\ClipKit` is any folder for footage, stock and exports. The last line printed must be all OK.

## Use
Ask your agent, e.g. "ตัดคลิป D:\งาน\video01.mp4 เป็นคลิปสั้นไม่เกิน 2 นาที ทำเป็นโปรเจกต์ CapCut ด้วย".
Underneath: `python scripts/run_clip.py "<video>" --max 120 --capcut` (see the skill for every option).

## What is in here
| Path | |
|---|---|
| `skills/make-clip/SKILL.md` | The only instructions the agent needs |
| `scripts/run_clip.py` | The one command |
| `presets/` | Subtitle presets (normal + emphasis text); make your own by chat or `scripts/preset_from_capcut.py` |
| `app/` | The engine: transcribe, cut, subtitles, colour, HyperFrames page and export, CapCut project |
| `scripts/doctor.py`, `setup.ps1`, `setup_workspace.py`, `fetch_model.py` | Machine setup and check |
| `prompts/story_split.md` | How stories are picked from a long recording |

Engine updates come from the full ClipKit repo (owner runs its `scripts/sync_team.py`).
