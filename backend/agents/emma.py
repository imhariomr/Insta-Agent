"""Emma — Marketing / Creative Caption Specialist. If the user already gave
this video a caption, or pointed it at an earlier video's caption to reuse,
Emma skips generation and reuses that. Otherwise she analyzes the actual
clip window's frames + transcript against the user's per-video description
and picks one caption, explaining why."""
import json
import time

from .. import config, db
from ..notify import emit, push_state
from ..tools import caption_tool

WAIT_FOR_SOURCE_TIMEOUT = 600
WAIT_FOR_SOURCE_INTERVAL = 2


def _wait_for_source_caption(batch_id, source_idx):
    deadline = time.time() + WAIT_FOR_SOURCE_TIMEOUT
    while time.time() < deadline:
        source = next((v for v in db.list_videos(batch_id) if v["idx"] == source_idx), None)
        if not source:
            return None
        if source["caption_text"]:
            return source
        if source["status"] == "FAILED":
            return None
        time.sleep(WAIT_FOR_SOURCE_INTERVAL)
    return None


def run(video, allow_reuse=True):
    """allow_reuse is False when this is a regeneration forced by a QA
    caption-failure — a manual/copied caption already proved to have a
    problem, so it must go through real generation instead of reusing it."""
    video_id, batch_id, idx = video["id"], video["batch_id"], video["idx"]

    if video["skip_caption"]:
        db.update_video(video_id, status="CAPTION_READY", caption_text="", caption_reason="")
        emit("Emma", f"Skipping caption for Video #{idx + 1} — no caption requested",
             batch_id=batch_id, video_id=video_id)
        push_state(video_id=video_id, batch_id=batch_id)
        return True

    if allow_reuse and video["caption_text"]:
        db.update_video(video_id, status="CAPTION_READY",
                         caption_reason=video["caption_reason"] or "Provided by you")
        emit("Emma", f"Using the caption you provided for Video #{idx + 1}", batch_id=batch_id, video_id=video_id)
        push_state(video_id=video_id, batch_id=batch_id)
        return True

    if allow_reuse and video["copied_from_idx"] is not None:
        source_idx = video["copied_from_idx"]
        db.update_video(video_id, status="CAPTION_GENERATING")
        push_state(video_id=video_id, batch_id=batch_id)
        emit("Emma", f"Waiting on Video #{source_idx + 1}'s caption to reuse it for Video #{idx + 1}",
             batch_id=batch_id, video_id=video_id)
        source = _wait_for_source_caption(batch_id, source_idx)
        if source and source["caption_text"]:
            db.update_video(video_id, status="CAPTION_READY", caption_text=source["caption_text"],
                             caption_candidates_json=source["caption_candidates_json"],
                             caption_reason=f"Copied from Video #{source_idx + 1}")
            emit("Emma", f"Copied Video #{source_idx + 1}'s caption for Video #{idx + 1}",
                 batch_id=batch_id, video_id=video_id)
            push_state(video_id=video_id, batch_id=batch_id)
            return True
        emit("Emma", f"Video #{source_idx + 1} never got a caption to copy — "
                      f"generating one for Video #{idx + 1} instead", batch_id=batch_id, video_id=video_id)

    db.update_video(video_id, status="CAPTION_GENERATING")
    push_state(video_id=video_id, batch_id=batch_id)
    emit("Emma", f"Analyzing Video #{idx + 1} for a caption", batch_id=batch_id, video_id=video_id)

    result = caption_tool.generate_caption(
        video["downloaded_path"], video["start_time_seconds"], config.CLIP_DURATION_SECONDS,
        video["description"] or "", {"title": video["title"]},
    )

    if not result["success"]:
        db.update_video(video_id, status="FAILED", error_message=result["error"], retry_count=video["retry_count"] + 1)
        emit("Emma", f"Couldn't write a caption for Video #{idx + 1}: {result['error']}",
             batch_id=batch_id, video_id=video_id)
        push_state(video_id=video_id, batch_id=batch_id)
        return False

    db.update_video(
        video_id, status="CAPTION_READY",
        caption_candidates_json=json.dumps(result["candidates"]),
        caption_text=result["selected"], caption_reason=result.get("reason", ""),
    )
    emit("Emma", f"Selected a caption for Video #{idx + 1}: \"{result['selected']}\"",
         batch_id=batch_id, video_id=video_id)
    push_state(video_id=video_id, batch_id=batch_id)
    return True
