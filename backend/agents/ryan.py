"""Ryan — Video Editor. Always computes end_time = start_time + 30s himself;
the user never provides an end timestamp."""
import os

from .. import config, db, live_progress
from ..notify import emit, push_state
from ..tools import editor_tool, framing_tool


def run(video):
    video_id, batch_id, idx = video["id"], video["batch_id"], video["idx"]
    batch = db.get_batch(batch_id)

    db.update_video(video_id, status="EDITING")
    push_state(video_id=video_id, batch_id=batch_id)
    start = video["start_time_seconds"]
    end = start + config.CLIP_DURATION_SECONDS
    emit("Ryan", f"Editing Video #{idx + 1} (start {start:.0f}s, end {end:.0f}s)",
         batch_id=batch_id, video_id=video_id)

    framing = framing_tool.analyze_framing(video["downloaded_path"], start, config.CLIP_DURATION_SECONDS)
    if framing["zoom"] != 1.0 or framing["crop_x"] != 0.5 or framing["crop_y"] != 0.5:
        note = "zooming in to crop out the source's own watermark" if framing["watermark_detected"] \
            else "re-centering the crop on the main subject"
        emit("Ryan", f"Video #{idx + 1}: {note} ({framing['reason']})", batch_id=batch_id, video_id=video_id)

    dest_path = os.path.join(config.batch_dir(batch_id), "final", f"video_{video_id}.mp4")

    def on_progress(pct):
        live_progress.set_progress(video_id, percent=round(pct, 1), stage="editing", start=start, end=end)

    result = editor_tool.create_video_clip(
        video["downloaded_path"], start, duration=config.CLIP_DURATION_SECONDS,
        aspect_ratio="1:1", caption=video["caption_text"] or "", caption_bold=bool(video["caption_bold"]),
        watermark_enabled=bool(batch["watermark_enabled"]), watermark_text=batch["watermark_text"] or "",
        dest_path=dest_path, on_progress=on_progress,
        crop_x=framing["crop_x"], crop_y=framing["crop_y"], zoom=framing["zoom"],
    )
    live_progress.clear(video_id)

    if not result["success"]:
        db.update_video(video_id, status="FAILED", error_message=result["error"], retry_count=video["retry_count"] + 1)
        emit("Ryan", f"Couldn't edit Video #{idx + 1}: {result['error']}", batch_id=batch_id, video_id=video_id)
        push_state(video_id=video_id, batch_id=batch_id)
        return False

    db.update_video(video_id, status="EDITED", final_path=result["output_file"])
    emit("Ryan", f"Finished editing Video #{idx + 1}", batch_id=batch_id, video_id=video_id)
    push_state(video_id=video_id, batch_id=batch_id)
    return True
