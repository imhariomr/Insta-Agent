"""download_youtube(url, resolution) — reuses yt/app.py's get_video_info,
ffmpeg detection, and cookie handling as-is. The only new code is the
destination: the original app's /download route saves into a throwaway
temp dir and streams it back over HTTP; here we save straight into the
batch's own job folder since the file needs to persist for later stages."""
import os
import uuid

from ..external_apps import get_yt_app


def _parse_height(resolution):
    digits = "".join(ch for ch in str(resolution) if ch.isdigit())
    return int(digits) if digits else 720


def _pick_format(resolutions, wanted_height):
    if not resolutions:
        return None
    exact = next((r for r in resolutions if r["height"] == wanted_height), None)
    return exact or min(resolutions, key=lambda r: abs(r["height"] - wanted_height))


def get_video_info(url):
    return get_yt_app().get_video_info(url)


def download_youtube(url, resolution="720p", dest_dir=".", on_progress=None):
    yt_app = get_yt_app()
    wanted_height = _parse_height(resolution)

    try:
        info = yt_app.get_video_info(url)
    except Exception as exc:
        return {"success": False, "error": f"Could not read video info: {exc}"}

    fmt = _pick_format(info.get("resolutions"), wanted_height)
    if not fmt:
        return {"success": False, "error": "No downloadable resolution found for this video."}

    will_merge = fmt["needs_merge"] and yt_app.HAS_FFMPEG
    format_string = f"{fmt['format_id']}+bestaudio/best" if will_merge else fmt["format_id"]

    os.makedirs(dest_dir, exist_ok=True)
    outtmpl = os.path.join(dest_dir, f"{uuid.uuid4().hex}.%(ext)s")

    def hook(d):
        if not on_progress:
            return
        if d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = round((downloaded / total) * 100, 1) if total else 0
            on_progress(pct, d.get("speed"), d.get("eta"))
        elif d.get("status") == "finished":
            on_progress(100, None, 0)

    ydl_opts = {
        "quiet": True, "no_warnings": True,
        "format": format_string, "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
    }
    if yt_app.FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = yt_app.FFMPEG_PATH
    yt_app._apply_cookies(ydl_opts)

    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result_info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(result_info)
            if not os.path.exists(filepath):
                base, _ = os.path.splitext(filepath)
                for ext in ("mp4", "mkv", "webm"):
                    candidate = f"{base}.{ext}"
                    if os.path.exists(candidate):
                        filepath = candidate
                        break
    except Exception as exc:
        return {"success": False, "error": f"Download failed: {exc}"}

    if not os.path.exists(filepath):
        return {"success": False, "error": "yt-dlp reported success but the output file is missing."}

    return {
        "success": True,
        "file_path": filepath,
        "title": result_info.get("title"),
        "resolution": f"{fmt['height']}p",
        "duration": result_info.get("duration"),
    }


if __name__ == "__main__":
    assert _parse_height("720p") == 720
    assert _parse_height("1080") == 1080
    assert _pick_format([{"height": 480}, {"height": 720}], 720)["height"] == 720
    assert _pick_format([{"height": 480}, {"height": 1080}], 720)["height"] in (480, 1080)
    print("youtube_tool.py self-check OK")
