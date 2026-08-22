"""Sophia — Social Media / Instagram Publishing Specialist. Stays idle
until the batch is explicitly APPROVED (checked by the orchestrator/route
before this is ever called), then really uploads each clip through the
tunneled media server, really polls Meta's processing status, and only
reports PUBLISHED on a real post id. A single-video batch publishes as a
normal post — Instagram's Carousel API rejects carousels with fewer than
2 children, so this never even attempts one."""
import os

from .. import db, live_progress, tunnel
from ..notify import emit, push_state
from ..tools import instagram_tool
from . import michael


def _revert_to_waiting_approval(batch_id, videos):
    """On any publish failure: batch goes back to WAITING_APPROVAL, and so
    does every video still marked PUBLISHING — otherwise they're stuck
    showing "Working" forever even though nothing is actually running."""
    db.update_batch(batch_id, status="WAITING_APPROVAL")
    for video in videos:
        if db.get_video(video["id"])["status"] == "PUBLISHING":
            db.update_video(video["id"], status="WAITING_APPROVAL")


def publish_batch(batch_id):
    batch = db.get_batch(batch_id)
    videos = db.list_videos(batch_id)
    is_single = len(videos) == 1

    status = instagram_tool.connection_status()
    if not status["connected"]:
        emit("Sophia", f"Can't publish: {status['reason']}", batch_id=batch_id)
        michael.announce(f"Sophia can't publish yet: {status['reason']} Once that's "
                          f"resolved, approve the batch again and she'll retry.", batch_id=batch_id)
        return {"success": False, "error": status["reason"]}

    db.update_batch(batch_id, status="PUBLISHING")
    push_state(batch_id=batch_id)
    emit("Sophia", "Uploading media" + (" for the carousel" if not is_single else ""), batch_id=batch_id)

    container_ids = []
    for video in videos:
        # A previous attempt at this same batch may have already uploaded
        # this video before a LATER video in the batch failed — without this,
        # every retry re-uploads every video from scratch (including ones
        # that already succeeded), which is exactly what burns through
        # Meta's rate limit fastest on multi-video batches specifically.
        # Containers expire 24h after creation, so this is only trusted
        # after Graph itself confirms it's still good right now.
        if video["ig_container_id"] and instagram_tool.container_still_usable(video["ig_container_id"]):
            emit("Sophia", f"Video #{video['idx'] + 1} was already uploaded — reusing it",
                 batch_id=batch_id, video_id=video["id"])
            container_ids.append(video["ig_container_id"])
            continue

        db.update_video(video["id"], status="PUBLISHING")
        live_progress.set_progress(video["id"], stage="uploading")
        push_state(video_id=video["id"], batch_id=batch_id)

        video_url = tunnel.get_public_media_url(batch_id, os.path.basename(video["final_path"]))
        if not video_url:
            live_progress.clear(video["id"])
            _revert_to_waiting_approval(batch_id, videos)
            emit("Sophia", "Lost the public media URL mid-publish (tunnel down)", batch_id=batch_id, video_id=video["id"])
            push_state(batch_id=batch_id)
            return {"success": False, "error": "Public media URL became unavailable during publishing."}

        result = instagram_tool.upload_instagram_media(video_url, is_carousel_item=not is_single)
        live_progress.clear(video["id"])
        if not result["success"]:
            _revert_to_waiting_approval(batch_id, videos)
            emit("Sophia", f"Failed to upload Video #{video['idx'] + 1}: {result['error']}",
                 batch_id=batch_id, video_id=video["id"])
            push_state(batch_id=batch_id)
            return {"success": False, "error": result["error"]}
        db.update_video(video["id"], ig_container_id=result["container_id"])
        container_ids.append(result["container_id"])
        emit("Sophia", f"Video #{video['idx'] + 1} uploaded", batch_id=batch_id, video_id=video["id"])

    if is_single:
        emit("Sophia", "Publishing single video post", batch_id=batch_id)
        published = instagram_tool.publish_instagram_carousel(container_ids[0])
    else:
        emit("Sophia", "Creating carousel", batch_id=batch_id)
        caption = next((v["caption_text"] for v in videos if v["caption_text"]), "")
        carousel = instagram_tool.create_instagram_carousel(container_ids, caption=caption)
        if not carousel["success"]:
            _revert_to_waiting_approval(batch_id, videos)
            emit("Sophia", f"Failed to create carousel: {carousel['error']}", batch_id=batch_id)
            push_state(batch_id=batch_id)
            return {"success": False, "error": carousel["error"]}

        emit("Sophia", "Publishing carousel", batch_id=batch_id)
        published = instagram_tool.publish_instagram_carousel(carousel["container_id"])

    if not published["success"]:
        _revert_to_waiting_approval(batch_id, videos)
        emit("Sophia", f"Failed to publish: {published['error']}", batch_id=batch_id)
        push_state(batch_id=batch_id)
        return {"success": False, "error": published["error"]}

    db.update_batch(batch_id, status="PUBLISHED", ig_post_id=published["post_id"])
    for video in videos:
        db.update_video(video["id"], status="PUBLISHED", ig_container_id=None)
    emit("Sophia", "Published successfully", batch_id=batch_id)
    michael.announce(f"The batch has been published to Instagram (post {published['post_id']}).",
                      batch_id=batch_id)
    push_state(batch_id=batch_id)
    return {"success": True, "post_id": published["post_id"]}


if __name__ == "__main__":
    # ponytail self-check: a video that already has a saved, still-valid
    # ig_container_id (from a previous attempt where a LATER video in the
    # batch failed) must be reused, not re-uploaded — this is the regression
    # that made every retry of a multi-video batch re-upload everything from
    # scratch, multiplying Graph API calls and burning the rate limit faster
    # than a single-video batch ever could.
    from unittest.mock import patch

    videos = [
        {"id": 1, "batch_id": 1, "idx": 0, "final_path": "a.mp4", "caption_text": "cap A", "ig_container_id": "already_uploaded"},
        {"id": 2, "batch_id": 1, "idx": 1, "final_path": "b.mp4", "caption_text": "", "ig_container_id": None},
    ]
    batch = {"id": 1, "watermark_enabled": 0, "watermark_text": ""}

    with patch("__main__.db") as mock_db, \
         patch("__main__.tunnel") as mock_tunnel, \
         patch("__main__.live_progress"), \
         patch("__main__.instagram_tool") as mock_ig, \
         patch("__main__.emit"), \
         patch("__main__.push_state"), \
         patch("__main__.michael"):
        mock_db.get_batch.return_value = batch
        mock_db.list_videos.return_value = videos
        mock_ig.connection_status.return_value = {"connected": True, "reason": None}
        mock_ig.container_still_usable.return_value = True
        mock_ig.upload_instagram_media.return_value = {"success": True, "container_id": "new_upload"}
        mock_ig.create_instagram_carousel.return_value = {"success": True, "container_id": "carousel_1"}
        mock_ig.publish_instagram_carousel.return_value = {"success": True, "post_id": "post_1"}
        mock_tunnel.get_public_media_url.return_value = "https://example.com/media/1/b.mp4"

        result = publish_batch(1)

        assert result == {"success": True, "post_id": "post_1"}
        mock_ig.upload_instagram_media.assert_called_once()  # only Video #2 — Video #1 was reused, not re-uploaded
        assert mock_ig.upload_instagram_media.call_args[0][0] == "https://example.com/media/1/b.mp4"
        mock_ig.create_instagram_carousel.assert_called_once_with(["already_uploaded", "new_upload"], caption="cap A")

    print("sophia.py self-check OK (an already-uploaded video is reused on retry, not re-uploaded)")
