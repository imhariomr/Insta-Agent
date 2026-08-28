"""Lets a human take over one pending video by hand in the real local video
editor UI instead of describing a fix in words. The editor's Flask app is
already imported in-process (see external_apps.get_editor_app) purely as a
library of functions; this module is the one place that actually starts it
as a real server (lazily, on first use) so a browser tab can open it, and
registers a video directly into its in-memory VIDEOS dict — skipping the
upload step — by copying the file server-side instead of re-uploading it."""
import os
import shutil
import threading
import uuid

from werkzeug.serving import make_server

from . import config, db
from .external_apps import get_editor_app
from .tools import editor_tool

_server = None
_lock = threading.Lock()


def _ensure_running():
    global _server
    with _lock:
        if _server is not None:
            return
        editor_app = get_editor_app()
        try:
            server = make_server("127.0.0.1", config.EDITOR_APP_PORT, editor_app.app)
        except OSError as exc:
            raise RuntimeError(
                f"Port {config.EDITOR_APP_PORT} is already in use — if you have the video "
                f"editor app running separately (e.g. `python app.py` in its own folder), "
                f"close that first so this app can manage it instead. ({exc})"
            ) from exc
        _server = server
        threading.Thread(target=_server.serve_forever, daemon=True).start()


def open_for_manual_edit(video, agency_base):
    """Copies video['downloaded_path'] (the raw, un-cropped, un-captioned
    source — NOT final_path, which already has Ryan's caption/crop/filter
    burned in; reopening that and re-applying the same overlay on export
    would burn the caption in a second time, on top of itself) into the
    editor's own upload directory and registers it exactly like a real
    /api/upload would, so the editor's frontend can preload it with no
    re-upload. Also stashes every caption/style/trim setting this video was
    actually rendered with as 'agency_cfg' on that same registry entry —
    /api/videos/<id>/info hands it back so the editor's UI can populate its
    controls (including the trim range) with the real current state instead
    of opening to blank defaults. Returns the URL to open."""
    editor_app = get_editor_app()
    _ensure_running()

    src_path = video["downloaded_path"]
    if not src_path or not os.path.exists(src_path):
        raise FileNotFoundError("This video hasn't finished downloading yet.")

    editor_video_id = uuid.uuid4().hex
    ext = os.path.splitext(src_path)[1].lstrip(".") or "mp4"
    stored_path = os.path.join(editor_app.UPLOAD_DIR, f"{editor_video_id}.{ext}")
    shutil.copyfile(src_path, stored_path)

    meta = editor_app.probe_video(stored_path)
    batch = db.get_batch(video["batch_id"])
    agency_cfg = {
        "text": video["caption_text"] or "",
        "bold": bool(video["caption_bold"]),
        "font_family": video["font_family"] or "poppins",
        "font_color": video["font_color"] or "white",
        "style": video["caption_style"] or "band",
        "position": video["caption_position"] or "top",
        "video_filter": video["video_filter"] or "none",
        "aspect_ratio": video["aspect_ratio"] or "1:1",
        "watermark_enabled": bool(batch["watermark_enabled"]),
        "watermark_text": batch["watermark_text"] or "",
        "start": video["start_time_seconds"],
        "clip_duration": config.CLIP_DURATION_SECONDS,
        "font_size": editor_tool.CAPTION_FONT_SIZE,
    }
    with editor_app.VIDEOS_LOCK:
        editor_app.VIDEOS[editor_video_id] = {
            "path": stored_path,
            "original_name": os.path.basename(src_path),
            "agency_cfg": agency_cfg,
            **meta,
        }

    editor_url = (
        f"http://127.0.0.1:{config.EDITOR_APP_PORT}/?preload={editor_video_id}"
        f"&agency={video['id']}&agencyBase={agency_base}"
    )
    return editor_url


def resolve_export_path(job_id):
    """Reads the finished export's output path directly out of the editor's
    own in-memory JOBS dict — safe because _ensure_running made it the same
    live Python process/module this backend already imports, so no HTTP
    round trip (or knowledge of the editor's export URL scheme) is needed."""
    editor_app = get_editor_app()
    with editor_app.JOBS_LOCK:
        job = editor_app.JOBS.get(job_id)
    if not job or not job.get("done") or job.get("error") or not job.get("output_path"):
        raise ValueError("That export job isn't finished (or failed) — nothing to sync yet.")
    return job["output_path"]


if __name__ == "__main__":
    assert hasattr(get_editor_app(), "VIDEOS")
    print("editor_bridge.py self-check OK (module loads, editor app reachable)")
