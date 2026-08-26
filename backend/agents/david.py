"""David — QA Engineer. Actually inspects the exported file; returns the
full structured report (not just pass/fail) so the orchestrator can route
a failure back to the right person and the UI can show the checklist."""
import json

from .. import db
from ..notify import emit, push_state
from ..tools import qa_tool


def run(video):
    video_id, batch_id, idx = video["id"], video["batch_id"], video["idx"]
    batch = db.get_batch(batch_id)

    db.update_video(video_id, status="QA")
    push_state(video_id=video_id, batch_id=batch_id)
    emit("David", f"Reviewing Video #{idx + 1}", batch_id=batch_id, video_id=video_id)

    expected = {
        "aspect_ratio": video["aspect_ratio"] or "1:1", "min_duration": 22, "max_duration": 28,
        "expect_caption": bool(video["caption_text"]), "caption_text": video["caption_text"] or "",
        "expect_watermark": bool(batch["watermark_enabled"]), "watermark_text": batch["watermark_text"] or "",
        "expect_audio": True,
    }

    try:
        result = qa_tool.qa_video(video["final_path"], expected)
    except Exception as exc:
        db.update_video(video_id, status="FAILED", error_message=f"QA crashed: {exc}")
        emit("David", f"QA crashed on Video #{idx + 1}: {exc}", batch_id=batch_id, video_id=video_id)
        push_state(video_id=video_id, batch_id=batch_id)
        return None

    db.update_video(
        video_id, status="QA_PASSED" if result["passed"] else "QA_FAILED",
        qa_report_json=json.dumps(result),
    )
    if result["passed"]:
        emit("David", f"QA PASSED for Video #{idx + 1}", batch_id=batch_id, video_id=video_id)
    else:
        emit("David", f"QA FAILED for Video #{idx + 1}: {result['failure_category']}",
             batch_id=batch_id, video_id=video_id)
    push_state(video_id=video_id, batch_id=batch_id)
    return result
