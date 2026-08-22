"""The real workflow state machine. One pipeline per video runs on a shared
thread pool, so e.g. Alex can be downloading Video #3 while Emma writes a
caption for Video #2 — but every step's status genuinely reflects a real
tool call finishing, per the "never fake agent activity" rule."""
import concurrent.futures

from . import config, db
from .agents import alex, david, emma, michael, ryan, sophia
from .notify import push_state

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=config.PIPELINE_WORKERS)

# QA failure categories that mean "the caption itself needs rework" vs.
# categories Ryan can address by re-editing with the caption unchanged.
CAPTION_FAILURE_CATEGORIES = {"caption"}


def start_batch(batch_id):
    videos = db.list_videos(batch_id)
    batch = db.get_batch(batch_id)
    plural = "s" if len(videos) != 1 else ""
    michael.announce(
        f"I've received {len(videos)} video{plural}. I'll have Alex download them at "
        f"{batch['resolution']} first. Once they're ready, Emma will work on captions, "
        f"Ryan will prepare the clips, and David will run QA on each.",
        batch_id=batch_id,
    )
    db.update_batch(batch_id, status="IN_PROGRESS")
    push_state(batch_id=batch_id)
    for video in videos:
        _executor.submit(_run_video_pipeline, video["id"])


def retry_video(video_id):
    video = db.get_video(video_id)
    db.update_video(video_id, status="QUEUED", error_message=None)
    push_state(video_id=video_id, batch_id=video["batch_id"])
    _executor.submit(_run_video_pipeline, video_id)


def stop_batch(batch_id):
    """Cooperative stop, not a hard kill: a step already running (e.g. Ryan
    mid-ffmpeg-encode) finishes on its own, but the pipeline checks this flag
    right before starting each subsequent step and won't start any more —
    there's no safe way to interrupt a thread mid-call without each tool
    polling a cancellation flag internally, which none of them do."""
    db.update_batch(batch_id, stop_requested=1)
    michael.announce(
        "Stopping this batch — steps already running will finish, but I won't start "
        "any further steps on it.",
        batch_id=batch_id,
    )
    push_state(batch_id=batch_id)


def _stop_requested(batch_id):
    batch = db.get_batch(batch_id)
    return bool(batch and batch["stop_requested"])


def _mark_stopped(video_id, batch_id):
    db.update_video(video_id, status="FAILED", error_message="Stopped by user")
    db.update_batch(batch_id, status="STOPPED")
    push_state(video_id=video_id, batch_id=batch_id)


def request_video_edit(video_id, target_agent, field_updates):
    """A chat-driven change to one video already in WAITING_APPROVAL. Applies
    the field updates, pulls the whole batch out of WAITING_APPROVAL so
    Approve isn't clickable while this video is mid-reprocessing, and
    re-enters the pipeline at the right stage — reusing the already
    downloaded file instead of re-running Alex."""
    video = db.get_video(video_id)
    db.update_video(video_id, **field_updates)
    db.update_batch(video["batch_id"], status="IN_PROGRESS")
    push_state(video_id=video_id, batch_id=video["batch_id"])
    include_caption_step = target_agent == "Emma"
    _executor.submit(_safe_rerun_from_caption, video_id, include_caption_step)


def _safe_rerun_from_caption(video_id, include_caption_step):
    try:
        _process_from_caption(video_id, need_caption_initially=include_caption_step,
                               first_caption_pass=include_caption_step)
    except Exception as exc:
        video = db.get_video(video_id)
        db.update_video(video_id, status="FAILED", error_message=f"Unexpected error: {exc}")
        michael.announce(
            f"Video #{video['idx'] + 1} hit an unexpected error and stopped: {exc}. "
            f"You can retry it from the Task Board.",
            batch_id=video["batch_id"],
        )
        push_state(video_id=video_id, batch_id=video["batch_id"])


def approve_batch(batch_id):
    db.update_batch(batch_id, status="APPROVED")
    push_state(batch_id=batch_id)
    _executor.submit(_run_publish_batch, batch_id)


def _run_publish_batch(batch_id):
    try:
        sophia.publish_batch(batch_id)
    except Exception as exc:
        db.update_batch(batch_id, status="WAITING_APPROVAL")
        for video in db.list_videos(batch_id):
            if video["status"] == "PUBLISHING":
                db.update_video(video["id"], status="WAITING_APPROVAL")
        michael.announce(
            f"Publishing hit an unexpected error and stopped: {exc}. The batch is back in "
            f"review — approve again to retry.",
            batch_id=batch_id,
        )
        push_state(batch_id=batch_id)


def reject_batch(batch_id):
    db.update_batch(batch_id, status="REJECTED")
    michael.announce("Understood — I've marked this batch as rejected. Let me know what to change.",
                      batch_id=batch_id)
    push_state(batch_id=batch_id)


def _run_video_pipeline(video_id):
    # Submitted to a ThreadPoolExecutor whose Future result is never awaited,
    # so an unhandled exception here would otherwise vanish silently and
    # leave the video frozen in whatever status it was mid-step — showing as
    # "stuck" forever with no error and no Retry button. Convert that into a
    # real FAILED status instead.
    try:
        _run_video_pipeline_steps(video_id)
    except Exception as exc:
        video = db.get_video(video_id)
        db.update_video(video_id, status="FAILED", error_message=f"Unexpected error: {exc}")
        michael.announce(
            f"Video #{video['idx'] + 1} hit an unexpected error and stopped: {exc}. "
            f"You can retry it from the Task Board.",
            batch_id=video["batch_id"],
        )
        push_state(video_id=video_id, batch_id=video["batch_id"])


def _run_video_pipeline_steps(video_id):
    video = db.get_video(video_id)
    if _stop_requested(video["batch_id"]):
        _mark_stopped(video_id, video["batch_id"])
        return
    if not alex.run(video):
        return
    _process_from_caption(video_id, need_caption_initially=True, first_caption_pass=True)


def _process_from_caption(video_id, need_caption_initially, first_caption_pass):
    qa_loops = 0
    need_caption = need_caption_initially
    while True:
        video = db.get_video(video_id)
        if _stop_requested(video["batch_id"]):
            _mark_stopped(video_id, video["batch_id"])
            return
        if need_caption:
            # Only the first pass may reuse a manual/copied caption — a QA
            # failure means whatever caption was in place had a real
            # problem, so a forced retry always goes through generation.
            if not emma.run(video, allow_reuse=first_caption_pass):
                return
            first_caption_pass = False
            video = db.get_video(video_id)

        if not ryan.run(video):
            return
        video = db.get_video(video_id)

        qa_result = david.run(video)
        if qa_result is None:
            return
        if qa_result["passed"]:
            break

        qa_loops += 1
        if qa_loops > config.MAX_QA_RETRY_LOOPS:
            db.update_video(video_id, status="FAILED",
                             error_message=f"QA failed {qa_loops} times ({qa_result['failure_category']})")
            michael.announce(
                f"Video #{video['idx'] + 1} has failed QA {qa_loops} times "
                f"({qa_result['failure_category']}). I need your input on how to proceed.",
                batch_id=video["batch_id"],
            )
            push_state(video_id=video_id, batch_id=video["batch_id"])
            return

        need_caption = qa_result["failure_category"] in CAPTION_FAILURE_CATEGORIES
        target = "Emma" if need_caption else "Ryan"
        michael.announce(
            f"David found an issue with Video #{video['idx'] + 1} "
            f"({qa_result['failure_category']}). Sending it back to {target} for correction.",
            batch_id=video["batch_id"],
        )

    db.update_video(video_id, status="QA_PASSED")
    push_state(video_id=video_id, batch_id=video["batch_id"])
    _maybe_mark_batch_ready(video["batch_id"])


def _maybe_mark_batch_ready(batch_id):
    videos = db.list_videos(batch_id)
    if not videos or any(v["status"] not in ("QA_PASSED", "WAITING_APPROVAL") for v in videos):
        return
    for v in videos:
        if v["status"] == "QA_PASSED":
            db.update_video(v["id"], status="WAITING_APPROVAL")
    db.update_batch(batch_id, status="WAITING_APPROVAL")
    michael.announce(
        f"The batch is ready. David has passed all {len(videos)} videos through QA. "
        f"Please review them and approve the batch when you're ready.",
        batch_id=batch_id,
    )
    push_state(batch_id=batch_id)


if __name__ == "__main__":
    # ponytail self-check: the caption-vs-not routing rule, no DB/network involved.
    assert "caption" in CAPTION_FAILURE_CATEGORIES
    assert "watermark" not in CAPTION_FAILURE_CATEGORIES
    assert "framing" not in CAPTION_FAILURE_CATEGORIES
    print("orchestrator.py self-check OK")
