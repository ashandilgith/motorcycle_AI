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
Phone (to build, see mobile/)       Backend (FastAPI, Python)            Services
─────────────────────────────       ─────────────────────────            ────────
Ride-risk loop  <── /ride/start ─── Curve pre-processor  <────────────── Google Routes API / OSM Overpass,
                                    + hotspots                           Open-Meteo, hotspots.geojson
Sound recorder  ── WAV ───────────> Anomaly service      ──────────────> SQLite (bikes, log, clips, labels)
Camera          ── photo ─────────> Vision check (VLM)   ──────────────> LLM / VLM API (Claude)
Chat + voice    ── text ──────────> LLM agent + tools    ──────────────> LLM API; Places API (shops)
```

The curve check runs **on the phone**. The server only computes the curves once per route.

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                    # 33 tests, no API keys or network needed
python -m tools.simulate_ride --wet    # replay a ride, see/hear the alerts
uvicorn app.main:app --reload          # API docs at http://localhost:8000/docs
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

- [ ] Phone app (see `mobile/README.md`)
- [ ] Replace the placeholder intervals in `maintenance.py` with figures from each model's owner's manual
- [ ] Self-hosted Overpass or pre-extracted Metro Manila roads (the public Overpass server is rate-limited)
- [ ] Fill `hotspots.geojson` from MMDA/LGU crash data
- [ ] Radar rain nowcast in place of hourly Open-Meteo
- [ ] Owner's-manual retrieval for the assistant
- [ ] Pretrained audio embedding in place of the numpy log-mel summary
- [ ] Fault classifier, once there are enough labelled clips
- [ ] Postgres + auth
