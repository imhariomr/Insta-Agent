"""Owns the Cloudflare Quick Tunnel lifecycle so nothing else talks to
`cloudflared` directly. Auto-detects the binary, starts a tunnel pointed at
the minimal media server (never the main app), waits for cloudflared to
print its real trycloudflare.com URL, and only trusts that URL once it
actually answers a health check — never assumes a spawned process means a
working public URL. A background thread keeps re-checking and restarts the
tunnel if it dies or stops answering, without requiring an app restart."""
import queue
import re
import shutil
import subprocess
import threading
import time

import requests

from . import config

TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
STARTUP_TIMEOUT = 25
HEALTH_CHECK_TIMEOUT = 5
MONITOR_INTERVAL = 15

# Valid values for TunnelManager.status:
#   stopped | starting | active | reconnecting | unavailable | not_installed | fixed


def _find_cloudflared():
    if config.CLOUDFLARED_PATH:
        return config.CLOUDFLARED_PATH
    return shutil.which("cloudflared")  # shutil.which already resolves .exe via PATHEXT on Windows


class TunnelManager:
    def __init__(self, local_port):
        self.local_port = local_port
        self.status = "stopped"
        self._proc = None
        self._lock = threading.Lock()
        self._public_url = None
        self._monitor_thread = None
        self._stop_monitor = threading.Event()
        self._on_status_change = None

    def set_status_callback(self, callback):
        """callback(status: str, detail: str) — called on every real transition."""
        self._on_status_change = callback

    def _notify(self, status, detail=""):
        self.status = status
        if self._on_status_change:
            try:
                self._on_status_change(status, detail)
            except Exception:
                pass

    def get_public_url(self):
        return self._public_url

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        # Always clear before doing anything else: restart() reaches here via
        # stop() (which sets this to halt the monitor thread), but a restart
        # triggered BY the monitor thread itself doesn't spawn a new thread
        # below, so if this weren't cleared unconditionally the flag would
        # stay set and kill monitoring for good after the first self-heal.
        self._stop_monitor.clear()

        if config.PUBLIC_BASE_URL_MODE == "fixed":
            self._public_url = config.PUBLIC_BASE_URL or None
            # Graph API can only fetch video_url/image_url over HTTPS — an
            # http:// misconfiguration here would otherwise surface later as
            # an oblique Instagram rejection instead of a clear local error.
            if self._public_url and not self._public_url.startswith("https://"):
                self._public_url = None
                self._notify("unavailable", "PUBLIC_BASE_URL must be HTTPS — Instagram's Graph API "
                                             "cannot fetch media over plain HTTP")
            elif self._public_url and self._health_check(self._public_url, retries=1, delay=0):
                self._notify("fixed")
            else:
                self._public_url = None
                self._notify("unavailable", "PUBLIC_BASE_URL_MODE=fixed but the URL is empty or unreachable")
            return

        cloudflared = _find_cloudflared()
        if not cloudflared:
            self._notify("not_installed", "cloudflared was not found on PATH")
            return

        with self._lock:
            self._notify("starting")
            self._proc = subprocess.Popen(
                [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{self.local_port}", "--no-autoupdate"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            url = self._wait_for_url(STARTUP_TIMEOUT)

        if not url:
            self._notify("unavailable", "Timed out waiting for cloudflared to report a tunnel URL")
            return

        if self._health_check(url):
            self._public_url = url
            self._notify("active")
        else:
            self._notify("unavailable", "The tunnel URL did not pass a health check")

        if not self._monitor_thread or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()

    def _wait_for_url(self, timeout):
        """Reads cloudflared's stdout on a helper thread so a slow/quiet
        process can never block this past `timeout`, unlike a bare
        blocking readline() loop."""
        lines = queue.Queue()

        def reader():
            for line in self._proc.stdout:
                lines.put(line)

        threading.Thread(target=reader, daemon=True).start()

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            match = TUNNEL_URL_RE.search(line)
            if match:
                return match.group(0)
            if self._proc.poll() is not None:
                break
        return None

    def _health_check(self, url, retries=5, delay=2):
        for attempt in range(retries):
            try:
                resp = requests.get(f"{url}/health", timeout=HEALTH_CHECK_TIMEOUT)
                if resp.ok and resp.json().get("status") == "ok":
                    return True
            except requests.RequestException:
                pass
            if attempt < retries - 1:
                time.sleep(delay)
        return False

    def health_check_now(self):
        return bool(self._public_url) and self._health_check(self._public_url, retries=1, delay=0)

    def stop(self):
        self._stop_monitor.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
        self._public_url = None
        self._notify("stopped")

    def restart(self):
        self.stop()
        self.start()

    def _monitor_loop(self):
        while not self._stop_monitor.wait(MONITOR_INTERVAL):
            if config.PUBLIC_BASE_URL_MODE == "fixed":
                continue
            if not self.is_running() or not self.health_check_now():
                self._notify("reconnecting", "Tunnel died or failed a health check — restarting")
                self.restart()


_manager = None


def get_manager():
    global _manager
    if _manager is None:
        _manager = TunnelManager(config.MEDIA_SERVER_PORT)
    return _manager


def get_public_base_url():
    return get_manager().get_public_url()


def get_public_media_url(batch_id, filename):
    base = get_public_base_url()
    return f"{base}/media/{batch_id}/{filename}" if base else None


if __name__ == "__main__":
    assert TUNNEL_URL_RE.search("some log line https://random-name.trycloudflare.com more text") \
        .group(0) == "https://random-name.trycloudflare.com"
    assert TUNNEL_URL_RE.search("no url here") is None
    mgr = TunnelManager(5101)
    assert mgr.get_public_url() is None and mgr.status == "stopped"
    print("tunnel.py self-check OK")
