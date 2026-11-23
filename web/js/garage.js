// Garage: the maintenance side of the app (bike, service log, what's due, engine sound, tyre photo, assistant).
// Talks to the backend with the bike's access key (X-Bike-Token), which lives only on this phone.

const $ = (id) => document.getElementById(id);
const KEY = "moto.bike";

const ITEMS = {
  v_belt: "V-belt", engine_oil: "Engine oil", gear_oil: "Gear oil", cvt_cleaning: "CVT cleaning", air_filter: "Air filter",
  spark_plug: "Spark plug", coolant: "Coolant", brake_pads_check: "Brake pads check",
};

function loadBike() { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return window.__bike || null; } }
function saveBike(b) { try { b ? localStorage.setItem(KEY, JSON.stringify(b)) : localStorage.removeItem(KEY); } catch { window.__bike = b; } }

async function api(path, { method = "GET", json, form } = {}) {
  const bike = loadBike();
  const headers = {};
  if (bike?.token) headers["X-Bike-Token"] = bike.token;
  if (json) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { method, headers, body: json ? JSON.stringify(json) : form });
  if (r.status === 204) return null;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`;
    if (r.status === 503) throw new Error("This feature isn't switched on yet. Try again later.");
    if (r.status === 429) throw new Error("Too many tries in a minute. Wait a bit and try again.");
    throw new Error(msg);
  }
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function say(id, text, kind = "") { const el = $(id); el.textContent = text; el.className = `out ${kind}`; }

// ---------- render ----------
async function render() {
  const bike = loadBike();
  $("gNoBike").hidden = !!bike;
  $("gHasBike").hidden = !bike;
  if (!bike) return;
  $("gBikeName").textContent = `${bike.make} ${bike.model}${bike.year ? " " + bike.year : ""}`;
  try {
    const fresh = await api(`/bikes/${bike.id}`);
    $("gOdo").textContent = `${Math.round(fresh.odometer_km).toLocaleString()} km · ${fresh.usage}`;
    $("svcOdo").value = Math.round(fresh.odometer_km) || "";
    await renderDue(bike);
  } catch (e) {
    if (/not found/i.test(e.message)) { saveBike(null); return render(); } // deleted elsewhere / server reset
    $("gOdo").textContent = `Offline: ${e.message}`;
  }
}

async function renderDue(bike) {
  const due = await api(`/bikes/${bike.id}/maintenance`);
  $("gDue").innerHTML = due.map((r) => {
    const label = ITEMS[r.item] || r.item;
    const when = r.status === "unknown" ? "no record yet" : r.status === "overdue" ? `${Math.abs(r.km_remaining).toLocaleString()} km overdue` : `in ${r.km_remaining.toLocaleString()} km`;
    const src = r.source === "manual" ? `<small>from the maker's manual</small>` : `<small>estimate</small>`;
    return `<li class="${r.status}"><span>${esc(label)} ${src}</span><b>${esc(when)}</b></li>`;
  }).join("") + `<li class="note">"Estimate" items aren't from your bike's manual yet. Check it to be sure. Heavy traffic and delivery riding shorten intervals.</li>`;
}

// ---------- actions ----------
async function register(ev) {
  ev.preventDefault();
  const body = {
    make: $("bMake").value.trim(), model: $("bModel").value.trim(),
    year: $("bYear").value ? Number($("bYear").value) : null,
    odometer_km: Number($("bOdo").value || 0), usage: $("bUsage").value,
  };
  if (!body.make || !body.model) return say("gRegOut", "Enter make and model.", "bad");
  try {
    const b = await api("/bikes", { method: "POST", json: body });
    saveBike({ id: b.id, token: b.token, make: b.make, model: b.model, year: b.year });
    say("gRegOut", "");
    render();
  } catch (e) { say("gRegOut", e.message, "bad"); }
}

async function logService(ev) {
  ev.preventDefault();
  const bike = loadBike();
  try {
    await api(`/bikes/${bike.id}/service`, { method: "POST", json: {
      item: $("svcItem").value, odometer_km: Number($("svcOdo").value), cost_php: $("svcCost").value ? Number($("svcCost").value) : null,
    } });
    say("svcOut", "Saved.", "ok");
    $("svcCost").value = "";
    render();
  } catch (e) { say("svcOut", e.message, "bad"); }
}

// Engine sound: record N seconds of raw audio and encode a 16-bit WAV in the browser
// (phones record webm/mp4 by default; the server reads WAV).
async function recordSound() {
  const bike = loadBike();
  const secs = 10;
  const btn = $("sndBtn");
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  } catch (e) { return say("sndOut", "Microphone blocked. Allow it in the browser's site settings.", "bad"); }
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(proc); proc.connect(ctx.destination);
  btn.disabled = true;
  for (let s = secs; s > 0; s--) { btn.textContent = `Recording… ${s}s (idle, then 3 revs)`; await new Promise((r) => setTimeout(r, 1000)); }
  proc.disconnect(); src.disconnect(); stream.getTracks().forEach((t) => t.stop());
  const wav = encodeWav(chunks, ctx.sampleRate);
  ctx.close();
  btn.textContent = "Checking…";
  const form = new FormData();
  form.append("file", new Blob([wav], { type: "audio/wav" }), "engine.wav");
  try {
    const base = $("sndBaseline").checked;
    const r = await api(`/bikes/${bike.id}/sound?baseline=${base}`, { method: "POST", form });
    if (base) say("sndOut", `Saved as healthy sound (${r.baseline_clips} of 3 needed). Record from the same spot each time.`, "ok");
    else if (r.baseline_clips < 3) say("sndOut", r.note, "");
    else say("sndOut", `${r.is_anomaly ? "⚠ " : "✓ "}${r.note} (score ${r.score})`, r.is_anomaly ? "bad" : "ok");
  } catch (e) { say("sndOut", e.message, "bad"); }
  btn.disabled = false; btn.textContent = "Record 10 s";
}

export function encodeWav(chunks, rate) {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, c[i])) * 0x7fff, true);
  return buf;
}

async function tyreCheck() {
  const file = $("tyreFile").files[0];
  if (!file) return;
  say("tyreOut", "Checking photo…");
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const r = await api("/tyre/check", { method: "POST", form });
    if (r.photo_usable === false) return say("tyreOut", "Photo not clear enough. Try again in daylight, close to the tread.", "bad");
    const bad = r.bulge_or_cut || r.cracks === "severe" || r.tread === "near_limit" || r.embedded_object;
    const parts = [`Tread: ${r.tread}`, `Cracks: ${r.cracks}`];
    if (r.embedded_object) parts.push("Object stuck in tyre");
    if (r.bulge_or_cut) parts.push("Bulge or cut");
    if (r.dot_date_code) parts.push(`Made: week ${r.dot_date_code.slice(0, 2)} of 20${r.dot_date_code.slice(2)}`);
    say("tyreOut", `${bad ? "⚠ " : "✓ "}${parts.join(" · ")}\n${r.advice_tl || r.advice_en || ""}`, bad ? "bad" : "ok");
  } catch (e) { say("tyreOut", e.message, "bad"); }
  $("tyreFile").value = "";
}

const chatLog = [];
async function ask(ev) {
  ev.preventDefault();
  const q = $("askText").value.trim();
  if (!q) return;
  const bike = loadBike();
  chatLog.push({ role: "user", content: q });
  $("askText").value = "";
  drawChat("…");
  try {
    const r = await api(`/bikes/${bike.id}/chat`, { method: "POST", json: { messages: chatLog.slice(-10) } });
    chatLog.push({ role: "assistant", content: r.reply });
  } catch (e) { chatLog.pop(); say("askOut", e.message, "bad"); }
  drawChat();
}
function drawChat(pending) {
  $("askLog").innerHTML = chatLog.map((m) => `<p class="${m.role}">${esc(m.content)}</p>`).join("") + (pending ? `<p class="assistant">${pending}</p>` : "");
  $("askLog").scrollTop = 1e9;
}

let deleteArmed = false;
async function deleteBike() {
  const btn = $("delBtn");
  if (!deleteArmed) { // two taps instead of a blocking confirm() dialog
    deleteArmed = true; btn.textContent = "Tap again to permanently delete";
    setTimeout(() => { deleteArmed = false; btn.textContent = "Delete my bike data"; }, 4000);
    return;
  }
  const bike = loadBike();
  try { await api(`/bikes/${bike.id}`, { method: "DELETE" }); } catch (e) { if (!/not found/i.test(e.message)) return say("delOut", e.message, "bad"); }
  saveBike(null); chatLog.length = 0; deleteArmed = false; btn.textContent = "Delete my bike data";
  render();
}

export function initGarage() {
  $("svcItem").innerHTML = Object.entries(ITEMS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  $("garageBtn").addEventListener("click", () => { $("garage").showModal(); render(); });
  $("gClose").addEventListener("click", () => $("garage").close());
  $("gRegForm").addEventListener("submit", register);
  $("svcForm").addEventListener("submit", logService);
  $("sndBtn").addEventListener("click", recordSound);
  $("tyreFile").addEventListener("change", tyreCheck);
  $("askForm").addEventListener("submit", ask);
  $("delBtn").addEventListener("click", deleteBike);
}
