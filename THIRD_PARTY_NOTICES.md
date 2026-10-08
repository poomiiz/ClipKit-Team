# Third-party notices

## HyperFrames (HeyGen) — Apache License 2.0

- Source: https://github.com/heygen-com/hyperframes (npm package `hyperframes`, version 0.8.101)
- Licence: Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0
- Used by ClipKit:
  - `app/static/hyperframes-player.js` — the `<hyperframes-player>` web component, copied unchanged from
    `hyperframes/dist/hyperframes-player.global.js`.
  - `app/hyperframe.runtime.iife.js` — the HyperFrames runtime, copied unchanged from `hyperframes/dist/`.
  - The `hyperframes` command line (installed from npm at run time) renders the editor's compositions to MP4.
- ClipKit is not affiliated with HeyGen; "HyperFrames" and "HeyGen" are their names, not ClipKit's.

## Kanit (Cadson Demak) — SIL Open Font License 1.1

- `fonts/Kanit-Bold.ttf`, licence text in `fonts/OFL-Kanit.txt`.

## Built-in sound effects

- `sfx_builtin/pop.wav` and `sfx_builtin/whoosh.wav` are synthesised by ClipKit itself with ffmpeg (see `app/render.py`); they are part of this repository under the MIT licence, with no third-party source.
