"""Rain -> the `wet` flag for curve safe speeds.

Uses Open-Meteo (free, no key). Its 15-minute data only covers Central Europe and
North America; for Manila it interpolates hourly data, so this is coarse. Upgrade
path: a radar nowcast (e.g. PAGASA or a paid nowcast API) for "rain in the next 15 min".
"""
from __future__ import annotations

from dataclasses import dataclass

from ..curves.geometry import LatLon
from .http import client

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WET_NOW_MM = 0.1         # any measurable rain now
WET_RECENT_MM = 0.5      # roads stay slick after rain (oil film on first rain is worst)


@dataclass
class RoadWeather:
    wet: bool
    rain_now_mm: float
    rain_last_hour_mm: float
    rain_next_hour_mm: float
    is_day: bool | None
    source: str = "open-meteo"


def road_weather(position: LatLon) -> RoadWeather:
    params = {
        "latitude": position[0],
        "longitude": position[1],
        "current": "precipitation,rain,is_day",
        "hourly": "precipitation",
        "past_hours": 1,
        "forecast_hours": 2,
        "timezone": "auto",
    }
    with client(timeout=10) as c:
        data = c.get(FORECAST_URL, params=params).raise_for_status().json()
    cur = data.get("current", {})
    hourly = data.get("hourly", {}).get("precipitation", []) or [0.0]
    now = float(cur.get("precipitation") or 0.0)
    last = float(hourly[0] or 0.0)
    nxt = float(hourly[-1] or 0.0)
    is_day = cur.get("is_day")
    return RoadWeather(
        wet=now >= WET_NOW_MM or last >= WET_RECENT_MM,
        rain_now_mm=now,
        rain_last_hour_mm=last,
        rain_next_hour_mm=nxt,
        is_day=None if is_day is None else bool(is_day),
    )
