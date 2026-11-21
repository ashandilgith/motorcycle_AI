"""Simulate two demo rides on the demo road and write timeline.json for the film page.

Ride A (dry, beeps only): rider ignores the caution beeps, reacts 1.5 s after the
rapid beeps start, brakes at 3 m/s^2 to the safe speed. Beeps stop when safe.
Ride B (wet, beeps + voice): rider reacts 1.5 s after the caution and eases off.
"""
import json
import math
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.curves.alert import AlertLevel, RideConditions, evaluate  # noqa: E402
from app.curves.geometry import Curve, to_local_xy  # noqa: E402

FPS = 30
DT = 1 / FPS
REACT = 1.5

d = json.load(open("demo.json"))
pts = [tuple(p) for p in d["points"]]
from app.curves.geometry import haversine_m  # noqa: E402

cum = [0.0]
for a, b in zip(pts, pts[1:]):
    cum.append(cum[-1] + haversine_m(a, b))
curves = [Curve(**{**c, "apex": tuple(c["apex"])}) for c in d["curves"]]
xy = to_local_xy(pts)
C = curves[0]


def at(along):
    i = next((k for k, c in enumerate(cum) if c >= along), len(cum) - 1)
    i = max(i, 1)
    t = (along - cum[i - 1]) / ((cum[i] - cum[i - 1]) or 1)
    lat = pts[i - 1][0] + t * (pts[i][0] - pts[i - 1][0])
    lon = pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1])
    x = xy[i - 1][0] + t * (xy[i][0] - xy[i - 1][0])
    y = xy[i - 1][1] + t * (xy[i][1] - xy[i - 1][1])
    hdg = math.degrees(math.atan2(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]))  # 0 = north, clockwise
    return (lat, lon), (x, y), hdg


def ride(start_along, v0, wet, react_to, brake, end_along, cruise_after):
    cond = RideConditions(wet=wet)
    safe = C.safe_kmh_wet if wet else C.safe_kmh_dry
    target = safe - 3
    along, v, t = start_along, v0, 0.0
    onset = None
    frames = []
    phase = "approach"
    while along < end_along:
        a = evaluate(along, v, curves, cond)
        if onset is None and a.level != AlertLevel.NONE and (react_to == "caution" or a.level == AlertLevel.URGENT):
            onset = t
        braking = onset is not None and t >= onset + REACT and v > target and along < C.end_m
        if braking:
            v = max(target, v - brake * 3.6 * DT)
        elif along > C.end_m + 15 and v < cruise_after:
            v = min(cruise_after, v + 1.3 * 3.6 * DT)
        (lat, lon), (x, y), hdg = at(along)
        if C.start_m <= along <= C.end_m:
            phase = "curve"
        elif along > C.end_m:
            phase = "exit"
        frames.append({
            "t": round(t, 4), "along": along, "speed": v, "lat": lat, "lon": lon, "x": x, "y": y, "hdg": hdg,
            "braking": braking, "level": a.level.value, "phase": phase,
            "map_m": max(0.0, C.start_m - along), "react_m": v / 3.6 * REACT,
        })
        along += v / 3.6 * DT
        t += DT
    return frames


rides = {
    "A": {"wet": False, "sound": "beeps", "lang": "en",
          "frames": ride(start_along=125, v0=65, wet=False, react_to="urgent", brake=3.0, end_along=505, cruise_after=50)},
    "B": {"wet": True, "sound": "both", "lang": "en",
          "frames": ride(start_along=165, v0=55, wet=True, react_to="caution", brake=2.0, end_along=420, cruise_after=40)},
}
sign_along = C.start_m - 45
hs = d["hotspots"][0]
hs_xy = to_local_xy([(hs["lat"], hs["lon"])], origin=pts[0])[0]
out = {
    "fps": FPS,
    "road": xy,
    "curve": {"start_xy": at(C.start_m)[1], "end_xy": at(C.end_m)[1], "apex_xy": at(C.apex_m)[1], "sign_xy": at(sign_along)[1], "sign_hdg": at(sign_along)[2]},
    "hotspot": {"xy": hs_xy, "name": hs["name"]},
    "rides": rides,
}
json.dump(out, open("timeline.json", "w"))
for k, r in rides.items():
    fr = r["frames"]
    lv = []
    for f in fr:
        if not lv or lv[-1][1] != f["level"]:
            lv.append((round(f["t"], 1), f["level"], round(f["speed"]), round(f["along"])))
    print(k, f"{fr[-1]['t']:.1f}s", "levels:", lv, "min speed", round(min(f["speed"] for f in fr)))
