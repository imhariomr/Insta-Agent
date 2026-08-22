"""Shared helper every agent stage uses: persist a real activity-feed event
and tell any connected browser the state changed (client re-fetches
/api/state — simplest thing that works at this scale, no delta-merging)."""
from . import db, events


def emit(agent, message, batch_id=None, video_id=None):
    db.add_event(agent, message, batch_id=batch_id, video_id=video_id)
    events.publish({"type": "event", "agent": agent, "message": message,
                     "batch_id": batch_id, "video_id": video_id})


def push_state(batch_id=None, video_id=None):
    events.publish({"type": "state_changed", "batch_id": batch_id, "video_id": video_id})
