"""Replay a ride and print/beep curve alerts once per GPS fix.

This is the Python prototype of the on-phone loop:
  1. get the route's curves once (find_curves)
  2. every fix: project position onto the route, evaluate(), beep or speak

Usage (from backend/):
  python -m tools.simulate_ride                       # synthetic 40 m curve at 65 km/h
  python -m tools.simulate_ride --wet --speed 55
  python -m tools.simulate_ride --gpx my_ride.gpx     # real trace with <time> per point
  python -m tools.simulate_ride --say                 # speak alerts (macOS `say` / Linux `espeak`)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

from app.curves.alert import AlertLevel, RideConditions, evaluate
from app.curves.geometry import distance_along, find_curves, haversine_m


def load_gpx(path: str) -> list[tuple[float, float, datetime | None]]:
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.parse(path).getroot()
    out = []
    for pt in root.iterfind(".//g:trkpt", ns):
        t = pt.find("g:time", ns)
        ts = datetime.fromisoformat(t.text.replace("Z", "+00:00")) if t is not None else None
        out.append((float(pt.get("lat")), float(pt.get("lon")), ts))
    return out


def synthetic_ride(speed_kmh: float):
    from tests.helpers import road_with_curve

    route = road_with_curve(radius_m=40)
    pts, cum, _ = find_curves(route)
    step = speed_kmh / 3.6  # metres per 1 s fix
    fixes, d, i = [], 0.0, 0
    while d < cum[-1]:
        while i + 1 < len(cum) and cum[i + 1] < d:
            i += 1
        fixes.append((pts[i], speed_kmh))
        d += step
    return route, fixes


def speak(text: str) -> None:
    for cmd in (["say", text], ["espeak", text]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpx")
    ap.add_argument("--speed", type=float, default=65.0, help="synthetic ride speed, km/h")
    ap.add_argument("--wet", action="store_true")
    ap.add_argument("--night", action="store_true")
    ap.add_argument("--say", action="store_true", help="speak alerts aloud")
    args = ap.parse_args(argv)

    if args.gpx:
        trace = load_gpx(args.gpx)
        route = [(lat, lon) for lat, lon, _ in trace]
        fixes = []
        for prev, cur in zip(trace, trace[1:]):
            dt = (cur[2] - prev[2]).total_seconds() if cur[2] and prev[2] else 1.0
            fixes.append(((cur[0], cur[1]), haversine_m(prev[:2], cur[:2]) / max(dt, 0.1) * 3.6))
    else:
        route, fixes = synthetic_ride(args.speed)

    pts, cum, curves = find_curves(route)
    print(f"Route {cum[-1]:.0f} m, {len(curves)} curve(s):")
    for c in curves:
        print(f"  at {c.start_m:5.0f} m  r={c.min_radius_m:5.1f} m  safe {c.safe_kmh_dry:.0f} dry / {c.safe_kmh_wet:.0f} wet km/h")

    cond = RideConditions(wet=args.wet, night=args.night)
    last = AlertLevel.NONE
    for t, (pos, speed) in enumerate(fixes):
        pos_m, _ = distance_along(pts, cum, pos)
        a = evaluate(pos_m, speed, curves, cond)
        if a.level != AlertLevel.NONE and a.level != last:
            bell = "\a" if a.level == AlertLevel.CAUTION else "\a\a"
            print(f"t={t:3d}s {pos_m:5.0f} m {speed:4.0f} km/h  {a.level.value.upper():7s} {a.message_tl}{bell}")
            if args.say and a.level == AlertLevel.URGENT:
                speak(a.message_en)
        last = a.level
    return 0


if __name__ == "__main__":
    sys.exit(main())
