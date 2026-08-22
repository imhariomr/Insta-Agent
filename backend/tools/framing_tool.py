"""analyze_framing(...) — decides the crop_x/crop_y/zoom Ryan should hand
to editor_tool.create_video_clip before cropping the source to a square.
Looks at one real frame from the clip window and asks: (1) does the source
already have its own burned-in watermark/logo baked in near an edge? if so
zoom in a bit so cropping pushes it out of frame. (2) is the main subject
off-center? if so shift the crop window to re-center them, instead of
always doing a plain centered crop. video_utils.py's crop_x/crop_y/zoom
support already existed — this is what decides the values to feed it."""
import os
import subprocess
import tempfile

from .. import llm
from ..external_apps import get_editor_app

SYSTEM_PROMPT = """You are a video editor's framing assistant. You will see ONE frame from a \
video that is about to be cropped from its original aspect ratio down to a 1:1 square for \
Instagram. Answer two questions about ONLY what's visible in this frame:

1. Does the video already have its own burned-in watermark/logo/username baked into the footage \
(e.g. a channel logo, a corner watermark, a subscribe button) — not counting normal on-screen \
content like subtitles? If yes, which corner is it in?
2. Where is the main subject (the person, or the visual focal point) positioned in the frame?

Reply with ONLY a JSON object:
{"watermark_detected": bool, "watermark_corner": "top-left"|"top-right"|"bottom-left"|"bottom-right"|null,
 "subject_h_pos": "left"|"center"|"right", "subject_v_pos": "top"|"middle"|"bottom",
 "reason": "one short sentence"}"""

# Bias the crop window toward whichever side the subject is on, so they end
# up centered in the narrower square output instead of clipped to one side.
_H_POS_TO_CROP_X = {"left": 0.2, "center": 0.5, "right": 0.8}
_V_POS_TO_CROP_Y = {"top": 0.2, "middle": 0.5, "bottom": 0.8}
# A source watermark sits near an edge; zooming in crops a bit off every
# edge, which is enough to push a small corner watermark out of frame
# without needing to target one exact corner.
WATERMARK_ZOOM = 1.2

DEFAULT_FRAMING = {"crop_x": 0.5, "crop_y": 0.5, "zoom": 1.0}


def _extract_frame(ffmpeg_path, video_path, timestamp, out_path):
    cmd = [ffmpeg_path, "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
           "-frames:v", "1", "-q:v", "3", out_path]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(out_path)


def analyze_framing(video_path, start, duration):
    """Returns {crop_x, crop_y, zoom, watermark_detected, reason}. Falls
    back to a plain centered/no-zoom crop (today's behavior) if the frame
    can't be extracted or the vision model can't/won't judge it — this
    never blocks the edit, it only ever refines it."""
    editor_app = get_editor_app()
    tmp_dir = tempfile.mkdtemp(prefix="agency_framing_")
    try:
        frame_path = os.path.join(tmp_dir, "frame.jpg")
        if not _extract_frame(editor_app.FFMPEG_PATH, video_path, start + duration / 2, frame_path):
            return {**DEFAULT_FRAMING, "watermark_detected": False, "reason": "could not extract a frame"}

        try:
            result = llm.chat_json(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": "Analyze this frame."}],
                image_paths=[frame_path],
            )
        except Exception as exc:
            return {**DEFAULT_FRAMING, "watermark_detected": False,
                    "reason": f"vision model unavailable: {exc}"}

        crop_x = _H_POS_TO_CROP_X.get(result.get("subject_h_pos"), 0.5)
        crop_y = _V_POS_TO_CROP_Y.get(result.get("subject_v_pos"), 0.5)
        watermark_detected = bool(result.get("watermark_detected"))
        zoom = WATERMARK_ZOOM if watermark_detected else 1.0

        return {
            "crop_x": crop_x, "crop_y": crop_y, "zoom": zoom,
            "watermark_detected": watermark_detected,
            "watermark_corner": result.get("watermark_corner"),
            "reason": result.get("reason", ""),
        }
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    assert _H_POS_TO_CROP_X["left"] < _H_POS_TO_CROP_X["center"] < _H_POS_TO_CROP_X["right"]
    assert WATERMARK_ZOOM > 1.0
    print("framing_tool.py self-check OK")
