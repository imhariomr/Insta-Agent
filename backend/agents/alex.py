"""Alex — YouTube Download Engineer. Downloads at the batch's chosen
resolution and never touches the user's requested start timestamp (that's
Ryan's input later, untouched here)."""
import os

from .. import config, db, live_progress
from ..notify import emit, push_state
from ..tools import youtube_tool


def run(video):
    video_id, batch_id, idx = video["id"], video["batch_id"], video["idx"]
    batch = db.get_batch(batch_id)

    db.update_video(video_id, status="DOWNLOADING")
    push_state(video_id=video_id, batch_id=batch_id)
    emit("Alex", f"Started downloading Video #{idx + 1} at {batch['resolution']}",
         batch_id=batch_id, video_id=video_id)

    dest_dir = os.path.join(config.batch_dir(batch_id), "downloaded")

    def on_progress(pct, speed, eta):
        live_progress.set_progress(
            video_id, percent=pct, stage="downloading",
            speed_bps=speed, eta_seconds=eta, resolution=batch["resolution"],
        )

    result = youtube_tool.download_youtube(
        video["youtube_url"], resolution=batch["resolution"], dest_dir=dest_dir, on_progress=on_progress,
    )
    live_progress.clear(video_id)

    if not result["success"]:
        db.update_video(video_id, status="FAILED", error_message=result["error"], retry_count=video["retry_count"] + 1)
        emit("Alex", f"Couldn't download Video #{idx + 1}: {result['error']}", batch_id=batch_id, video_id=video_id)
        push_state(video_id=video_id, batch_id=batch_id)
        return False

    db.update_video(
        video_id, status="DOWNLOADED", downloaded_path=result["file_path"],
        title=result.get("title"), duration=result.get("duration"),
    )
    emit("Alex", f"Finished downloading Video #{idx + 1} ({result.get('title') or 'untitled'})",
         batch_id=batch_id, video_id=video_id)
    push_state(video_id=video_id, batch_id=batch_id)
    return True
