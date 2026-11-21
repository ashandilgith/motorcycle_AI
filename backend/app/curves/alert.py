"""Curve-speed alert decision.

Reference implementation of the loop the phone runs once per GPS fix (~1 Hz).
Keep it dependency-free so it can be ported to the mobile app unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .geometry import Curve


class AlertLevel(str, Enum):
    NONE = "none"
    CAUTION = "caution"   # single beep: curve coming, start easing off
    URGENT = "urgent"     # voice: brake now


@dataclass
class RideConditions:
    wet: bool = False
    night: bool = False


@dataclass
class Alert:
    level: AlertLevel
    curve: Curve | None = None
    distance_to_curve_m: float | None = None
    action_distance_m: float | None = None   # after reaction time; shown to the rider
    speed_kmh: float | None = None
    safe_kmh: float | None = None
    message_tl: str | None = None   # Taglish voice line
    message_en: str | None = None


# Tunable assumptions -- validate with real riders before trusting them.
BRAKE_DECEL_MS2 = 3.0        # comfortable braking, not emergency
REACTION_S = 1.5
CAUTION_FACTOR = 1.6         # warn earlier than the bare minimum
LOOKAHEAD_M = 400.0
NIGHT_SPEED_FACTOR = 0.9


def action_distance_m(distance_m: float, speed_kmh: float) -> float:
    """Distance left to the curve once the rider has reacted (REACTION_S at current speed). This is what we show."""
    return max(0.0, distance_m - speed_kmh / 3.6 * REACTION_S)


def required_distance_m(speed_kmh: float, target_kmh: float) -> float:
    """Distance needed to react and slow from speed to target."""
    v, vt = speed_kmh / 3.6, target_kmh / 3.6
    if v <= vt:
        return 0.0
    return v * REACTION_S + (v * v - vt * vt) / (2 * BRAKE_DECEL_MS2)


def evaluate(
    position_m: float,
    speed_kmh: float,
    curves: list[Curve],
    conditions: RideConditions = RideConditions(),
) -> Alert:
    """Return the most urgent alert for curves ahead of `position_m` (distance along route)."""
    worst = Alert(AlertLevel.NONE, speed_kmh=speed_kmh)
    for c in curves:
        d = c.start_m - position_m
        if d < 0 and c.end_m < position_m:
            continue  # already passed
        d = max(d, 0.0)
        if d > LOOKAHEAD_M:
            break
        safe = c.safe_kmh_wet if conditions.wet else c.safe_kmh_dry
        if conditions.night:
            safe *= NIGHT_SPEED_FACTOR
        if math.isinf(safe) or speed_kmh <= safe:
            continue
        need = required_distance_m(speed_kmh, safe)
        if d <= need:
            level = AlertLevel.URGENT
        elif d <= need * CAUTION_FACTOR:
            level = AlertLevel.CAUTION
        else:
            continue
        if _rank(level) > _rank(worst.level):
            act = action_distance_m(d, speed_kmh)
            r5 = _r(act / 5) * 5
            wet_tl = ", basa ang kalsada" if conditions.wet else ""
            wet_en = " on a wet road" if conditions.wet else ""
            worst = Alert(
                level=level,
                curve=c,
                distance_to_curve_m=d,
                action_distance_m=act,
                speed_kmh=speed_kmh,
                safe_kmh=safe,
                message_tl=f"Preno na! Bagalan sa {_r(safe)}." if act < 5 else f"Kurba in {r5} metro{wet_tl}. Bagalan sa {_r(safe)}.",
                message_en=f"Brake now! Slow to {_r(safe)}." if act < 5 else f"Curve in {r5} metres{wet_en}. Slow to {_r(safe)}.",
            )
    return worst


HOTSPOT_WARN_M = 150.0


def hotspot_ahead(position_m: float, hotspots: list[dict]) -> dict | None:
    """Next known crash hotspot within HOTSPOT_WARN_M ahead (hotspots carry `at_m`), or None.

    Announce once per hotspot (the phone keeps track), e.g.
    "Paps, delikadong intersection sa unahan, ingat."
    """
    for h in hotspots:
        d = h["at_m"] - position_m
        if 0 <= d <= HOTSPOT_WARN_M:
            name = h.get("name") or "crash hotspot"
            return {
                **h,
                "distance_m": round(d),
                "message_tl": f"Paps, delikadong lugar sa unahan, {round(d, -1):.0f} metro: {name}. Ingat.",
                "message_en": f"Crash hotspot in {round(d, -1):.0f} metres: {name}. Take care.",
            }
    return None


def _r(x: float) -> int:
    """Round half up, exactly like JS Math.round (Python's round() rounds half to even)."""
    return int(math.floor(x + 0.5))


def _rank(level: AlertLevel) -> int:
    return {AlertLevel.NONE: 0, AlertLevel.CAUTION: 1, AlertLevel.URGENT: 2}[level]
