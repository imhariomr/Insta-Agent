"""create_video_clip(...) / inspect_video(...) — reuse editor/app.py's
probe_video, _build_base_overlays (the existing caption + watermark
rendering) and run_export (the existing trim/crop/burn-in ffmpeg pass)
directly. No overlay math or ffmpeg filter graph is re-derived here."""
import os
import shutil
import tempfile

from ..external_apps import get_editor_app


def inspect_video(path):
    editor_app = get_editor_app()
    return editor_app.probe_video(path)


def create_video_clip(input_file, start_time, duration=30, aspect_ratio="1:1",
                       caption="", caption_bold=True, watermark_enabled=True, watermark_text="",
                       dest_path=None, on_progress=None,
                       crop_x=0.5, crop_y=0.5, zoom=1.0):
    editor_app = get_editor_app()

    try:
        info = editor_app.probe_video(input_file)
    except editor_app.VideoProbeError as exc:
        return {"success": False, "error": f"Could not read source video: {exc}"}

    src_duration = info["duration"]
    start = max(0.0, min(float(start_time), src_duration))
    end = min(start + duration, src_duration)
    if end - start < 0.3:
        return {"success": False, "error": "Clip window is too short — source video ends too soon after this start time."}

    canvas_w, canvas_h = editor_app.ASPECT_RATIOS.get(aspect_ratio, editor_app.ASPECT_RATIOS["1:1"])
    cfg = {
        "text": caption or "", "font_size": 44, "bold": bool(caption_bold), "align": "center",
        "style": "band", "position": "top",
        "watermark_text": (watermark_text or "") if watermark_enabled else "",
    }

    tmp_dir = tempfile.mkdtemp(prefix="agency_clip_")
    try:
        video_h, video_y, overlays, _bottom_limit = editor_app._build_base_overlays(
            tmp_dir, canvas_w, canvas_h, cfg
        )

        output_path = dest_path or os.path.join(tmp_dir, "clip.mp4")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        editor_app.run_export(
            input_path=input_file, output_path=output_path,
            start=start, end=end,
            canvas_w=canvas_w, canvas_h=canvas_h,
            video_h=video_h, video_y=video_y,
            has_audio=info["has_audio"],
            on_progress=(on_progress or (lambda pct: None)),
            overlays=overlays,
            crop_x=crop_x, crop_y=crop_y, zoom=zoom,
        )
    except editor_app.ExportError as exc:
        return {"success": False, "error": f"Export failed: {exc}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    out_info = editor_app.probe_video(output_path)
    return {
        "success": True, "output_file": output_path,
        "duration": round(out_info["duration"], 2),
        "width": out_info["width"], "height": out_info["height"],
        "start_time": start, "end_time": end,
    }


if __name__ == "__main__":
    editor_app = get_editor_app()
    assert "1:1" in editor_app.ASPECT_RATIOS
    print("editor_tool.py self-check OK (module loads, ASPECT_RATIOS present)")
