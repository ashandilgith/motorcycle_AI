// On-phone curve alert. Line-for-line port of backend/app/curves/{geometry,alert}.py
// (distance_along, required_distance_m, evaluate, hotspot_ahead).
// Keep in sync: backend/tests/test_js_parity.py checks both give the same answers.

const EARTH_RADIUS_M = 6371000;

export const BRAKE_DECEL_MS2 = 3.0;
export const REACTION_S = 1.5;
export const CAUTION_FACTOR = 1.6;
export const LOOKAHEAD_M = 400;
export const NIGHT_SPEED_FACTOR = 0.9;
export const HOTSPOT_WARN_M = 150;

export const Level = Object.freeze({ NONE: "none", CAUTION: "caution", URGENT: "urgent" });
const RANK = { none: 0, caution: 1, urgent: 2 };

const rad = (d) => (d * Math.PI) / 180;

export function haversineM(a, b) {
  const [lat1, lon1, lat2, lon2] = [rad(a[0]), rad(a[1]), rad(b[0]), rad(b[1])];
  const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
}

export function toLocalXY(points, origin) {
  if (!points.length) return [];
  const [lat0, lon0] = origin || points[0];
  const k = Math.cos(rad(lat0));
  return points.map(([lat, lon]) => [rad(lon - lon0) * EARTH_RADIUS_M * k, rad(lat - lat0) * EARTH_RADIUS_M]);
}

export function cumulative(points) {
  const cum = [0];
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1] + haversineM(points[i - 1], points[i]));
  return cum;
}

/** Project position onto the route -> {alongM, offM}. */
export function distanceAlong(route, cum, position) {
  const xy = toLocalXY(route, position);
  let best = { alongM: 0, offM: Infinity };
  for (let i = 0; i < xy.length - 1; i++) {
    const [x1, y1] = xy[i];
    const [x2, y2] = xy[i + 1];
    const dx = x2 - x1, dy = y2 - y1;
    const seg2 = dx * dx + dy * dy;
    const t = seg2 === 0 ? 0 : Math.max(0, Math.min(1, (-x1 * dx - y1 * dy) / seg2));
    const off = Math.hypot(x1 + t * dx, y1 + t * dy);
    if (off < best.offM) best = { alongM: cum[i] + t * (cum[i + 1] - cum[i]), offM: off };
  }
  return best;
}

/** Distance left to the curve once the rider has reacted (REACTION_S at current speed). This is what we show. */
export function actionDistanceM(distanceM, speedKmh) {
  return Math.max(0, distanceM - (speedKmh / 3.6) * REACTION_S);
}

export function requiredDistanceM(speedKmh, targetKmh) {
  const v = speedKmh / 3.6, vt = targetKmh / 3.6;
  if (v <= vt) return 0;
  return v * REACTION_S + (v * v - vt * vt) / (2 * BRAKE_DECEL_MS2);
}

/** Most urgent curve alert ahead of positionM. curves: [{start_m, end_m, safe_kmh_dry, safe_kmh_wet, ...}] */
export function evaluate(positionM, speedKmh, curves, { wet = false, night = false } = {}) {
  let worst = { level: Level.NONE, speedKmh };
  for (const c of curves) {
    let d = c.start_m - positionM;
    if (d < 0 && c.end_m < positionM) continue; // passed
    d = Math.max(d, 0);
    if (d > LOOKAHEAD_M) break;
    let safe = wet ? c.safe_kmh_wet : c.safe_kmh_dry;
    if (night) safe *= NIGHT_SPEED_FACTOR;
    if (!isFinite(safe) || speedKmh <= safe) continue;
    const need = requiredDistanceM(speedKmh, safe);
    let level;
    if (d <= need) level = Level.URGENT;
    else if (d <= need * CAUTION_FACTOR) level = Level.CAUTION;
    else continue;
    if (RANK[level] > RANK[worst.level]) {
      const act = actionDistanceM(d, speedKmh);
      const r5 = Math.round(act / 5) * 5;
      worst = {
        level, curve: c, distanceM: d, actionM: act, speedKmh, safeKmh: safe,
        inCurve: d === 0,
        // Inside the curve: never "brake now" (hard braking while leaned over, esp. wet, can cause a fall).
        messageTl: d === 0 ? "Masyadong mabilis sa kurba. Dahan-dahan." : act < 5 ? `Preno na! Bagalan sa ${Math.round(safe)}.` : `Kurba in ${r5} metro${wet ? ", basa ang kalsada" : ""}. Bagalan sa ${Math.round(safe)}.`,
        messageEn: d === 0 ? "Too fast for this curve. Ease off gently." : act < 5 ? `Brake now! Slow to ${Math.round(safe)}.` : `Curve in ${r5} metres${wet ? " on a wet road" : ""}. Slow to ${Math.round(safe)}.`,
      };
    }
  }
  return worst;
}

/** Next hotspot within HOTSPOT_WARN_M ahead (hotspots carry at_m), or null. */
export function hotspotAhead(positionM, hotspots) {
  for (const h of hotspots) {
    const d = h.at_m - positionM;
    if (d >= 0 && d <= HOTSPOT_WARN_M) {
      const r10 = Math.round(d / 10) * 10;
      const name = h.name || "crash hotspot";
      return { ...h, distanceM: Math.round(d), messageTl: `Paps, delikadong lugar sa unahan, ${r10} metro: ${name}. Ingat.`, messageEn: `Crash hotspot in ${r10} metres: ${name}. Take care.` };
    }
  }
  return null;
}
