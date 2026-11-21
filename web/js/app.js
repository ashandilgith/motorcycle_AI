// Ride screen: GPS loop -> curve alert -> beeps (and optional voice).
import { Level, cumulative, distanceAlong, evaluate, hotspotAhead } from "./alert.js";
import { Beeper, speak } from "./beeper.js";

const $ = (id) => document.getElementById(id);
const API = ""; // same origin as the backend that serves this page

const OFF_ROUTE_M = 40;
const OFF_ROUTE_FIXES = 5;
const REFETCH_NEAR_END_M = 300;
const MIN_REFETCH_MS = 10_000;
const WEATHER_EVERY_MS = 10 * 60_000;

// ---------- settings (localStorage can throw in some webviews) ----------
const defaults = { soundMode: "beeps", voiceLang: "tl", volume: 1, destination: "" };
function loadSettings() {
  try { return { ...defaults, ...JSON.parse(localStorage.getItem("moto.settings") || "{}") }; } catch { return { ...defaults }; }
}
function saveSettings() {
  try { localStorage.setItem("moto.settings", JSON.stringify(settings)); } catch { /* ignore */ }
}
const settings = loadSettings();

// ---------- state ----------
const beeper = new Beeper();
let ride = null;          // {points, cum, curves, hotspots, source, conditions}
let wetOverride = false;
let watchId = null, demoTimer = null, wakeLock = null, weatherTimer = null;
let lastFix = null, offCount = 0, lastFetch = 0, fetching = false;
let lastLevel = Level.NONE, cautionedCurve = null, announcedHotspots = new Set();

// ---------- backend ----------
async function fetchRide(position, heading) {
  if (fetching || Date.now() - lastFetch < MIN_REFETCH_MS) return;
  fetching = true; lastFetch = Date.now();
  setStatus("Loading road…");
  try {
    const body = { position, heading_deg: heading ?? null };
    const dest = parseLatLon(settings.destination);
    if (dest) body.destination = dest;
    const r = await fetch(`${API}/ride/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    setRide(await r.json());
    setStatus(`Live · ${ride.curves.length} curves`, true);
  } catch (e) {
    setStatus(`Road data failed: ${e.message}`);
  } finally {
    fetching = false;
  }
}

function setRide(data) {
  const points = data.points || [];
  ride = { ...data, points, cum: cumulative(points), curves: data.curves || [], hotspots: data.hotspots || [] };
  offCount = 0; cautionedCurve = null; announcedHotspots = new Set();
  for (const w of data.warnings || []) console.warn(w);
}

async function refreshWeather(position) {
  try {
    const r = await fetch(`${API}/weather?lat=${position[0]}&lon=${position[1]}`);
    if (r.ok && ride) {
      const wx = await r.json();
      ride.conditions = { wet: wx.wet, night: wx.is_day === false };
      renderWet();
    }
  } catch { /* keep last known */ }
}

// ---------- per-fix loop ----------
function onFix(position, speedKmh, heading) {
  $("speed").textContent = Math.round(speedKmh);
  if (!ride || ride.points.length < 2) {
    if (!demoTimer) fetchRide(position, heading);
    return;
  }
  const { alongM, offM } = distanceAlong(ride.points, ride.cum, position);

  // left the path, or near the end of an OSM "road ahead" -> get fresh road
  offCount = offM > OFF_ROUTE_M ? offCount + 1 : 0;
  const nearEnd = ride.source === "osm_road_ahead" && ride.cum.at(-1) - alongM < REFETCH_NEAR_END_M;
  if (!demoTimer && (offCount >= OFF_ROUTE_FIXES || nearEnd)) fetchRide(position, heading);

  const cond = conditions();
  const alert = evaluate(alongM, speedKmh, ride.curves, cond);
  const hotspot = hotspotAhead(alongM, ride.hotspots);
  render(alongM, speedKmh, alert, hotspot, cond);
  sound(alert, hotspot);
}

function conditions() {
  const c = ride?.conditions || {};
  return { wet: wetOverride || !!c.wet, night: !!c.night };
}

// ---------- sound policy ----------
function sound(alert, hotspot) {
  const beeps = settings.soundMode !== "voice";
  const voice = settings.soundMode !== "beeps";
  const msg = (a) => (settings.voiceLang === "tl" ? a.messageTl : a.messageEn);

  if (alert.level === Level.URGENT) {
    if (beeps) beeper.loop("urgent");                       // keeps beeping until speed is safe
    if (voice && lastLevel !== Level.URGENT) speak(msg(alert));
  } else {
    beeper.stop();
    if (alert.level === Level.CAUTION && cautionedCurve !== alert.curve.start_m) {
      cautionedCurve = alert.curve.start_m;                 // once per curve
      if (beeps) beeper.play("caution");
      if (voice) speak(msg(alert));
    }
  }
  if (hotspot && alert.level === Level.NONE && !announcedHotspots.has(hotspot.at_m)) {
    announcedHotspots.add(hotspot.at_m);
    if (beeps) beeper.play("hotspot");
    if (voice) speak(msg(hotspot));
  }
  lastLevel = alert.level;
}

// ---------- UI ----------
function render(alongM, speedKmh, alert, hotspot, cond) {
  const next = ride.curves.find((c) => c.end_m >= alongM && c.start_m - alongM <= 2000);
  if (next) {
    const safe = (cond.wet ? next.safe_kmh_wet : next.safe_kmh_dry) * (cond.night ? 0.9 : 1);
    $("nextDist").textContent = next.start_m <= alongM ? "Now" : `${Math.round(next.start_m - alongM)} m`;
    $("nextSafe").textContent = `${Math.round(safe)}`;
  } else {
    $("nextDist").textContent = "—";
    $("nextSafe").textContent = "—";
  }
  let level = alert.level, text = "No sharp curves close";
  if (alert.level === Level.URGENT) text = `SLOW DOWN to ${Math.round(alert.safeKmh)}`;
  else if (alert.level === Level.CAUTION) text = `Curve in ${Math.round(alert.distanceM)} m: ease off`;
  else if (hotspot) { level = "hotspot"; text = `Crash hotspot ahead: ${hotspot.name || ""}`; }
  else if (next) text = "OK";
  document.body.dataset.level = level;
  $("banner").textContent = text;
}

function setStatus(text, live = false) {
  $("status").textContent = text;
  $("status").classList.toggle("live", live);
}

function renderWet() {
  const wet = conditions().wet;
  $("wetBtn").textContent = wet ? (wetOverride ? "Wet (manual)" : "Wet (rain)") : "Dry";
  $("wetBtn").setAttribute("aria-pressed", String(wetOverride));
}

// ---------- GPS ----------
function onPosition(pos) {
  const p = [pos.coords.latitude, pos.coords.longitude];
  let speedKmh = pos.coords.speed != null && !Number.isNaN(pos.coords.speed) ? pos.coords.speed * 3.6 : null;
  if (speedKmh == null && lastFix) {
    const dt = (pos.timestamp - lastFix.t) / 1000;
    if (dt > 0) speedKmh = (haversine(lastFix.p, p) / dt) * 3.6;
  }
  lastFix = { p, t: pos.timestamp };
  const heading = pos.coords.heading != null && !Number.isNaN(pos.coords.heading) && (speedKmh ?? 0) > 5 ? pos.coords.heading : null;
  onFix(p, speedKmh ?? 0, heading);
}

function haversine(a, b) {
  const r = (d) => (d * Math.PI) / 180;
  const h = Math.sin(r(b[0] - a[0]) / 2) ** 2 + Math.cos(r(a[0])) * Math.cos(r(b[0])) * Math.sin(r(b[1] - a[1]) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

// ---------- start / stop ----------
async function start() {
  beeper.unlock();
  beeper.volume = Number(settings.volume);
  await keepAwake();
  if (!("geolocation" in navigator)) return setStatus("No GPS on this device");
  if (!window.isSecureContext) return setStatus("GPS needs HTTPS (see README)");
  setStatus("Waiting for GPS…");
  watchId = navigator.geolocation.watchPosition(onPosition, (e) => setStatus(`GPS: ${e.message}`), {
    enableHighAccuracy: true, maximumAge: 0, timeout: 15000,
  });
  weatherTimer = setInterval(() => lastFix && refreshWeather(lastFix.p), WEATHER_EVERY_MS);
  setRunning(true);
}

function stop() {
  if (watchId != null) navigator.geolocation.clearWatch(watchId);
  clearInterval(weatherTimer); clearInterval(demoTimer);
  watchId = weatherTimer = demoTimer = null;
  beeper.stop();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  wakeLock?.release?.(); wakeLock = null;
  ride = null; lastFix = null; lastLevel = Level.NONE;
  document.body.dataset.level = "none";
  $("speed").textContent = "--";
  $("banner").textContent = "Stopped";
  setStatus("Ready");
  setRunning(false);
}

function setRunning(on) {
  $("startBtn").textContent = on ? "Stop" : "Start ride";
  $("startBtn").classList.toggle("stop", on);
  $("demoBtn").disabled = on;
}

async function keepAwake() {
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* not supported */ }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && (watchId != null || demoTimer)) keepAwake();
});

// ---------- demo ride: simulated rider who brakes ~2 s after the urgent beeps ----------
async function demo() {
  beeper.unlock();
  beeper.volume = Number(settings.volume);
  setStatus("Demo ride");
  const r = await fetch(`${API}/demo/ride`);
  if (!r.ok) return setStatus("Demo failed to load");
  setRide(await r.json());
  setRunning(true);
  $("demoBtn").disabled = true;
  let along = 0, speed = 65, urgentFor = 0;
  const TICK = 0.25; // s
  demoTimer = setInterval(() => {
    const cond = conditions();
    const a = evaluate(along, speed, ride.curves, cond);
    urgentFor = a.level === Level.URGENT ? urgentFor + TICK : 0;
    const inCurve = ride.curves.some((c) => along >= c.start_m && along <= c.end_m);
    if (urgentFor > 2) speed = Math.max(speed - 3 * 3.6 * TICK, 20);   // rider reacts and brakes
    else if (!inCurve && a.level === Level.NONE && speed < 65 && along > ride.curves.at(-1)?.end_m) speed += 1.5 * 3.6 * TICK;
    along += (speed / 3.6) * TICK;
    if (along >= ride.cum.at(-1)) { stop(); $("banner").textContent = "Demo finished"; return; }
    onFix(pointAt(along), speed, null);
  }, TICK * 1000);
}

function pointAt(along) {
  const { points, cum } = ride;
  let i = cum.findIndex((c) => c >= along);
  if (i <= 0) return points[0];
  const t = (along - cum[i - 1]) / (cum[i] - cum[i - 1] || 1);
  return [points[i - 1][0] + t * (points[i][0] - points[i - 1][0]), points[i - 1][1] + t * (points[i][1] - points[i - 1][1])];
}

// ---------- wiring ----------
function parseLatLon(s) {
  const m = String(s || "").split(",").map((x) => parseFloat(x));
  return m.length === 2 && m.every(Number.isFinite) ? m : null;
}

$("startBtn").addEventListener("click", () => (watchId != null || demoTimer ? stop() : start()));
$("demoBtn").addEventListener("click", demo);
$("testBtn").addEventListener("click", () => {
  beeper.unlock();
  beeper.volume = Number(settings.volume);
  beeper.play("caution");
  setTimeout(() => beeper.loop("urgent"), 700);
  setTimeout(() => beeper.stop(), 3000);
});
$("wetBtn").addEventListener("click", () => { wetOverride = !wetOverride; renderWet(); });
$("settingsBtn").addEventListener("click", () => {
  for (const k of ["soundMode", "voiceLang", "volume", "destination"]) $(k).value = settings[k];
  $("settings").showModal();
});
$("settings").addEventListener("close", () => {
  for (const k of ["soundMode", "voiceLang", "volume", "destination"]) settings[k] = $(k).value;
  beeper.volume = Number(settings.volume);
  saveSettings();
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
renderWet();
