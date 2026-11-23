# motorcycle_AI

motorcycle_AI helps Metro Manila riders prepare for curves before they can see them, and catch maintenance problems early.

This is the MVP skeleton: a Python backend with three features. Each one works end to end on synthetic data and has tests.

| Feature | Solves | Code |
| --- | --- | --- |
| **Curve-speed alert**: beep or Taglish voice when you're too fast for a curve ahead (lower limit when wet) | Crashes | `backend/app/curves/` |
| **Sound + photo check**: engine-sound anomaly detection against *your* bike's healthy baseline; tyre check with a vision LLM | Won't start, strange noises, tyre wear | `backend/app/sound/`, `backend/app/llm/vision.py` |
| **Bike-aware assistant**: LLM agent that knows your model, odometer, service log and sound checks | Triage, maintenance schedule, quotes | `backend/app/llm/assistant.py`, `backend/app/maintenance.py` |

## Architecture

```
Phone (web/ PWA; Android later)     Backend (FastAPI, Python)            Services
─────────────────────────────       ─────────────────────────            ────────
Ride-risk loop  <── /ride/start ─── Curve pre-processor  <────────────── Google Routes API / OSM Overpass,
                                    + hotspots                           Open-Meteo, hotspots.geojson
Sound recorder  ── WAV ───────────> Anomaly service      ──────────────> SQLite (bikes, log, clips, labels)
Camera          ── photo ─────────> Vision check (VLM)   ──────────────> LLM / VLM API (Claude)
Chat + voice    ── text ──────────> LLM agent + tools    ──────────────> LLM API; Places API (shops)
```

The curve check runs **on the phone**. The server only computes the curves once per route.

## Use it on your phone (MVP)

The MVP phone app is a **mobile web app (PWA)** in `web/`. The backend serves it at `/app/`. Open it in Chrome on Android and choose **Add to Home screen**, and it runs full-screen like an app.

- **The instruction is the biggest text:** a small "CURVE IN" label above a huge **"35 m"**. That distance is **what's left after your 1.5 s reaction time** at your current speed. At 65 km/h, a curve 61 m away on the map shows as 35 m. The raw map distance is shown smaller underneath.
- **Beeps by default.** You're too fast for a curve ahead: two short beeps first, then **rapid, sharp, high-pitched beeps that keep repeating until you're at a safe speed**. A crash hotspot gets one long low tone. The screen also turns amber, then flashing red.
- **Voice is an option.** In Settings, choose *Beeps + voice* or *Voice only*, in Taglish or English.
- **Demo ride** plays a simulated ride into a sharp curve, so you can hear the alerts at your desk. **Test beeps** plays the sounds.
- **Wet / Dry** chip: switches to wet-road safe speeds yourself. It also turns on automatically when it's raining.
- **Warning sensitivity** (Settings): Cautious, Normal or Relaxed. Use it to tune on test rides. You always get at least 4 s of warning before a curve you're too fast for.
- **First-run safety screen:** explains the app's limits. The rider must accept it before the first ride.
- **Garage:** register your bike, log services, see what's due (NMAX intervals from Yamaha's manual, the rest marked as estimates), record a 10 s engine-sound check, check a tyre photo, ask the assistant, and delete your data.

**GPS needs HTTPS** on a phone (the browser blocks location on plain http, except on localhost). The quickest test is a tunnel:

```bash
cd backend && uvicorn app.main:app --port 8000
cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
# open https://<random>.trycloudflare.com/app/ on the phone
```

For real use, deploy the backend anywhere with HTTPS (Render, Fly.io, Cloud Run).

**Limits of a web app:** the screen has to stay on and the app has to stay open; the app keeps the screen awake itself. That's fine for a handlebar-mounted phone. For alerts with the screen off or the app in the background, ship the Android app (below).

### Path to an Android app

1. **Fast:** wrap this PWA as a Trusted Web Activity with [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap). You get a Play Store APK with the same code, but the same foreground-only limit.
2. **Full:** a native or Flutter app with a foreground location service, so alerts work with the screen off. Port `web/js/alert.js`; it's ~100 lines, and `backend/tests/test_js_parity.py` shows how to check a port against Python.

**Demo video:** `demo_video/` renders a split-screen video (animated ride + the real app + its beeps and voice). See its README.

## Deploy (pilot)

1. **Host:** push to GitHub, then on [Render](https://render.com) choose **New → Blueprint** and pick this repo. `render.yaml` sets up a Docker web service in Singapore with HTTPS, a health check and a 1 GB disk for the database. Any Docker host works (`Dockerfile`); keep `/data` on a persistent disk.
2. **Keys:** in the host's dashboard, set `GOOGLE_MAPS_API_KEY` (enable Routes, Roads, Places API (New)) and `ANTHROPIC_API_KEY`. Never commit them.
3. **Road data** (for riding without a destination). Nothing to do per road:
   - **Region:** the "Build region road data" GitHub workflow builds every road in Metro Manila, Rizal, Cavite, Laguna, Bulacan and Batangas from the OpenStreetMap extract, monthly (or Actions → Run workflow). It stores them as a release file and updates `backend/data/region_tiles.json`; Render redeploys and the Docker build downloads them (`backend/tools/fetch_region_tiles.py`). To cover more, widen `REGIONS` in `backend/tools/tiles_from_pbf.py` and `BBOX` in the workflow.
   - **Anywhere else:** the server answers `need_roads: true` straight away, the phone downloads the roads around it from OpenStreetMap itself (`web/js/roads.js`, 1.5 km circle, reused while the rider stays within 500 m of where it was fetched) and sends them with the ride request. Slower (the public map servers can take 10–30 s) and needs mobile data, but works everywhere. `OVERPASS_LIVE=0` (set in `render.yaml`) stops the server trying those servers itself; they don't answer Render.
   - Small extra areas without waiting a month: "Build road tiles" workflow with a box, or `python -m tools.prewarm_osm --around 14.6240,121.2064 --km 3`.
4. **Phone:** open `https://<your-app>.onrender.com/app/` in Chrome on Android, then **Add to Home screen**.
5. **Fill in** `[operator name]` and the contact emails in `web/privacy.html` and `web/terms.html`, and have a lawyer review both before a public launch.

GitHub Actions (`.github/workflows/ci.yml`) runs the tests, the JavaScript checks and a Docker build plus health check on every push.

## Security and privacy

- **Bike access keys:** registering a bike returns a secret key, shown once and kept on the phone. Every `/bikes/{id}` call needs it in `X-Bike-Token`. The server stores only a hash. Without the key you get 404, so ids can't be probed.
- **AI and Google endpoints** (`/tyre/check`, `/shops/nearby`, `/roads/snap`) need a registered bike's key, because they cost money per call.
- **Rate limits** per IP: ride start 30/min, weather 30/min, AI and shops 10/min.
- **Upload limits:** sound 5 MB, images 8 MB.
- **Erasure:** `DELETE /bikes/{id}` removes the bike, its service log and its sound data. The phone has a "Delete my bike data" button.
- **Not stored:** ride locations, raw engine audio, tyre photos and assistant messages.
- **Error logging:** server errors and the phone's JavaScript errors (`/client-errors`) go to the server log.

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                    # 52 tests, no API keys or network needed
python -m tools.simulate_ride --wet    # replay a ride, see/hear the alerts
uvicorn app.main:app --reload          # app: http://localhost:8000/app/  ·  API docs: /docs
```

Optional keys (see `.env.example`): `ANTHROPIC_API_KEY` for the assistant and tyre check, and `GOOGLE_MAPS_API_KEY` for planned routes, GPS snapping and shop search. Without them, those endpoints return 503. Everything else works. `/ride/start` falls back to OpenStreetMap when there's no Google key.

## Maps and context integrations

| Need | Service | Key? | Notes |
| --- | --- | --- | --- |
| Planned route (destination set) | Google **Routes API** `computeRoutes` | Google | The legacy Directions API can't be enabled on new projects. `TWO_WHEELER` mode covers PH but costs more; set `ROUTE_TRAVEL_MODE=DRIVE` to save |
| Road ahead (no destination) | **OpenStreetMap** via Overpass | none | Follows the straightest legal road from your GPS heading and respects one-way streets. The public server is rate-limited, so self-host or pre-extract Metro Manila before launch |
| Clean up recorded GPS traces | Google **Roads API** `snapToRoads` | Google | Batched 100 points per call |
| Open repair shops nearby | Google **Places API (New)** Text Search | Google | Field mask keeps billing to the fields we use. Also an assistant tool |
| Wet road flag | **Open-Meteo** | none | Current + last-hour rain. Manila only gets interpolated hourly data; a radar nowcast is the upgrade |
| Crash hotspots | `backend/data/hotspots.geojson` | none | **Empty on purpose.** Fill from MMDA/LGU crash data |

Every external call goes through `app/integrations/http.py`, so tests swap in a mock transport. If one service fails, `/ride/start` adds a warning and keeps going instead of failing the whole ride.

## API

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/health` | Status, plus which keys are configured |
| POST | `/bikes` | Register a bike (make, model, year, odometer, usage: commuter/gig/diy) |
| POST | `/bikes/{id}/service` | Log a service (e.g. `engine_oil` at 5,200 km) |
| GET | `/bikes/{id}/maintenance` | What's overdue or due soon |
| GET | `/app/` | The phone app (+ `/app/privacy.html`, `/app/terms.html`) |
| DELETE | `/bikes/{id}` | Erase a bike and its data (needs `X-Bike-Token`) |
| GET | `/bikes/{id}/service` | Service log |
| POST | `/client-errors` | Phone-side error reports |
| GET | `/demo/ride` | Synthetic road for the demo ride |
| POST | `/ride/start` | **Main call for the phone**: route (Google) or road ahead (OSM) + curves + weather + hotspots |
| POST | `/routes/curves` | Google route → curves with safe dry/wet speeds |
| POST | `/routes/curves/from-points` | Same, for your own polyline (no key needed) |
| POST | `/ride/alert` | Reference version of the on-phone alert loop |
| POST | `/roads/snap` | Snap a GPS trace to roads |
| GET | `/shops/nearby?lat=&lon=` | Open motorcycle repair shops |
| GET | `/weather?lat=&lon=` | Rain now / last hour / next hour → `wet` |
| POST | `/bikes/{id}/sound?baseline=true` | Upload a WAV; the first 3 healthy clips set the baseline |
| POST | `/clips/{id}/label` | Mechanic-confirmed finding → training label |
| POST | `/tyre/check` | Tyre photo → JSON (tread, cracks, bulge, nail, DOT code) |
| POST | `/bikes/{id}/chat` | Ask the assistant |

## How the curve alert decides

- Each curve's radius comes from three points on the resampled road line.
- Safe speed: `v = sqrt(a_lat × r)`, with `a_lat` = 2.5 m/s² dry and 1.5 m/s² wet.
- It alerts when you're faster than the safe speed and closer than the distance you need to slow down (1.5 s reaction time + 3 m/s² braking). A single beep (CAUTION) comes at 1.6× that distance, and a voice warning (URGENT) at 1×.

**All of these constants are starting guesses.** Tune them with real riders before trusting the alert.

## Not done yet (by design)

- [ ] Android app with background alerts (see "Path to an Android app")
- [ ] Replace the placeholder intervals in `maintenance.py` with figures from each model's owner's manual
- [ ] Self-hosted Overpass or pre-extracted Metro Manila roads (the public Overpass server is rate-limited)
- [ ] Fill `hotspots.geojson` from MMDA/LGU crash data
- [ ] Radar rain nowcast in place of hourly Open-Meteo
- [ ] Owner's-manual retrieval for the assistant
- [ ] Pretrained audio embedding in place of the numpy log-mel summary
- [ ] Fault classifier, once there are enough labelled clips
- [ ] Postgres + auth
