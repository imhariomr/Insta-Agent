"""Loads the user's existing yt-downloader and video-editor Flask apps as
plain Python modules (their own `if __name__ == "__main__": app.run(...)`
guards make this side-effect-free) so our tools can call their real
functions directly — no HTTP, no second process, no duplicated logic."""
import importlib.util
import os
import sys

from . import config

_yt_module = None
_editor_module = None


def _load_module(module_name, dir_path):
    if dir_path not in sys.path:
        sys.path.insert(0, dir_path)
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(dir_path, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def get_yt_app():
    global _yt_module
    if _yt_module is None:
        _yt_module = _load_module("yt_downloader_app", config.YT_APP_DIR)
    return _yt_module


def get_editor_app():
    global _editor_module
    if _editor_module is None:
        _editor_module = _load_module("video_editor_app", config.EDITOR_APP_DIR)
    return _editor_module


if __name__ == "__main__":
    yt_app = get_yt_app()
    assert hasattr(yt_app, "get_video_info")
    editor_app = get_editor_app()
    assert hasattr(editor_app, "run_export") and hasattr(editor_app, "probe_video")
    print("external_apps.py self-check OK")
