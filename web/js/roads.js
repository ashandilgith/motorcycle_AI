// Roads fetched by the phone itself, for areas the server has no road data for.
// The server can't use the public OpenStreetMap (Overpass) servers from its host, but a phone can.
// Fetch a wide circle once, reuse it while the rider stays well inside, and send only what's near.
import { haversineM } from "./alert.js";

// Public servers, all slow and sometimes down (browser check 23 Nov 2026, 1.5 km in Baguio:
// maps.mail.ru 15 s, z./lz4.overpass-api.de ~36 s, overpass-api.de / kumi / private.coffee >40 s).
// Ask all at once and take the first good answer.
const SERVERS = [
  "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
  "https://overpass-api.de/api/interpreter",
  "https://z.overpass-api.de/api/interpreter",
  "https://lz4.overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];
// same road types as the server's tiles (no service roads: driveways and parking aisles)
const TYPES = "trunk|primary|secondary|tertiary|unclassified|residential|living_street|trunk_link|primary_link|secondary_link|tertiary_link";
export const FETCH_RADIUS_M = 1500;
export const SEND_RADIUS_M = 1000;
const TIMEOUT_MS = 45_000;

let cache = null; // {center, elements}

/** Do we already hold roads covering everything within SEND_RADIUS_M of pos? */
export function covers(pos) {
  return !!cache && haversineM(cache.center, pos) <= FETCH_RADIUS_M - SEND_RADIUS_M;
}

/** Cached roads near pos, or null if we have none for here. */
export function cachedRoadsNear(pos) {
  return covers(pos) ? trim(cache.elements, pos, SEND_RADIUS_M) : null;
}

/** Roads near pos, downloading from OpenStreetMap if needed. Throws if every server fails. */
export async function roadsNear(pos) {
  if (!covers(pos)) cache = { center: pos, elements: await download(pos, FETCH_RADIUS_M) };
  return trim(cache.elements, pos, SEND_RADIUS_M);
}

async function download([lat, lon], radiusM) {
  const q = `[out:json][timeout:40];way(around:${radiusM},${lat.toFixed(6)},${lon.toFixed(6)})[highway~"^(${TYPES})$"]->.w;.w out body qt;node(w.w);out skel qt;`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  const ask = async (url) => {
    try {
      const r = await fetch(url, { method: "POST", body: new URLSearchParams({ data: q }), signal: ctl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (!Array.isArray(data.elements)) throw new Error("bad answer");
      return data.elements;
    } catch (e) {
      throw new Error(`${new URL(url).host}: ${e.name === "AbortError" ? "timeout" : e.message}`);
    }
  };
  try {
    return await Promise.any(SERVERS.map(ask));
  } catch (e) {
    throw new Error(`map servers busy (${(e.errors || [e]).map((x) => x.message).join("; ")})`);
  } finally {
    clearTimeout(timer);
    ctl.abort(); // stop the slower servers
  }
}

/** Only nodes within radius of pos and the road pieces between them, minimal fields. */
export function trim(elements, pos, radiusM) {
  const keep = new Map();
  for (const e of elements) {
    if (e.type === "node" && haversineM(pos, [e.lat, e.lon]) <= radiusM) {
      keep.set(e.id, { type: "node", id: e.id, lat: +e.lat.toFixed(6), lon: +e.lon.toFixed(6) });
    }
  }
  const ways = [];
  for (const e of elements) {
    if (e.type !== "way") continue;
    const nodes = (e.nodes || []).filter((id) => keep.has(id));
    if (nodes.length < 2) continue;
    const tags = { highway: e.tags?.highway || "" };
    if (e.tags?.oneway) tags.oneway = e.tags.oneway;
    ways.push({ type: "way", id: e.id, nodes, tags });
  }
  const used = new Set(ways.flatMap((w) => w.nodes));
  return [...[...keep.values()].filter((n) => used.has(n.id)), ...ways];
}

export function _resetForTests() { cache = null; }
