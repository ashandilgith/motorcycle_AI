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
Ride-risk loop  <── curves ──────── Curve pre-processor  <────────────── Google Directions / OSM, weather
Sound recorder  ── WAV ───────────> Anomaly service      ──────────────> SQLite (bikes, log, clips, labels)
Camera          ── photo ─────────> Vision check (VLM)   ──────────────> LLM / VLM API (Claude)
Chat + voice    ── text ──────────> LLM agent + tools    ──────────────> LLM API; manuals (to add)
```

The curve check runs **on the phone**. The server only computes the curves once per route.

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                    # 19 tests, no API keys needed
python -m tools.simulate_ride --wet    # replay a ride, see/hear the alerts
uvicorn app.main:app --reload          # API docs at http://localhost:8000/docs
```

Optional keys (see `.env.example`): `ANTHROPIC_API_KEY` for the assistant and tyre check, and `GOOGLE_MAPS_API_KEY` for `/routes/curves`. Without them, those endpoints return 503. Everything else works.

## API

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/health` | Status, plus which keys are configured |
| POST | `/bikes` | Register a bike (make, model, year, odometer, usage: commuter/gig/diy) |
| POST | `/bikes/{id}/service` | Log a service (e.g. `engine_oil` at 5,200 km) |
| GET | `/bikes/{id}/maintenance` | What's overdue or due soon |
| POST | `/routes/curves` | Google route → curves with safe dry/wet speeds |
| POST | `/routes/curves/from-points` | Same, for your own polyline (no key needed) |
| POST | `/ride/alert` | Reference version of the on-phone alert loop |
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
- [ ] OpenStreetMap cache, so the alert works without a destination
- [ ] Weather feed for the `wet` flag
- [ ] Owner's-manual retrieval for the assistant
- [ ] Pretrained audio embedding in place of the numpy log-mel summary
- [ ] Fault classifier, once there are enough labelled clips
- [ ] Postgres + auth
