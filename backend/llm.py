"""Thin LLM client: OpenAI-SDK-compatible, pointed at NVIDIA NIM. Text calls
use NIM_TEXT_MODEL; anything that needs to look at actual video frames uses
NIM_VISION_MODEL (the configured text model has no vision)."""
import base64
import json
import re
import time

from openai import OpenAI

from . import config

_client = None


def _get_client():
    global _client
    if _client is None:
        if not config.NVIDIA_API_KEY:
            raise RuntimeError(
                "NVIDIA_API_KEY is not set — the LLM-backed agents (Michael's chat, "
                "Emma's captions, David's visual QA) can't run without it."
            )
        # The SDK's default timeout is 10 minutes — a slow/hung endpoint would
        # otherwise block a caller (e.g. Michael's chat reply) with no error
        # and nothing shown to the user for that long. 60s is still generous
        # for a chat reply or a vision call, but fails fast enough to surface
        # an actual error message instead of looking like total silence.
        _client = OpenAI(base_url=config.NIM_BASE_URL, api_key=config.NVIDIA_API_KEY, timeout=60.0)
    return _client


def _image_to_data_url(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def chat_text(messages, temperature=0.6, max_tokens=800):
    resp = _get_client().chat.completions.create(
        model=config.NIM_TEXT_MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def chat_vision(messages, image_paths, temperature=0.4, max_tokens=1000):
    """messages: normal chat messages; the last user message gets the images
    appended as content parts. image_paths: local file paths, most
    representative first — callers may pass more than the model accepts,
    this caps to NIM_VISION_MAX_IMAGES (some hosted VLMs 400 past 1)."""
    image_paths = list(image_paths)[:max(1, config.NIM_VISION_MAX_IMAGES)]
    messages = [dict(m) for m in messages]
    last = messages[-1]
    content = last["content"]
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    for path in image_paths:
        parts.append({"type": "image_url", "image_url": {"url": _image_to_data_url(path)}})
    last["content"] = parts

    resp = _get_client().chat.completions.create(
        model=config.NIM_VISION_MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _extract_json(text):
    """Models often wrap JSON in prose or a ```json fence — pull the first
    {...} or [...] block out rather than requiring a perfectly bare response."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    match = re.search(r"[\{\[].*[\}\]]", candidate, re.DOTALL)
    return json.loads(match.group(0) if match else candidate)


def chat_json(messages, image_paths=None, temperature=0.5, max_tokens=1000, retries=3):
    """Calls chat_text or chat_vision and parses the reply as JSON. This
    hosted NIM endpoint occasionally returns a completely empty completion
    under load (not an API error — a real "stop" with no content), so an
    empty reply gets a fresh retry with backoff, not just a nudge; a
    non-empty-but-malformed reply gets one stricter-instruction nudge."""
    caller = (lambda m: chat_vision(m, image_paths, temperature, max_tokens)) if image_paths \
        else (lambda m: chat_text(m, temperature, max_tokens))

    last_error = None
    for attempt in range(retries):
        text = caller(messages)
        if not text.strip():
            last_error = ValueError("the model returned an empty response")
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            return _extract_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Reply with ONLY valid JSON, no prose, no markdown fence."},
            ]

    raise RuntimeError(f"LLM never returned valid JSON after {retries} attempts ({last_error})")


if __name__ == "__main__":
    assert _extract_json('here you go:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('{"a": 1}') == {"a": 1}
    print("llm.py self-check OK")
