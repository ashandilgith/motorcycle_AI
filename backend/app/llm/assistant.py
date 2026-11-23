"""Bike-aware assistant: an LLM agent with tools over the rider's own data.

Answers technical questions and triages symptoms for the rider's specific model,
grounded in the service log, maintenance schedule and recent sound checks.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..config import settings
from ..integrations import google_maps
from ..maintenance import next_due
from ..storage import Store

SYSTEM_PROMPT = """You are "Paps", a motorcycle maintenance assistant for riders in Metro Manila.
Reply in the rider's language (Taglish if they write Taglish), short and practical.

Rules:
- Always call get_bike_profile first so answers fit the rider's make, model, year and odometer.
- For a symptom, give the 2-4 most likely causes RANKED, each with the next check the rider can do,
  then when to go to a mechanic. Never state one cause as certain.
- Anything involving brakes, fuel leaks, burning smell, smoke, wiring damage, or overheating that
  keeps returning: tell them to stop riding and see a mechanic now.
- Never tell a rider to open a hot radiator cap. Wait until the engine is fully cool.
- After floods: do NOT start the engine until oil, air filter and exhaust are checked.
- Maintenance items carry a "source": "manual" figures come from the maker's manual (name it);
  "estimate" figures are generic, so say "check your owner's manual" when giving them.
- Give peso estimates only as rough ranges and say they vary by shop.
- If the rider is stranded or needs a shop, call find_nearby_shops (needs their location) and list
  2-3 open shops with name, address and phone."""

TOOLS = [
    {
        "name": "get_bike_profile",
        "description": "The rider's bike: make, model, year, odometer_km, usage (commuter/gig/diy).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_service_log",
        "description": "Past maintenance entries for this bike, newest odometer first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_maintenance_due",
        "description": "Each maintenance item with km remaining and status (overdue/due_soon/ok).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_sound_checks",
        "description": "Recent engine-sound recordings with anomaly scores (higher = more unusual).",
        "input_schema": {"type": "object", "properties": {}},
    },
]

SHOP_TOOL = {
    "name": "find_nearby_shops",
    "description": "Motorcycle repair shops near the rider that are open now: name, address, phone, map link.",
    "input_schema": {
        "type": "object",
        "properties": {"radius_m": {"type": "number", "description": "Search radius in metres, default 3000"}},
    },
}

MAX_TOOL_ROUNDS = 5


def _tool_impls(store: Store, bike_id: str, location=None) -> dict[str, Callable[..., Any]]:
    def bike():
        return store.get_bike(bike_id)

    def shops(radius_m: float = 3000):
        if not location:
            return {"error": "Rider location unknown; ask them where they are."}
        if not settings.google_maps_api_key:
            return {"error": "Shop search not configured."}
        return google_maps.repair_shops(location, settings.google_maps_api_key, radius_m=radius_m)

    return {
        "find_nearby_shops": shops,
        "get_bike_profile": bike,
        "get_service_log": lambda: store.service_log(bike_id),
        "get_maintenance_due": lambda: next_due(bike()["odometer_km"], bike()["usage"], store.service_log(bike_id), bike()["model"]),
        "get_recent_sound_checks": lambda: store.recent_clips(bike_id),
    }


def chat(store: Store, bike_id: str, messages: list[dict], client: Any | None = None, location=None) -> dict:
    """Run the tool-use loop. `messages` = [{"role": "user"|"assistant", "content": str}, ...].

    `client` can be injected (tests); otherwise an Anthropic client is created.
    """
    if client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; assistant unavailable")
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    impls = _tool_impls(store, bike_id, location)
    tools = TOOLS + ([SHOP_TOOL] if location else [])
    convo: list[dict] = [dict(m) for m in messages]
    tools_used: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS + 1):
        resp = client.messages.create(
            model=settings.llm_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=convo,
        )
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {"reply": text, "tools_used": tools_used}

        convo.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            tools_used.append(block.name)
            fn = impls.get(block.name)
            try:
                output = fn(**(block.input or {})) if fn else {"error": f"unknown tool {block.name}"}
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(output, default=str)})
            except Exception as exc:  # surface tool errors to the model instead of crashing
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True})
        convo.append({"role": "user", "content": results})

    return {"reply": "Sorry paps, I couldn't finish that. Please try again.", "tools_used": tools_used}
