"""Real Meta/Instagram Graph API publishing: upload each video as a
carousel-item media container, poll its real processing status, create the
top-level carousel container, then publish it. Never reports success
without a real "id" back from Graph. Graph can only fetch video_url from a
public HTTPS URL — it cannot take a local file — so this also refuses to
proceed (with a clear reason) until IG_USER_ID/IG_ACCESS_TOKEN/PUBLIC_BASE_URL
are all configured."""
import time

import requests

from .. import config, tunnel

GRAPH_BASE = f"{config.IG_GRAPH_HOST}/{config.IG_GRAPH_VERSION}"

_TUNNEL_STATUS_REASONS = {
    "not_installed": "cloudflared isn't installed, so there's no public URL for Instagram to fetch media from.",
    "starting": "the media tunnel is still starting up.",
    "unavailable": "the media tunnel isn't reachable right now.",
    "reconnecting": "the media tunnel is reconnecting.",
    "stopped": "the media tunnel isn't running.",
}


def connection_status():
    if not config.IG_USER_ID or not config.IG_ACCESS_TOKEN:
        return {"connected": False, "reason": "IG_USER_ID / IG_ACCESS_TOKEN are not configured."}
    if not tunnel.get_public_base_url():
        mgr_status = tunnel.get_manager().status
        reason = _TUNNEL_STATUS_REASONS.get(mgr_status, "no public media URL is available yet.")
        return {"connected": False, "reason": f"No public media URL — {reason}"}
    return {"connected": True, "reason": None}


def _blocked():
    status = connection_status()
    if status["connected"]:
        return None
    return {"success": False, "error": f"Instagram not connected: {status['reason']}"}


def _check_container_once(container_id):
    """One GET /<container_id>?fields=status_code call, no looping. Returns
    (status_code_or_None, api_error_dict_or_None, request_exception_or_None)
    — exactly one of the three is non-None/non-empty. Shared by the polling
    loop below and by the one-shot reuse check a retry uses to decide
    whether an already-uploaded container is still good."""
    try:
        resp = requests.get(f"{GRAPH_BASE}/{container_id}",
                             params={"fields": "status_code", "access_token": config.IG_ACCESS_TOKEN},
                             timeout=15)
        data = resp.json()
    except requests.RequestException as exc:
        return None, None, exc
    if "error" in data:
        return None, data["error"], None
    return data.get("status_code"), None, None


def container_still_usable(container_id):
    """One-shot check for reusing a container_id saved from a previous,
    partially-failed publish attempt. True only if Graph confirms it's still
    FINISHED right now — containers expire 24h after creation if unused, so
    a stale one must never be trusted without asking Graph first."""
    status, _error, _exc = _check_container_once(container_id)
    return status == "FINISHED"


def _poll_container(container_id, timeout=None, interval=None):
    """Polls GET /<container_id>?fields=status_code — the same endpoint and
    status values apply to every container type Meta issues (single image,
    single video/REELS, or the parent CAROUSEL container); Meta's docs don't
    define a separate polling flow per media type.

    Returns {"success": True} once FINISHED, or {"success": False, "error":
    ..., "status": "ERROR"|"EXPIRED"|"TIMEOUT"|"REQUEST_FAILED"} otherwise —
    the status field lets callers (and logs) tell apart a real processing
    failure (ERROR, with Meta's own error payload), an expired container
    (EXPIRED — sat unused past Meta's 24h window), our own timeout budget
    running out while it was still IN_PROGRESS (TIMEOUT), and a network/API
    failure of the status check itself (REQUEST_FAILED)."""
    timeout = config.IG_CONTAINER_POLL_TIMEOUT_SECONDS if timeout is None else timeout
    interval = config.IG_CONTAINER_POLL_INTERVAL_SECONDS if interval is None else interval
    deadline = time.time() + timeout
    last_status = None
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        status, api_error, exc = _check_container_once(container_id)

        if exc is not None:
            print(f"[instagram] container={container_id} attempt={attempt} status-check request failed: {exc}")
            return {"success": False, "status": "REQUEST_FAILED", "error": f"Container status check failed: {exc}"}

        if api_error is not None:
            if api_error.get("is_transient"):
                # e.g. code 4 / subcode 1349210 "Application request limit
                # reached" — Meta's own signal to back off and retry, not a
                # real failure. Keep polling within the existing budget
                # instead of aborting on the first rate-limit hit.
                last_status = f"transient error {api_error.get('code')}/{api_error.get('error_subcode')}"
                print(f"[instagram] container={container_id} attempt={attempt} {last_status} — backing off {interval}s")
                time.sleep(interval)
                continue
            # A non-transient rejection of the status-check call itself (bad
            # token scope, wrong container ID for this call, etc.) — without
            # this check that error is invisible: status_code just reads as
            # None forever and the real problem gets reported as "unknown"
            # only after burning the full timeout.
            print(f"[instagram] container={container_id} attempt={attempt} status-check rejected: {api_error}")
            return {"success": False, "status": "REQUEST_FAILED",
                    "error": f"Container status check rejected: {api_error}"}

        print(f"[instagram] container={container_id} attempt={attempt} status={status}")
        last_status = status
        if status == "FINISHED":
            return {"success": True}
        if status == "ERROR":
            return {"success": False, "status": "ERROR", "error": f"Media processing failed (status={status})"}
        if status == "EXPIRED":
            return {"success": False, "status": "EXPIRED",
                    "error": "Media container expired before it could be published (unused past Meta's 24h window)."}
        # IN_PROGRESS, or any other/unrecognized value — keep polling; a
        # container's real terminal states are exactly the three above.
        time.sleep(interval)
    return {"success": False, "status": "TIMEOUT",
            "error": f"Media container never finished processing within {timeout}s "
                     f"(last status: {last_status or 'unknown'})."}


def upload_instagram_media(video_url, is_carousel_item=True, caption=""):
    blocked = _blocked()
    if blocked:
        return blocked
    try:
        # Meta removed media_type=VIDEO (confirmed via a real API error:
        # "media_type के लिए 'वीडियो' वैल्यू को हटा दिया गया है") — all
        # video posts, including carousel children, now use REELS.
        data = {
            "media_type": "REELS",
            "video_url": video_url,
            "is_carousel_item": "true" if is_carousel_item else "false",
            "access_token": config.IG_ACCESS_TOKEN,
        }
        # Carousel children can't carry their own caption — only the parent
        # CAROUSEL container can (set via create_instagram_carousel); a
        # single (non-carousel) post has no parent container, so this is
        # the only place its caption is ever sent.
        if caption and not is_carousel_item:
            data["caption"] = caption
        resp = requests.post(f"{GRAPH_BASE}/{config.IG_USER_ID}/media", data=data, timeout=30)
        data = resp.json()
    except requests.RequestException as exc:
        return {"success": False, "error": f"Instagram upload request failed: {exc}"}

    if "id" not in data:
        return {"success": False, "error": f"Instagram rejected the media: {data}"}

    poll_result = _poll_container(data["id"])
    if not poll_result["success"]:
        return {"success": False, "error": poll_result["error"]}
    return {"success": True, "container_id": data["id"]}


def create_instagram_carousel(children_container_ids, caption=""):
    blocked = _blocked()
    if blocked:
        return blocked
    try:
        resp = requests.post(f"{GRAPH_BASE}/{config.IG_USER_ID}/media", data={
            "media_type": "CAROUSEL",
            "children": ",".join(children_container_ids),
            "caption": caption,
            "access_token": config.IG_ACCESS_TOKEN,
        }, timeout=30)
        data = resp.json()
    except requests.RequestException as exc:
        return {"success": False, "error": f"Instagram carousel creation failed: {exc}"}

    if "id" not in data:
        return {"success": False, "error": f"Instagram rejected the carousel: {data}"}

    # Every child container gets polled to FINISHED individually above, but
    # the parent CAROUSEL container also needs its own processing time —
    # publishing it immediately can race ahead of Meta's backend and come
    # back "Media ID is not available" (code 9007 / subcode 2207027).
    poll_result = _poll_container(data["id"])
    if not poll_result["success"]:
        return {"success": False, "error": poll_result["error"]}
    return {"success": True, "container_id": data["id"]}


# (error code, error_subcode) pairs Meta returns when a container reports
# FINISHED but the publish backend isn't actually ready for it yet — a real
# delay against Meta's own systems, not something a poll on our side caught.
_NOT_READY_YET = {(9007, 2207027)}


def publish_instagram_carousel(container_id, retries=None, delay=None):
    blocked = _blocked()
    if blocked:
        return blocked
    retries = config.IG_PUBLISH_RETRY_COUNT if retries is None else retries
    delay = config.IG_PUBLISH_RETRY_DELAY_SECONDS if delay is None else delay
    for attempt in range(retries):
        try:
            resp = requests.post(f"{GRAPH_BASE}/{config.IG_USER_ID}/media_publish", data={
                "creation_id": container_id,
                "access_token": config.IG_ACCESS_TOKEN,
            }, timeout=30)
            data = resp.json()
        except requests.RequestException as exc:
            return {"success": False, "error": f"Publish request failed: {exc}"}

        if "id" in data:
            return {"success": True, "post_id": data["id"]}

        error = data.get("error", {})
        if (error.get("code"), error.get("error_subcode")) in _NOT_READY_YET and attempt < retries - 1:
            time.sleep(delay)
            continue
        return {"success": False, "error": f"Instagram rejected the publish request: {data}"}
    return {"success": False, "error": "Instagram still wasn't ready to publish after retrying."}


if __name__ == "__main__":
    from unittest.mock import patch

    status = connection_status()
    assert status["connected"] is False and status["reason"]

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    with patch("time.sleep", lambda s: None):
        with patch("requests.get", return_value=_Resp({"status_code": "FINISHED"})):
            assert _poll_container("cid", timeout=10, interval=1) == {"success": True}

        with patch("requests.get", return_value=_Resp({"error": {"message": "bad token"}})):
            result = _poll_container("cid", timeout=10, interval=1)
            assert result["success"] is False and result["status"] == "REQUEST_FAILED"

        # A transient error (e.g. rate limiting) must be retried, not aborted —
        # simulate it clearing after one hit.
        transient = {"error": {"code": 4, "error_subcode": 1349210, "is_transient": True}}
        responses = iter([_Resp(transient), _Resp({"status_code": "FINISHED"})])
        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            assert _poll_container("cid", timeout=10, interval=0.01) == {"success": True}

        with patch("requests.get", return_value=_Resp({"status_code": "ERROR"})):
            result = _poll_container("cid", timeout=10, interval=1)
            assert result["success"] is False and result["status"] == "ERROR"

        with patch("requests.get", return_value=_Resp({"status_code": "EXPIRED"})):
            result = _poll_container("cid", timeout=10, interval=1)
            assert result["success"] is False and result["status"] == "EXPIRED"

    # Deliberately outside the time.sleep mock: a no-op sleep would make this
    # busy-spin for the full real-world duration instead of pacing itself,
    # printing thousands of lines. Real (tiny) values keep it a fraction of
    # a second without needing to fake the clock.
    with patch("requests.get", return_value=_Resp({"status_code": "IN_PROGRESS"})):
        result = _poll_container("cid", timeout=0.15, interval=0.05)
        assert result["success"] is False and result["status"] == "TIMEOUT"

    print("instagram_tool.py self-check OK (not-connected reporting; _poll_container "
          "distinguishes FINISHED/ERROR/EXPIRED/TIMEOUT)")
