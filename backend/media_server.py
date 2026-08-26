"""The ONLY thing ever tunneled publicly. Deliberately a separate, minimal
Flask app on its own port — just a health check and the final-clips media
route — so a Cloudflare tunnel can never expose the main app's chat/API/DB
or any secret (.env, tokens, source code). Runs via werkzeug's make_server
so it can be shut down cleanly instead of just app.run()."""
import os
import threading

from flask import Flask, jsonify, send_from_directory
from werkzeug.serving import make_server

from . import config

media_app = Flask(__name__)


@media_app.get("/health")
def health():
    return jsonify({"status": "ok"})


@media_app.get("/media/<int:batch_id>/<filename>")
def media(batch_id, filename):
    directory = os.path.join(config.batch_dir(batch_id), "final")
    return send_from_directory(directory, filename, conditional=True)


_server = None
_thread = None


def start(port=None):
    global _server, _thread
    if _server is not None:
        return
    # threaded=True is the fix, not a nicety: make_server defaults to
    # single-threaded, so while it's mid-stream sending a large video to
    # Instagram's fetcher, it can't also answer the tunnel monitor's /health
    # ping — that ping times out, the monitor decides the tunnel is dead,
    # and restarts it mid-download, aborting Instagram's in-flight fetch
    # (surfaces there as "Media processing failed (status=ERROR)").
    _server = make_server("127.0.0.1", port or config.MEDIA_SERVER_PORT, media_app, threaded=True)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()


def stop():
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server = None
        _thread = None


if __name__ == "__main__":
    rules = {rule.rule for rule in media_app.url_map.iter_rules()}
    assert "/health" in rules
    assert "/media/<int:batch_id>/<filename>" in rules
    print("media_server.py self-check OK (only /health and /media/* are exposed)")
