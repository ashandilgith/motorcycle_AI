# Native mobile app (later)

The MVP phone app is the PWA in `../web/`. A native or Flutter Android app comes later, so alerts can keep working with the screen off (a foreground location service). Most Manila riders are on Android, so start there.

## What the phone does

| Job | Runs on the phone because |
| --- | --- |
| Curve-speed loop (1 Hz) | A server round trip is too slow, and the signal drops in traffic |
| Beep + Taglish voice (TTS) through the helmet's Bluetooth | The rider never looks at the screen while moving |
| Recording 10 s idle + 3 revs | Same phone position every time (e.g. 30 cm from the exhaust) |
| Camera: tyre, odometer, quote photos | Uploaded to the backend |

## Curve-speed loop contract

Port `web/js/alert.js` (itself a port of `backend/app/curves/{geometry,alert}.py`). Check it against Python the way `backend/tests/test_js_parity.py` does. Sounds: `web/js/beeper.js` (urgent = 2.4 kHz square-wave triple beeps, repeated every 450 ms until speed is safe).

1. When a ride starts, call `POST /ride/start {position, heading_deg, destination?}`. Cache `points`, `curves`, `hotspots` and `conditions`.
2. On each GPS fix (`speed`, `lat`, `lon`):
   1. Compute `pos_m = distance_along(points, cum, position)`.
   2. Compute `alert = evaluate(pos_m, speed_kmh, curves, RideConditions(wet, night))`.
   3. If the level rises to CAUTION, play a single beep. If it rises to URGENT, speak `message_tl`.
   4. Call `hotspot_ahead(pos_m, hotspots)`. Speak it once per hotspot.
3. Refresh `wet` every 10 minutes with `GET /weather`.
4. Call `/ride/start` again when either:
    - you are more than 40 m off the path for 5 s (took a turn), or
    - you are within 300 m of the end of an OSM "road ahead".

Tune the constants in `alert.py` (braking, reaction time, caution factor) and the dry/wet lateral grip in `geometry.py` with real riders before release.
