// Ride screen: GPS loop -> curve alert -> beeps (and optional voice).
import { Level, actionDistanceM, cumulative, distanceAlong, evaluate, hotspotAhead } from "./alert.js";
import { Beeper, speak } from "./beeper.js";
import { initGarage } from "./garage.js";

// Report our own JavaScript errors to the server log (no personal data, just the error).
const APP_VERSION = "0.2.0";
function reportError(message, where) {
  try { fetch("/client-errors", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: String(message).slice(0, 2000), where: String(where || "").slice(0, 300), version: APP_VERSION }) }); } catch { /* offline */ }
}
window.addEventListener("error", (e) => reportError(e.message, `${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => reportError(e.reason?.stack || e.reason, "promise"));
let filmSpeak = null;
// delayMs lets the beeps finish before the voice starts (beeps + voice mode)
const say = (text, delayMs = 0) => (filmSpeak ? filmSpeak(text, delayMs) : setTimeout(() => speak(text, settings.voiceLang === "tl" ? "fil-PH" : "en-US"), delayMs));

const $ = (id) => document.getElementById(id);
const API = ""; // same origin as the backend that serves this page

const OFF_ROUTE_M = 40;
const OFF_ROUTE_FIXES = 3;
const BEHIND_START_OFF_M = 15;   // before the start of the fetched path and drifting away = wrong direction
const MAX_ACCURACY_M = 30;       // ignore worse GPS fixes
const SPEED_WINDOW_MS = 3000;    // speed from distance over ~3 s when the phone gives no speed
const HEADING_MIN_MOVE_M = 15;   // direction from movement once we've moved this far
const WAIT_FOR_HEADING_MS = 20_000;
const GPS_LOST_MS = 10_000;
const REFETCH_NEAR_END_M = 300;
const MIN_REFETCH_MS = 10_000;
const WEATHER_EVERY_MS = 10 * 60_000;

// ---------- settings (localStorage can throw in some webviews) ----------
const defaults = { soundMode: "beeps", voiceLang: "tl", volume: 1, destination: "", sensitivity: "normal" };
const SAFETY_VERSION = "1"; // bump when the safety text changes, so riders see it again
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
let fixes = [], startedAt = 0, lastGoodFixAt = 0, gpsTimer = null, heldAlert = null, lowerCount = 0, liveStatus = "";

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
    const tags = shortWarnings(ride.warnings || []);
    liveStatus = `Live · ${ride.curves.length} curves${tags ? " · " + tags : ""}`;
    setStatus(liveStatus, !tags.includes("no road"));
  } catch (e) {
    setStatus(`Road data failed: ${e.message}`);
  } finally {
    fetching = false;
  }
}

/** Turn backend warnings into a few words for the status chip. */
function shortWarnings(ws) {
  const tags = new Set();
  for (const w of ws) {
    if (/Weather/i.test(w)) tags.add("no weather");
    else if (/OpenStreetMap fetch failed|No usable road/i.test(w)) tags.add("no road data");
    else if (/Google route failed/i.test(w)) tags.add("map fallback");
  }
  return [...tags].join(", ");
}

function setRide(data) {
  const points = data.points || [];
  ride = { ...data, points, cum: cumulative(points), curves: data.curves || [], hotspots: data.hotspots || [] };
  offCount = 0; cautionedCurve = null; announcedHotspots = new Set(); heldAlert = null; lowerCount = 0;
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
    if (demoTimer) return;
    // Without a destination we need the direction of travel, or we may load the road behind the rider.
    const needHeading = !parseLatLon(settings.destination) && heading == null && Date.now() - startedAt < WAIT_FOR_HEADING_MS;
    if (needHeading) { setInstruction("", "Start riding", "Finding your direction…", true); return; }
    fetchRide(position, heading);
    return;
  }
  const { alongM, offM } = distanceAlong(ride.points, ride.cum, position);

  // left the path, rode the other way, or near the end of an OSM "road ahead" -> get fresh road
  const wrongWay = alongM < 2 && offM > BEHIND_START_OFF_M;
  offCount = offM > OFF_ROUTE_M || wrongWay ? offCount + 1 : 0;
  const nearEnd = ride.source === "osm_road_ahead" && ride.cum.at(-1) - alongM < REFETCH_NEAR_END_M;
  if (!demoTimer && (offCount >= OFF_ROUTE_FIXES || nearEnd)) fetchRide(position, heading);

  const cond = conditions();
  const alert = steady(evaluate(alongM, speedKmh, ride.curves, cond));
  const hotspot = hotspotAhead(alongM, ride.hotspots);
  render(alongM, speedKmh, alert, hotspot, cond);
  sound(alert, hotspot);
}

/** Escalate at once; step down only after 2 fixes in a row at the lower level (stops flicker from GPS noise). */
const RANK = { none: 0, caution: 1, urgent: 2 };
function steady(alert) {
  if (demoTimer || !heldAlert || RANK[alert.level] >= RANK[heldAlert.level]) { lowerCount = 0; heldAlert = alert; return alert; }
  lowerCount += 1;
  if (lowerCount >= 2) { lowerCount = 0; heldAlert = alert; return alert; }
  return { ...heldAlert, speedKmh: alert.speedKmh };
}

function conditions() {
  const c = ride?.conditions || {};
  return { wet: wetOverride || !!c.wet, night: !!c.night, sensitivity: settings.sensitivity || "normal" };
}

// ---------- sound policy ----------
function sound(alert, hotspot) {
  const beeps = settings.soundMode !== "voice";
  const voice = settings.soundMode !== "beeps";
  const msg = (a) => (settings.voiceLang === "tl" ? a.messageTl : a.messageEn);

  if (alert.level === Level.URGENT) {
    if (beeps) beeper.loop("urgent");                       // keeps beeping until speed is safe
    if (voice && lastLevel !== Level.URGENT) say(msg(alert), beeps ? 400 : 0);
  } else {
    beeper.stop();
    if (alert.level === Level.CAUTION && cautionedCurve !== alert.curve.start_m) {
      cautionedCurve = alert.curve.start_m;                 // once per curve
      if (beeps) beeper.play("caution");
      if (voice) say(msg(alert), beeps ? 400 : 0);
    }
  }
  if (hotspot && alert.level === Level.NONE && !announcedHotspots.has(hotspot.at_m)) {
    announcedHotspots.add(hotspot.at_m);
    if (beeps) beeper.play("hotspot");
    if (voice) say(msg(hotspot), beeps ? 550 : 0);
  }
  lastLevel = alert.level;
}

// ---------- UI ----------
function render(alongM, speedKmh, alert, hotspot, cond) {
  const next = ride.curves.find((c) => c.end_m >= alongM && c.start_m - alongM <= 2000);
  const safeOf = (c) => (cond.wet ? c.safe_kmh_wet : c.safe_kmh_dry) * (cond.night ? 0.9 : 1);
  $("nextSafe").textContent = next ? `${Math.round(safeOf(next))}` : "—";
  $("nextDist").textContent = !next ? "—" : next.start_m <= alongM ? "in curve" : `${Math.round(next.start_m - alongM)} m`;

  const r5 = (m) => Math.round(m / 5) * 5;
  let level = alert.level;
  if (alert.level !== Level.NONE) {
    const act = r5(alert.actionM);
    const sub = alert.level === Level.URGENT ? `Brake to ${Math.round(alert.safeKmh)} km/h` : `Ease off to ${Math.round(alert.safeKmh)} km/h`;
    if (alert.inCurve) setInstruction("", "TOO FAST", `Ease off gently to ${Math.round(alert.safeKmh)}`, true);
    else if (act < 5) setInstruction("", "BRAKE NOW", sub, true);
    else setInstruction("Curve in", act, sub);
  } else if (hotspot) {
    level = "hotspot";
    setInstruction("Hotspot in", r5(actionDistanceM(hotspot.distanceM, speedKmh)), hotspot.name || "Known crash spot");
  } else if (next && next.start_m <= alongM) {
    setInstruction("", "In curve", `Hold ${Math.round(safeOf(next))} km/h or less`, true);
  } else if (next) {
    const act = r5(actionDistanceM(next.start_m - alongM, speedKmh));
    if (act < 5) setInstruction("", "Curve now", "Speed OK", true);
    else setInstruction("Curve in", act, "Speed OK");
  } else {
    setInstruction("", "Clear road", "No sharp curves ahead", true);
  }
  document.body.dataset.level = level;
}

/** label: small caps line; main: a number of metres (shown huge) or a short word; sub: what to do. */
function setInstruction(label, main, sub, isWord = false) {
  $("instrLabel").textContent = label;
  const el = $("instrMain");
  el.classList.toggle("word", isWord);
  if (isWord) el.textContent = main;
  else el.innerHTML = `${Number(main)}<small>m</small>`;
  $("instrSub").textContent = sub;
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
  const c = pos.coords;
  if (c.accuracy != null && c.accuracy > MAX_ACCURACY_M) return gpsWeak(`GPS weak (±${Math.round(c.accuracy)} m)`);
  const p = [c.latitude, c.longitude];
  const t = pos.timestamp || Date.now();
  lastGoodFixAt = Date.now();
  if (liveStatus && /GPS/.test($("status").textContent)) setStatus(liveStatus, true);
  fixes.push({ p, t });
  while (fixes.length > 2 && t - fixes[0].t > 8000) fixes.shift();

  // Speed: the phone's own (Doppler) speed when it has one; else distance over the last ~3 s (smooths GPS jitter).
  let speedKmh = c.speed != null && !Number.isNaN(c.speed) ? c.speed * 3.6 : null;
  if (speedKmh == null) {
    const old = [...fixes].reverse().find((f) => t - f.t >= SPEED_WINDOW_MS) || fixes[0];
    const dt = (t - old.t) / 1000;
    speedKmh = dt > 0 ? (haversine(old.p, p) / dt) * 3.6 : 0;
  }
  // Heading: the phone's own when moving; else the bearing from where we were ≥15 m ago.
  let heading = c.heading != null && !Number.isNaN(c.heading) && speedKmh > 5 ? c.heading : null;
  if (heading == null) {
    const back = [...fixes].reverse().find((f) => haversine(f.p, p) >= HEADING_MIN_MOVE_M);
    if (back) heading = bearing(back.p, p);
  }
  lastFix = { p, t };
  onFix(p, speedKmh, heading);
}

function gpsWeak(text) { setStatus(text); }

function onGpsError(e) {
  // Errors are often transient (timeouts); only report if we've really had no fix for a while.
  if (Date.now() - lastGoodFixAt > GPS_LOST_MS) {
    const why = { 1: "permission denied", 2: "no signal", 3: "no fix yet" }[e.code] || e.message || "error";
    setStatus(`GPS: ${why}`);
  }
}

function bearing(a, b) {
  const r = (d) => (d * Math.PI) / 180;
  const y = Math.sin(r(b[1] - a[1])) * Math.cos(r(b[0]));
  const x = Math.cos(r(a[0])) * Math.sin(r(b[0])) - Math.sin(r(a[0])) * Math.cos(r(b[0])) * Math.cos(r(b[1] - a[1]));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
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
  startedAt = Date.now(); lastGoodFixAt = Date.now(); fixes = []; heldAlert = null; liveStatus = "";
  watchId = navigator.geolocation.watchPosition(onPosition, onGpsError, {
    enableHighAccuracy: true, maximumAge: 0, timeout: 15000,
  });
  gpsTimer = setInterval(() => { if (Date.now() - lastGoodFixAt > GPS_LOST_MS) setStatus("GPS: no fix"); }, 5000);
  weatherTimer = setInterval(() => lastFix && refreshWeather(lastFix.p), WEATHER_EVERY_MS);
  setRunning(true);
}

function stop() {
  if (watchId != null) navigator.geolocation.clearWatch(watchId);
  clearInterval(weatherTimer); clearInterval(demoTimer); clearInterval(gpsTimer);
  watchId = weatherTimer = demoTimer = gpsTimer = null;
  fixes = []; heldAlert = null; liveStatus = "";
  beeper.stop();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  wakeLock?.release?.(); wakeLock = null;
  ride = null; lastFix = null; lastLevel = Level.NONE;
  document.body.dataset.level = "none";
  $("speed").textContent = "--";
  setInstruction("", "Stopped", "Tap Start to ride", true);
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
    if (along >= ride.cum.at(-1)) { stop(); setInstruction("", "Demo done", "Tap Demo ride to replay", true); return; }
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

// ---------- first-run safety screen ----------
function accepted() { try { return localStorage.getItem("moto.safety") === SAFETY_VERSION; } catch { return window.__safetyOk === true; } }
function showSafety() {
  $("acceptBox").checked = accepted();
  $("acceptBtn").disabled = !$("acceptBox").checked;
  $("onboard").showModal();
}
$("acceptBox").addEventListener("change", () => { $("acceptBtn").disabled = !$("acceptBox").checked; });
$("onboardForm").addEventListener("submit", () => {
  if ($("acceptBox").checked) { try { localStorage.setItem("moto.safety", SAFETY_VERSION); } catch { window.__safetyOk = true; } }
});
$("onboard").addEventListener("cancel", (e) => { if (!accepted()) e.preventDefault(); }); // can't skip with Esc
$("showSafety").addEventListener("click", () => { $("settings").close(); showSafety(); });

$("startBtn").addEventListener("click", () => {
  if (watchId != null || demoTimer) return stop();
  if (!accepted()) return showSafety();
  start();
});
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
  for (const k of ["soundMode", "voiceLang", "volume", "destination", "sensitivity"]) $(k).value = settings[k];
  $("settings").showModal();
});
$("settings").addEventListener("close", () => {
  for (const k of ["soundMode", "voiceLang", "volume", "destination", "sensitivity"]) settings[k] = $(k).value;
  beeper.volume = Number(settings.volume);
  saveSettings();
});

// ---------- film mode (?film): driven frame by frame by the demo-video recorder ----------
// Sounds are logged with simulation time instead of played, so the soundtrack can be built offline.
if (new URLSearchParams(location.search).has("film")) {
  const events = [];
  let t = 0;
  beeper.unlock = () => {};
  beeper.play = (name) => { events.push({ t, type: "beep", name }); return 0; };
  beeper.loop = (name) => { if (beeper.looping !== name) { beeper.looping = name; events.push({ t, type: "loop", name }); } };
  beeper.stop = () => { if (beeper.looping) { events.push({ t, type: "stop", name: beeper.looping }); beeper.looping = null; } };
  filmSpeak = (text, delayMs = 0) => events.push({ t: t + delayMs / 1000, type: "voice", text });
  window.motoFilm = {
    events,
    load(data) { setRide(data); lastLevel = Level.NONE; setStatus("Live · demo road", true); setRunning(true); },
    fix(simT, pos, speed) { t = simT; onFix(pos, speed, null); },
    setWet(on) { wetOverride = on; renderWet(); },
    setSound(mode, lang = "tl") { settings.soundMode = mode; settings.voiceLang = lang; },
    reset() { ride = null; lastLevel = Level.NONE; cautionedCurve = null; announcedHotspots = new Set(); beeper.looping = null; document.body.dataset.level = "none"; },
  };
} else if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
renderWet();
initGarage();
if (!new URLSearchParams(location.search).has("film") && !accepted()) showSafety();
