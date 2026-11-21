"""The phone runs web/js/alert.js; the server and simulator run Python.
Both must make the same decisions. Skipped if Node.js isn't installed."""
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from app.curves.alert import RideConditions, evaluate, hotspot_ahead
from app.curves.geometry import distance_along, find_curves

from .helpers import ORIGIN, offset, road_with_curve

ALERT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "alert.js"
NODE = shutil.which("node")


def run_js(payload: dict) -> dict:
    script = f"""
const m = await import({json.dumps(ALERT_JS.as_uri())});
const p = JSON.parse(process.argv[1]);
const cum = m.cumulative(p.points);
const out = {{ along: [], alerts: [], hotspots: [] }};
for (const pos of p.positions) out.along.push(m.distanceAlong(p.points, cum, pos));
for (const s of p.scenarios) {{
  const a = m.evaluate(s.pos_m, s.speed, p.curves, {{ wet: s.wet, night: s.night }});
  out.alerts.push({{ level: a.level, safe: a.safeKmh ?? null, dist: a.distanceM ?? null }});
}}
for (const x of p.hotspot_pos) {{ const h = m.hotspotAhead(x, p.hotspots); out.hotspots.push(h ? h.distanceM : null); }}
console.log(JSON.stringify(out));
"""
    res = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(payload)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_js_matches_python():
    pts, cum, curves = find_curves(road_with_curve(radius_m=40))
    curves_d = [asdict(c) for c in curves]
    positions = [offset(ORIGIN, dx, dy) for dx, dy in [(0, 0), (3, 100), (-4, 250), (20, 320), (60, 340), (150, 345)]]
    scenarios = [
        {"pos_m": curves[0].start_m - d, "speed": v, "wet": wet, "night": night}
        for d in (400, 200, 120, 95, 80, 50, 30, 5, -20)
        for v in (25, 40, 55, 70, 90)
        for wet in (False, True)
        for night in (False, True)
    ]
    hs = [{"name": "X", "lat": 0, "lon": 0, "at_m": 300.0}]
    hotspot_pos = [100, 150, 160, 299, 300, 301]

    js = run_js({"points": pts, "curves": curves_d, "positions": positions, "scenarios": scenarios, "hotspots": hs, "hotspot_pos": hotspot_pos})

    for pos, j in zip(positions, js["along"]):
        along, off = distance_along(pts, cum, pos)
        assert j["alongM"] == pytest.approx(along, abs=1e-6)
        assert j["offM"] == pytest.approx(off, abs=1e-6)

    for s, j in zip(scenarios, js["alerts"]):
        a = evaluate(s["pos_m"], s["speed"], curves, RideConditions(s["wet"], s["night"]))
        assert j["level"] == a.level.value, s
        if a.safe_kmh is not None:
            assert j["safe"] == pytest.approx(a.safe_kmh)
            assert j["dist"] == pytest.approx(a.distance_to_curve_m)

    for x, j in zip(hotspot_pos, js["hotspots"]):
        h = hotspot_ahead(x, hs)
        assert j == (h["distance_m"] if h else None), x
