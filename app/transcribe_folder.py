"""Transcribe the Sony interview takes proposed from a footage folder."""
from __future__ import annotations

import argparse
import gc
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # ClipKit/app
import video_edit
from video_edit import scan_folder, summarize_transcript, transcribe


def stamp(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600:02}:{(total % 3600) // 60:02}:{total % 60:02}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.folder / "transcripts"
    output.mkdir(parents=True, exist_ok=True)

    files = [item for item in scan_folder(str(args.folder)) if not item.get("error")]
    status = {"started_at": datetime.now().isoformat(timespec="seconds"), "takes": []}
    for take, file in enumerate(files, 1):
        source = Path(file["path"])
        target = output / f"{source.stem}.json"
        if target.exists() and not args.force:
            print(f"SKIP Take {take}: {target.name}", flush=True)
            status["takes"].append({"take": take, "status": "existing", "file": str(target)})
            continue
        print(f"START Take {take}: {source.name}", flush=True)
        phrases = transcribe(str(source), 0, file["duration"], "th", None)  # one model for everything: video_edit.WHISPER_MODEL (large-v3)
        payload = {"take": take, "source": str(source), "duration": file["duration"],
                   "folder": file.get("folder", ""),
                   "phrases": phrases}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        text_target = target.with_suffix(".md")
        text_target.write_text("# Take %d — %s\n\n" % (take, source.name) +
                               "\n".join(f"- [{stamp(item['start'])}] {item['text']}" for item in phrases),
                               encoding="utf-8")
        print(f"DONE Take {take}: {len(phrases)} lines", flush=True)
        status["takes"].append({"take": take, "status": "done", "lines": len(phrases), "file": str(target)})
        (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    # ponytail: one batch at a time; unload Whisper before Qwen because this machine has 8GB VRAM.
    video_edit._model = None
    gc.collect()
    for item in status["takes"]:
        if item["status"] not in ("done", "existing"):
            continue
        target = Path(item["file"])
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("review") and not args.force:
            continue
        data["review"] = summarize_transcript([phrase["text"] for phrase in data["phrases"]], Path(data["source"]).name)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    status["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = ["# SUMMARY TOPICS", "", "ร่างจาก Qwen local — ตรวจเคาะก่อนสร้าง CapCut", ""]
    for item in status["takes"]:
        if item["status"] != "done":
            continue
        data = json.loads(Path(item["file"]).read_text(encoding="utf-8"))
        review = data.get("review", {})
        summary.extend([f"## {Path(data['source']).name}",
                        f"- หัวข้อ: {review.get('title', '')}",
                        f"- Hook: {review.get('hook', '')}",
                        f"- สรุป: {review.get('summary', '')}", ""])
    (output / "SUMMARY_TOPICS.md").write_text("\n".join(summary), encoding="utf-8")
    print("COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
