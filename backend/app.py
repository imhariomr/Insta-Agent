"""Flask app: office UI pages, JSON API, and the SSE live-update stream.
Final clips for in-browser preview are served here too, but the copy
Instagram's Graph API fetches from goes through the separate, minimal
media_server (see media_server.py) tunneled by tunnel.py — this app itself
is never exposed publicly."""
import json
import os
import threading

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from . import cleanup, config, db, events, live_progress, media_server, orchestrator, timeparse, tunnel
from .agents import michael
from .tools import instagram_tool

_TUNNEL_CHAT_MESSAGES = {
    "active": "The media tunnel is online — Instagram can reach locally processed videos.",
    "reconnecting": "The media tunnel went offline. I'm restarting it before continuing.",
    "unavailable": "The media tunnel isn't reachable right now.",
    "not_installed": "cloudflared isn't installed, so I can't open a public media tunnel yet. "
                      "Instagram publishing will stay blocked until it's set up.",
    "fixed": "Using the configured fixed public media URL.",
}


def _on_tunnel_status_change(status, detail):
    message = _TUNNEL_CHAT_MESSAGES.get(status)
    if not message:
        events.publish({"type": "state_changed"})
        return
    if detail and status in ("reconnecting", "unavailable"):
        message = f"{message} ({detail})"
    michael.announce(message)

app = Flask(
    __name__,
    template_folder=os.path.join(config.BASE_DIR, "templates"),
    static_folder=os.path.join(config.BASE_DIR, "static"),
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    data = db.full_state()
    data["live_progress"] = live_progress.get_all()
    ig_status = instagram_tool.connection_status()
    ig_status["tunnel_status"] = tunnel.get_manager().status
    ig_status["tunnel_url"] = tunnel.get_public_base_url()
    data["instagram"] = ig_status
    data["settings"] = {
        "nim_text_model": config.NIM_TEXT_MODEL,
        "nim_vision_model": config.NIM_VISION_MODEL,
        "cleanup_retention": f"{cleanup.RETENTION_SECONDS // 3600}h ({', '.join(cleanup.TERMINAL_STATUSES)})",
    }
    return jsonify(data)


@app.route("/api/instagram/test", methods=["POST"])
def api_instagram_test():
    mgr = tunnel.get_manager()
    tunnel_ok = mgr.health_check_now() if mgr.get_public_url() else False
    status = instagram_tool.connection_status()
    return jsonify({"tunnel_reachable": tunnel_ok, **status})


@app.route("/api/stream")
def api_stream():
    q = events.subscribe()

    def gen():
        try:
            while True:
                event = q.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            events.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/batches", methods=["POST"])
def api_create_batch():
    data = request.get_json(silent=True) or {}
    videos_in = [v for v in (data.get("videos") or []) if (v.get("url") or "").strip()]
    if not videos_in:
        return jsonify({"error": "Add at least one video with a URL."}), 400

    resolution = (data.get("resolution") or "720p").strip()
    watermark_enabled = bool(data.get("watermark_enabled", True))
    watermark_text = (data.get("watermark_text") or "").strip()

    batch_id = db.create_batch(resolution, watermark_enabled, watermark_text)
    for idx, v in enumerate(videos_in):
        start_seconds = timeparse.parse_timestamp(v.get("start_time", 0))
        caption_text = (v.get("caption") or "").strip() or None
        description = (v.get("description") or "").strip()
        copied_from_idx = v.get("copied_from")
        if not isinstance(copied_from_idx, int) or not (0 <= copied_from_idx < idx):
            copied_from_idx = None
        db.add_video(batch_id, idx, v["url"].strip(), start_seconds,
                     caption_text=caption_text, description=description, copied_from_idx=copied_from_idx)

    orchestrator.start_batch(batch_id)
    return jsonify({"batch_id": batch_id})


@app.route("/api/batches/<int:batch_id>/approve", methods=["POST"])
def api_approve(batch_id):
    batch = db.get_batch(batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404
    if batch["status"] != "WAITING_APPROVAL":
        return jsonify({"error": f"Batch isn't waiting for approval (status={batch['status']})."}), 400
    orchestrator.approve_batch(batch_id)
    return jsonify({"success": True})


@app.route("/api/batches/<int:batch_id>/reject", methods=["POST"])
def api_reject(batch_id):
    if not db.get_batch(batch_id):
        return jsonify({"error": "Batch not found."}), 404
    orchestrator.reject_batch(batch_id)
    return jsonify({"success": True})


@app.route("/api/videos/<int:video_id>/retry", methods=["POST"])
def api_retry_video(video_id):
    if not db.get_video(video_id):
        return jsonify({"error": "Video not found."}), 404
    orchestrator.retry_video(video_id)
    return jsonify({"success": True})


@app.route("/api/batches/<int:batch_id>/stop", methods=["POST"])
def api_stop_batch(batch_id):
    batch = db.get_batch(batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404
    if batch["status"] in ("PUBLISHED", "REJECTED", "STOPPED"):
        return jsonify({"error": f"Batch is already {batch['status'].lower()}."}), 400
    orchestrator.stop_batch(batch_id)
    return jsonify({"success": True})


@app.route("/api/batches/<int:batch_id>/delete", methods=["DELETE"])
def api_delete_batch(batch_id):
    batch = db.get_batch(batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404
    if batch["status"] in ("IN_PROGRESS", "PUBLISHING"):
        return jsonify({"error": "Stop the batch first before deleting it."}), 400
    db.delete_batch(batch_id)
    events.publish({"type": "state_changed"})
    return jsonify({"success": True})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is empty."}), 400
    reply = michael.chat_reply(message)
    return jsonify({"reply": reply})


@app.route("/media/<int:batch_id>/<filename>")
def media(batch_id, filename):
    directory = os.path.join(config.batch_dir(batch_id), "final")
    return send_from_directory(directory, filename, conditional=True)


def main():
    db.init_db()
    removed = cleanup.cleanup_old_batches()
    if removed:
        print(f"[cleanup] removed {len(removed)} finished batch(es) older than "
              f"{cleanup.RETENTION_SECONDS // 3600}h: {removed}")

    media_server.start()

    mgr = tunnel.get_manager()
    mgr.set_status_callback(_on_tunnel_status_change)
    threading.Thread(target=mgr.start, daemon=True).start()  # don't block app startup on the tunnel

    try:
        app.run(host="127.0.0.1", port=5100, debug=True, threaded=True, use_reloader=False)
    finally:
        mgr.stop()
        media_server.stop()


if __name__ == "__main__":
    main()
