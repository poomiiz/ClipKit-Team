# Contributing to ClipKit

Thanks for helping. Issues and pull requests may be written in Thai or English.

## Set up
1. Fork and clone the repo, then follow "Machine setup" in [README.md](README.md).
2. `python scripts/doctor.py` must show all OK before you report a bug.

## Good first contributions
- A new subtitle preset in `presets/` (copy `default.json`).
- A new motion template in `motion/<name>/` (see [motion/README.md](motion/README.md)).
- Support for a newer CapCut draft format in `app/capcut_edit.py` / `scripts/capcut/`.
- Fixes for issues labelled `good first issue`.

## Rules for code
These are the same rules the AI agents follow ([AGENTS.md](AGENTS.md)):
- Machine paths come from `config.json`; never hard-code a drive path, user name or client project name.
- Scripts raise on bad input; never add a silent fallback (`except: pass`, defaulting to another model, etc.).
- Close CapCut before editing a draft, and back up `draft_content.json` first.
- Any endpoint that takes a path must `resolve()` it and check it stays inside the allowed folder.
- Keep changes small and match the surrounding style.

## Never commit
Video, audio, footage frames, client names or client files, `config.json`, `*.sqlite` edit databases, API keys,
Apps Script / webhook URLs, or fonts you are not allowed to redistribute.

## Before you open a pull request
- `python -m py_compile` on every Python file you changed.
- Run what you changed on a real clip or draft and say in the PR what you ran and what happened.
- One topic per pull request.

By contributing you agree your work is released under the [MIT License](LICENSE).
