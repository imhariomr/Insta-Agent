"""Michael — Manager. The user's only direct contact. His chat replies are
grounded in a real state summary pulled from the database (never invented),
and his "announcements" are the same messages that land in the activity
feed, just also surfaced in the chat panel.

Michael can also route a small, explicit set of chat-requested changes to
the right agent (e.g. "make the caption not bold" -> Ryan, "change what the
caption says" -> Emma) for a video in a batch that's WAITING_APPROVAL. The
LLM only ever proposes the intent as structured JSON — this module validates
it against a strict allow-list before touching the database, and always
writes the confirmation message itself rather than trusting the LLM's own
wording, so chat can never claim to have done something it didn't."""
from .. import db, llm
from ..notify import emit, push_state

PERSONA = """You are Michael, the project manager of a small AI content production team:
Alex (YouTube download engineer), Emma (marketing/caption specialist), Ryan (video editor),
David (QA engineer), and Sophia (social media/Instagram specialist). You talk to your human
client in a natural, professional, concise tone - like a competent PM giving a status update,
not a chatbot.

CRITICAL RULES:
- Only state facts present in the CURRENT STATE SUMMARY below. Never invent progress,
  statuses, numbers, or completed steps that aren't in it.
- Never claim anything has been published to Instagram, or will be published, unless the
  summary shows the batch is already APPROVED or later — publishing requires the human's
  explicit approval and never happens automatically.
- If asked something the summary doesn't answer, say you don't have that yet rather than
  guessing.
- Keep replies short: a few sentences, not a report.

You can also route a small set of concrete change requests for one video in the batch that is
currently WAITING_APPROVAL (if any — check the summary). Reply with ONLY a JSON object:
{"reply": "<your natural reply>", "route": null or {
  "video_number": <the video's number as shown, e.g. 1 for "Video #1">,
  "target_agent": "Ryan" or "Emma",
  "field_updates": {...}
}}

Only set "route" (non-null) when the request is CLEARLY one of these exact kinds, for a video
that exists in the batch currently WAITING_APPROVAL:
- "don't make the caption bold" / "remove the bold" -> target_agent "Ryan", field_updates {"caption_bold": false}
- "make the caption bold" -> target_agent "Ryan", field_updates {"caption_bold": true}
- "change what the caption says / its wording / tone / make it funnier / shorter" etc ->
  target_agent "Emma", field_updates {"description": "<instructions for Emma, in your own words>"}

If there's no batch WAITING_APPROVAL, the request doesn't match one of these exact kinds, or
you can't tell which video number they mean, set "route" to null and just answer/ask normally
in "reply" — do not say you're making a change unless "route" is actually set, since only a
non-null "route" that passes validation actually does anything."""

ROUTE_ALLOWED_FIELDS = {
    "Ryan": {"caption_bold"},
    "Emma": {"description"},
}


def _state_summary(state):
    if not state["batches"]:
        return "No batches created yet."
    lines = []
    for b in state["batches"]:
        lines.append(f"Batch #{b['id']}: status={b['status']}, resolution={b['resolution']}, "
                      f"watermark={'on' if b['watermark_enabled'] else 'off'}")
        for v in [v for v in state["videos"] if v["batch_id"] == b["id"]]:
            detail = f"  Video #{v['idx'] + 1}: status={v['status']}"
            if v["title"]:
                detail += f", title={v['title']!r}"
            if v["caption_text"]:
                detail += f", caption={v['caption_text']!r}, caption_bold={bool(v['caption_bold'])}"
            if v["error_message"]:
                detail += f", error={v['error_message']!r}"
            lines.append(detail)
    recent = state["events"][-15:]
    if recent:
        lines.append("Recent activity:")
        for e in recent:
            lines.append(f"  [{e['agent']}] {e['message']}")
    return "\n".join(lines)


def _apply_route(route, state):
    """Returns a confirmation string on success, or None if the route was
    missing/invalid/unsafe — the caller falls back to the LLM's own reply."""
    if not isinstance(route, dict):
        return None
    target_agent = route.get("target_agent")
    video_number = route.get("video_number")
    field_updates = route.get("field_updates")
    allowed = ROUTE_ALLOWED_FIELDS.get(target_agent)
    if not allowed or not isinstance(video_number, int) or not isinstance(field_updates, dict):
        return None

    batch = next((b for b in state["batches"] if b["status"] == "WAITING_APPROVAL"), None)
    if not batch:
        return None
    video = next((v for v in state["videos"]
                  if v["batch_id"] == batch["id"] and v["idx"] == video_number - 1), None)
    if not video:
        return None

    updates = {k: v for k, v in field_updates.items() if k in allowed}
    if not updates:
        return None
    if target_agent == "Ryan":
        updates["caption_bold"] = bool(updates.get("caption_bold", video["caption_bold"]))
    if target_agent == "Emma":
        updates["description"] = str(updates.get("description", "")).strip()
        if not updates["description"]:
            return None
        # Force real regeneration instead of reusing the old caption/copy.
        updates["caption_text"] = None
        updates["caption_candidates_json"] = None
        updates["copied_from_idx"] = None

    from .. import orchestrator  # lazy: orchestrator imports this module
    orchestrator.request_video_edit(video["id"], target_agent, updates)
    return (f"Got it — sending Video #{video_number} to {target_agent} to fix that. "
            f"I'll let you know once it's ready again.")


def chat_reply(user_message):
    db.add_chat_message("user", user_message)
    push_state()

    state = db.full_state()
    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": f"CURRENT STATE SUMMARY:\n{_state_summary(state)}"},
        {"role": "user", "content": user_message},
    ]
    try:
        result = llm.chat_json(messages)
        reply = (result.get("reply") or "").strip() or "Got it."
        applied = _apply_route(result.get("route"), state)
        if applied:
            reply = applied
    except Exception as exc:
        # This must never raise back into the Flask route — an unhandled
        # exception here previously meant the request 500'd, the frontend's
        # fetch silently failed, and the user saw no reply at all with no
        # error either, looking exactly like "Michael doesn't respond."
        reply = f"Sorry, I hit an error processing that ({exc})."

    db.add_chat_message("michael", reply)
    push_state()
    return reply


def announce(message, batch_id=None):
    db.add_chat_message("michael", message)
    emit("Michael", message, batch_id=batch_id)
    push_state(batch_id=batch_id)
