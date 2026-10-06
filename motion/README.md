# Motion stock

One folder = one reusable effect (HyperFrames project). Edit the data at the top of index.html, then:

    npx --yes hyperframes@0.8.101 render --format mov -o <out>.mov

MOV = ProRes 4444 with transparency: drop it on a track above the footage in CapCut.
Preview over footage: serve this folder and open index.html?bg=<image>.
No client footage or frames in this repo.

| Folder | Effect | From |
|---|---|---|
| sentence-pair | white lead types in, blue punch smears in, both end together | hand-made edit |
| hook-title | level 0 hook: neon-flicker red keyword + typed sub-line (`?style=straight` for no tilt) | hand-made edit |
| cover-a | cover: bold headline (orange / blue) + white sub-line, renders a 1080x1920 PNG | hand-made cover |

## Adding a new effect
1. Copy a folder (e.g. `hook-title`) to `motion/<new-name>/` and change the animation.
2. Edit its `<script type="application/json" id="clipkit-motion">` block: `label` (shown in the menu),
   `fields` (`text`, `number`, `select` with `options`, or `pairs`), and `length` (`"duration"` or `"pairs"`).
   The template reads the values from `window.KIT` (render) or the URL (preview).
3. Push to GitHub. Teammates press **อัปเดต** in Settings; the effect appears on the motion page with no code change.
A folder without that block (covers, work in progress) is not listed.
