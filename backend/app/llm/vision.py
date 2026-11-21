"""Tyre check with a vision-language model (VLM).

MVP uses an LLM with vision and a fixed JSON output. Once ~1-2k photos are
labelled (via the "was this right?" button), train a small on-device classifier
and keep the VLM as fallback.
"""
from __future__ import annotations

import base64
import json
import re

from ..config import settings

TYRE_PROMPT = """You are inspecting a photo of a motorcycle tyre for a rider in Metro Manila.
Return ONLY a JSON object with these keys:
{
  "photo_usable": true|false,            // false if blurry, too dark, or not a tyre
  "tread": "good"|"worn"|"near_limit"|"unknown",
  "cracks": "none"|"minor"|"severe"|"unknown",
  "bulge_or_cut": true|false|null,
  "embedded_object": true|false|null,    // nail, screw, glass
  "dot_date_code": "WWYY"|null,          // 4-digit date code if readable
  "confidence": 0.0-1.0,
  "advice_en": "one sentence",
  "advice_tl": "one sentence in Taglish"
}
Be conservative: if unsure, say "unknown" and lower confidence. Any bulge, cut, or
severe cracking means advise replacing before riding."""


def _media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported image type; send JPEG, PNG or WebP")


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("Model did not return JSON")
    return json.loads(match.group(0))


def check_tyre(image: bytes) -> dict:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; tyre check unavailable")
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.llm_model,
        max_tokens=600,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _media_type(image),
                            "data": base64.b64encode(image).decode(),
                        },
                    },
                    {"type": "text", "text": TYRE_PROMPT},
                ],
            }
        ],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return parse_json(text)
