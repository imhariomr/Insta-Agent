"""Ephemeral (not persisted) per-video progress, same pattern the existing
yt/editor apps already use for their own progress bars — real percentages
from real yt-dlp/ffmpeg callbacks, just not worth a DB write per tick."""
import threading

from . import events

_progress = {}
_lock = threading.Lock()


def set_progress(video_id, **fields):
    with _lock:
        entry = _progress.setdefault(video_id, {})
        entry.update(fields)
        snapshot = dict(entry)
    events.publish({"type": "progress", "video_id": video_id, **snapshot})


def clear(video_id):
    with _lock:
        _progress.pop(video_id, None)


def get_all():
    with _lock:
        return {str(k): dict(v) for k, v in _progress.items()}
