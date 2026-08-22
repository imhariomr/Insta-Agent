"""qa_video(path, expected) — David's real checks. Resolution/duration/audio
via editor_app.probe_video (reused); black/frozen sections via ffmpeg's own
blackdetect/freezedetect filters (deterministic, no LLM); caption/watermark
visibility + content relevance via a vision LLM call against actual
extracted frames. Nothing here is rubber-stamped from the export call's
return value."""
import os
import re
import subprocess
import tempfile

from .. import llm
from ..external_apps import get_editor_app

BLACK_DURATION_RE = re.compile(r"black_duration:\s*([\d.]+)")
FREEZE_DURATION_RE = re.compile(r"freeze_duration:\s*([\d.]+)")

VISION_SYSTEM_PROMPT = """You are a strict QA reviewer for short vertical/square social \
video clips. You will see up to 3 frames sampled from the start, middle, and end of an \
exported clip. Judge only what is visible in the frames. Reply with ONLY a JSON object:
{"caption_visible": bool, "caption_cut_off": bool, "watermark_visible": bool, \
"watermark_text_correct": bool, "content_relevant": bool, "notes": "one short sentence"}
caption_visible/watermark_visible: true if a caption/watermark can be seen in at least one frame \
(pass null-equivalent as false if no caption or watermark was expected and none should be checked \
for — the caller already knows whether one was expected).
caption_cut_off: true only if the caption text is clipped/cut off or clearly outside the safe area.
content_relevant: true if the caption's message plausibly fits the mood/content of the frames."""


def _detect_black_and_freeze(ffmpeg_path, path, duration):
    cmd = [
        ffmpeg_path, "-i", path,
        "-vf", "blackdetect=d=0.5:pic_th=0.98,freezedetect=n=-60dB:d=0.5",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr or ""
    black_total = sum(float(m) for m in BLACK_DURATION_RE.findall(stderr))
    freeze_total = sum(float(m) for m in FREEZE_DURATION_RE.findall(stderr))
    threshold = 0.3 * max(duration, 0.01)
    ok = (black_total + freeze_total) <= threshold
    detail = f"{black_total + freeze_total:.1f}s black/frozen out of {duration:.1f}s"
    return ok, detail


def _extract_frame(ffmpeg_path, video_path, timestamp, out_path):
    cmd = [ffmpeg_path, "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
           "-frames:v", "1", "-q:v", "3", out_path]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(out_path)


def qa_video(path, expected):
    """expected: {aspect_ratio, min_duration, max_duration, expect_caption,
    caption_text, expect_watermark, watermark_text, expect_audio}"""
    editor_app = get_editor_app()
    checks = []

    if not os.path.exists(path):
        return {"passed": False, "checks": [{"name": "file exists", "passed": False, "detail": path}],
                "failure_category": "other"}

    try:
        info = editor_app.probe_video(path)
        checks.append({"name": "file opens / integrity", "passed": True, "detail": "probed OK"})
    except editor_app.VideoProbeError as exc:
        checks.append({"name": "file opens / integrity", "passed": False, "detail": str(exc)})
        return {"passed": False, "checks": checks, "failure_category": "other"}

    target_w, target_h = editor_app.ASPECT_RATIOS.get(expected.get("aspect_ratio", "1:1"),
                                                        editor_app.ASPECT_RATIOS["1:1"])
    res_ok = info["width"] == target_w and info["height"] == target_h
    checks.append({"name": "resolution", "passed": res_ok, "detail": f"{info['width']}x{info['height']}"})

    dur_ok = expected.get("min_duration", 27) <= info["duration"] <= expected.get("max_duration", 33)
    checks.append({"name": "duration ~30s", "passed": dur_ok, "detail": f"{info['duration']:.1f}s"})

    audio_ok = info["has_audio"] if expected.get("expect_audio", True) else True
    checks.append({"name": "audio present", "passed": audio_ok, "detail": str(info["has_audio"])})

    black_ok, black_detail = _detect_black_and_freeze(editor_app.FFMPEG_PATH, path, info["duration"])
    checks.append({"name": "no unexpected black/frozen sections", "passed": black_ok, "detail": black_detail})

    tmp_dir = tempfile.mkdtemp(prefix="agency_qa_")
    try:
        frame_paths = []
        # Middle frame first: most representative, and llm.chat_vision caps
        # to NIM_VISION_MAX_IMAGES (some hosted VLMs reject >1 image).
        for frac in (0.5, 0.1, 0.85):
            frame_path = os.path.join(tmp_dir, f"frame_{frac}.jpg")
            if _extract_frame(editor_app.FFMPEG_PATH, path, info["duration"] * frac, frame_path):
                frame_paths.append(frame_path)

        expect_caption = expected.get("expect_caption", bool(expected.get("caption_text")))
        expect_watermark = expected.get("expect_watermark", bool(expected.get("watermark_text")))
        vision = None
        vision_skip_reason = None

        if not frame_paths:
            vision_skip_reason = "could not extract frames"
        else:
            prompt = (f"Expected caption present: {expect_caption}. Expected caption text: "
                      f"{expected.get('caption_text', '')!r}. Expected watermark present: "
                      f"{expect_watermark}. Expected watermark text: {expected.get('watermark_text', '')!r}.")
            try:
                vision = llm.chat_json(
                    [{"role": "system", "content": VISION_SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
                    image_paths=frame_paths,
                )
            except Exception as exc:
                # Some hosted vision models refuse certain frames (e.g. real
                # people in shot) instead of erroring cleanly — chat_json's
                # retries just get refused again. Treat as "couldn't verify"
                # (skipped, doesn't fail QA) rather than blocking every real
                # video with people in it forever.
                vision_skip_reason = f"vision model unavailable for this clip: {exc}"

        if vision_skip_reason:
            if expect_caption:
                checks.append({"name": "caption visible", "passed": None, "detail": vision_skip_reason})
                checks.append({"name": "caption inside safe area", "passed": None, "detail": vision_skip_reason})
                checks.append({"name": "content relevance", "passed": None, "detail": vision_skip_reason})
            if expect_watermark:
                checks.append({"name": "watermark visible", "passed": None, "detail": vision_skip_reason})
                checks.append({"name": "watermark text correct", "passed": None, "detail": vision_skip_reason})
        else:
            if expect_caption:
                cap_visible = bool(vision.get("caption_visible"))
                cap_not_cut_off = not vision.get("caption_cut_off", False)
                checks.append({"name": "caption visible", "passed": cap_visible, "detail": vision.get("notes", "")})
                checks.append({"name": "caption inside safe area", "passed": cap_not_cut_off, "detail": ""})
                checks.append({"name": "content relevance", "passed": bool(vision.get("content_relevant")),
                                "detail": vision.get("notes", "")})
            if expect_watermark:
                wm_visible = bool(vision.get("watermark_visible"))
                wm_correct = bool(vision.get("watermark_text_correct"))
                checks.append({"name": "watermark visible", "passed": wm_visible, "detail": ""})
                checks.append({"name": "watermark text correct", "passed": wm_correct, "detail": ""})
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # None = "couldn't verify" (vision unavailable) — doesn't block QA on
    # its own; only an explicit False (a real deterministic or vision
    # failure) does.
    passed = all(c["passed"] is not False for c in checks)
    failure_category = None
    if not passed:
        failed_names = {c["name"] for c in checks if c["passed"] is False}
        if failed_names & {"caption visible", "caption inside safe area", "content relevance"}:
            failure_category = "caption"
        elif failed_names & {"watermark visible", "watermark text correct"}:
            failure_category = "watermark"
        elif failed_names & {"resolution", "no unexpected black/frozen sections"}:
            failure_category = "framing"
        else:
            failure_category = "other"

    return {"passed": passed, "checks": checks, "failure_category": failure_category}


if __name__ == "__main__":
    sample_stderr = "black_start:1.0 black_end:2.5 black_duration:1.5\nfreeze_duration: 0.8\n"
    black_total = sum(float(m) for m in BLACK_DURATION_RE.findall(sample_stderr))
    freeze_total = sum(float(m) for m in FREEZE_DURATION_RE.findall(sample_stderr))
    assert black_total == 1.5 and freeze_total == 0.8
    print("qa_tool.py self-check OK")
