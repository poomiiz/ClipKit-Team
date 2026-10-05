---
name: make-clip
description: Make short vertical clips from a talking-head video in one command - "ตัดคลิปนี้", "ทำคลิปสั้นไม่เกิน 2 นาที", "ตัดเป็น TikTok 3 ตัว", "ทำเป็นโปรเจกต์ CapCut ด้วย", "make a clip from this footage". Runs scripts/run_clip.py (transcribe, stories, cuts, subtitles in the person's preset, title, b-roll, music, MP4 and/or CapCut project) and does its two thinking steps. Also makes subtitle presets.
---

# Make a clip (ClipKit Team)

Run from the ClipKit folder. A failed command = report `Blocked` with the exact error; never skip a step or guess.

## New machine (once)
`powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Workspace D:\ClipKit` (any folder). It installs
Python, ffmpeg, Node.js, the packages and the speech model, makes `config.json`, and ends with
`python scripts/doctor.py`: every line must be OK (WARN on "preview font" is fine). Optional: a free Pixabay key
(pixabay.com/api/docs) as `"pixabay_key"` in `config.json` for automatic b-roll.

## The command
```
python scripts/run_clip.py "<video>" [--max 120] [--preset default] [--all] [--capcut] [--export] [--insert]
```
Ask only what is missing: the video path, and the subtitle preset (`default` when they have no preference;
the list is the `presets/` folder). "ทำเป็นโปรเจกต์ CapCut" = `--capcut`; "ขอไฟล์ MP4" = `--export`;
"ไม่เกิน 2 นาที" = `--max 120`. `--look` (pair, karaoke, pop, none) and `--shape` (portrait) rarely change.
1. Run it. Output is one JSON line per step; rerunning skips every finished step.
2. `{"WAIT": ...}` (exit 2) = a thinking step is yours: **Split stories** or **Pick emphasis** below, then run
   the same command again.
3. Pilot first: without `--all` only story 1 is built. Show the person the result (`<video> - ClipKit.html`,
   the MP4, or the "<project> · CapCut" project) and wait for their OK before `--all`.
4. B-roll: free Pixabay clips go in by themselves. For Envato, the HTML page lists one search link per spot; the
   person downloads into the project's `clipkit_insert` folder (file name starts with the link number, e.g.
   `2 coffee.mp4`), then run again with `--insert`. Never log in to Envato for them.
5. `--capcut` warnings (missing font / skin effect on this machine): tell the person, they add it once in CapCut.
6. `--export` takes about 4 min per minute of clip.

## Split stories (WAIT: `ClipKit: แบ่งเรื่อง "<video>"`)
Read `<video>.transcript.json` (`[{start, end, text}]`, seconds). Follow `prompts/story_split.md`: one point per
story, 60-130 s (never over `--max`), start on the line that opens the point, end on a finished sentence, skip
greetings and setup talk. Write `<video>.stories.json` as UTF-8 JSON
`[{"title": "...", "summary": "...", "start": 12.3, "end": 95.0}]` with times taken from transcript lines.

## Pick emphasis (WAIT: `ClipKit: เลือกคำเน้น "<project folder>"`)
For the sentence-pair subtitles (normal lead line + bigger emphasis punch line, in the project's preset).
1. Read `<folder>/clipkit_lines.json` (list of subtitle line texts, in order) and the whole clip's meaning.
2. For every line, split it into short phrases of about 2 seconds / 10-18 Thai letters, in speaking order,
   breaking where a thought ends. Each phrase = `[lead, punch]`: the punch is the word or short phrase that
   carries the point (a feeling, a number, a result, a brand: "ถูกกดดัน", "20 ปี", "ไม่อิ่ม"), not just the
   last word. Lead + punch together must be that phrase's exact text (punch may come first: then lead is "").
   Every character of the line must be used once, in order (spaces may be dropped).
   Keep each lead to about 15 Thai letters; split a longer phrase rather than letting the text shrink.
   Not every phrase deserves a coloured punch. Filler and connective talk ("อืมๆ", "เออจริงๆ", "แบบว่า",
   "อะไรอย่างนี้", repeated words) and phrases with no point get white only: `[text, ""]`. Colour only the
   phrases that carry the message (a feeling, a number, a result, a key idea) - roughly half or fewer.
   The screen shows at most 2 lines: white 1 + colour 1, or one of them on 2 lines.
   Display options - a 4th item `{"look": ..., "show": [white, colour]}` per phrase ("white" = the preset's
   normal text, "colour" = its emphasis text), measured from real hand-edited clips:
   - `pair` (default): white lead + coloured punch.
   - `white`: white only - setups and questions ("จะแนะนำยังไง", "ธุรกิจเขาเป็นยังไง").
   - `color`: coloured only - a short line that is all point.
   - `red`: the colour line turns red - the one or two strongest points or the clip's question.
   - `hold`: the previous white line stays while the coloured line changes - lists ("บางคนบอก" ->
     "ทำแบรนด์ดิ้ง" / "ลงระบบ CRM" / "จ้างเซลส์มาขาย").
   - `skip`: no subtitle - filler, repeats, false starts, the other person's "อืมๆ".
   - `show`: the words on screen, rewritten short and clean ("ไปจ้างพนักงานขายซิ" ->
     "จ้างเซลส์มาขาย", "มันคือแบบกูต้องทำอะไร" -> "ต้องทำอะไรก่อน?"). Keep the meaning, drop slang and
     filler, max ~15 letters per line. Lead + punch still hold the exact spoken text (they give the timing).
   Example item: `["บางคนบอกต้องลง", "ระบบ Crm", {"look": "hold", "show": ["", "ลงระบบ CRM"]}]`;
   with a b-roll search: `[lead, punch, "office team computer", {...}]`.
3. B-roll: about one phrase in three that names something you can see (a product, food, a place, an action,
   an emotion on a face), add a third item: a short English stock-footage search, e.g.
   `["เดี๋ยวมากิน", "กาแฟต่อ", "coffee cup cafe"]`. Concrete nouns, 2-4 words, no brand names.
   Skip abstract phrases; never two b-roll phrases in a row.
4. Write `<folder>/clipkit_punch.json` as `{"<line text>": [item, ...], ...}`, each item
   `[lead, punch]`, `[lead, punch, query]`, `[lead, punch, options]` or `[lead, punch, query, options]`
   covering every line.
5. Clip title (every clip has one, on screen for the first 4 s; the subtitles start after it): write
   `<folder>/clipkit_hook.json` as 1-2 lines `[{"text": "เริ่มทำธุรกิจใหม่", "color": "red"},
   {"text": "ทำสิ่งนี้ก่อน", "color": "white"}]`. Short punchy promise of the clip, ~8-16 letters per line, not a
   sentence from the talk. Colours per line: `white`, `orange` or `red`, any mix (red/white, white/orange,
   orange/white, one big red word...) - pick by the content: red for a warning or shock, orange for the promise.
   Examples of good titles: "ธุรกิจสมัยนี้ / ไม่ต้องแย่งทำเลอีกแล้ว", "ทำธุรกิจไม่เหนื่อย / ต้องรู้ 2 เรื่องนี้",
   "เงิน 5 แสนก็ไม่เอา!", "Burn out".
6. Only when the project's preset has a `caption` (e.g. `with-caption`): write `<folder>/clipkit_caption.json`
   as `{"<line text>": "<translation>"}` for every line, in the preset's caption `language` (default English) -
   short and natural ("If I want to start my own business from scratch, let's say..."). Brand names stay.

## Subtitle presets (each person makes their own)
`presets/<name>.json`: how the normal text and the emphasis text look, plus an optional small translated
caption. Ask the person for a name.
- **By chat** ("ตัวปกติขาวขอบดำ ตัวเน้นเหลืองใหญ่กว่า"): copy `presets/default.json` and change it. Per role:
  `size` (15-40), `y` (-1 bottom .. 1 top; normal above emphasis, about 0.12 apart), `color` / `outline` as
  `#rrggbb`, `outline_width` (0-0.15). `caption`: `null`, or the same fields plus `language`.
- **From their own CapCut project**: `python scripts/preset_from_capcut.py "<CapCut project folder>" --name <name>`;
  show them what it wrote.

## Check before saying done
Look at the real output, not the plan: grab frames (`ffmpeg -ss <t> -i out.mp4 -frames:v 1 f.png`) at the
title, a subtitle pair, a b-roll and the end. The title shows the first 4 s; subtitles never cover each other and
never exceed 2 lines; no cut inside a word; music under the voice; 1080x1920. Report: pass, or `time - problem`.

## Report
End with `Changed` / `Verified` / `Blocked`, in plain words, with what to open and what to check.
