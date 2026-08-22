"""generate_caption(...) — Emma's tool. Extracts real frames from the exact
clip window, best-effort reuses editor/subtitle_utils.transcribe_clip for a
transcript snippet, and asks the vision LLM to write a caption based on the
user's per-video description/instructions."""
import os
import subprocess
import tempfile

from .. import llm
from ..external_apps import get_editor_app

SYSTEM_PROMPT = """You are Emma, a marketing/creative caption specialist for a short-form \
video content agency. You will see 3 frames sampled from a ~30 second vertical/square clip, \
optionally a transcript snippet, the video's title/uploader, and a description of this specific \
clip written by the human creator — it may describe what's happening, the tone to use, or plain \
style instructions (e.g. "short", "no hashtags", "Gen-Z", "no cringe"). Follow it exactly when given.

Reply with ONLY a JSON object: {"candidates": ["...", "...", "..."], "selected": "...", \
"reason": "one short sentence on why the selected candidate fits best"}. Produce 3-5 candidates. \
"selected" must be exactly one of the candidates."""


def _extract_frames(ffmpeg_path, video_path, start, duration, tmp_dir):
    # Middle frame first: it's the most representative, and llm.chat_vision
    # caps to NIM_VISION_MAX_IMAGES (some hosted VLMs reject >1 image).
    frames = []
    for frac in (0.5, 0.1, 0.85):
        t = start + duration * frac
        out_path = os.path.join(tmp_dir, f"cap_frame_{frac}.jpg")
        cmd = [ffmpeg_path, "-y", "-ss", f"{t:.3f}", "-i", video_path,
               "-frames:v", "1", "-q:v", "3", out_path]
        subprocess.run(cmd, capture_output=True)
        if os.path.exists(out_path):
            frames.append(out_path)
    return frames


def _best_effort_transcript(editor_app, video_path, start, end, tmp_dir):
    try:
        segments = editor_app.transcribe_clip(video_path, start, end, tmp_dir)
        return " ".join(text for _s, _e, text in segments).strip()
    except Exception:
        return ""


def generate_caption(video_path, start, duration, description, metadata):
    editor_app = get_editor_app()
    tmp_dir = tempfile.mkdtemp(prefix="agency_caption_")
    try:
        frames = _extract_frames(editor_app.FFMPEG_PATH, video_path, start, duration, tmp_dir)
        if not frames:
            return {"success": False, "error": "Could not extract frames from the clip window."}

        transcript = _best_effort_transcript(editor_app, video_path, start, start + duration, tmp_dir)

        user_prompt = (
            f"Video title: {metadata.get('title', '(unknown)')}\n"
            f"Uploader: {metadata.get('uploader', '(unknown)')}\n"
            f"Transcript snippet (may be empty): {transcript or '(none available)'}\n\n"
            f"Description / instructions for this clip: {description or '(none provided)'}\n\n"
            "Generate the caption candidates now."
        )

        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}]
        used_frames = True
        try:
            result = llm.chat_json(messages, image_paths=frames)
        except Exception:
            # Some hosted vision models refuse certain frames (e.g. real
            # people in shot) with a short non-JSON reply instead of an API
            # error — chat_json's retries just get refused again. Falling
            # back to a text-only pass (title/transcript/examples/style,
            # no frames) keeps Emma working instead of failing the video.
            used_frames = False
            try:
                result = llm.chat_json(messages)
            except Exception as exc:
                return {"success": False, "error": f"Caption generation failed: {exc}"}

        candidates = result.get("candidates") or []
        selected = result.get("selected") or (candidates[0] if candidates else "")
        if not selected:
            return {"success": False, "error": "LLM returned no usable caption candidates."}

        reason = result.get("reason", "")
        if not used_frames:
            reason = (reason + " (based on title/transcript only — the vision model "
                      "declined to process this clip's frames)").strip()
        return {"success": True, "candidates": candidates, "selected": selected, "reason": reason}
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    assert "candidates" in SYSTEM_PROMPT and "selected" in SYSTEM_PROMPT
    print("caption_tool.py self-check OK")
