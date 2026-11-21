# Demo video

Renders `Moto_AI_demo.mp4`: a split screen with the animated ride on the left and the **real phone app** on the right. The app runs in film mode (`/app/?film`). The soundtrack is built from the app's own sound log, so the beeps and voice happen exactly when the app makes them.

Requires Python + Playwright (Chrome), ffmpeg and espeak-ng.

```bash
# 1. backend running (for the demo road)
cd backend && uvicorn app.main:app --port 8000 &
# 2. from demo_video/
curl -s localhost:8000/demo/ride > demo.json
ln -sfn ../web app                   # film page and app on one origin
python -m http.server 8822 &          # shoot.py/slides.py load http://localhost:8822
python sim.py                         # rider simulation -> timeline.json
python slides.py                      # title/feature slides -> slide_*.png
python shoot.py A && python shoot.py B    # ride frames + events_*.json
python build.py                       # encode, soundtrack, join -> Moto_AI_demo.mp4
```

Rides:
- **Ride 1, dry, beeps only:** the rider ignores the 2 caution beeps. Rapid beeps start, the rider brakes 1.5 s later, and the beeps stop once the speed is safe.
- **Ride 2, rain, beeps + voice:** the safe speed drops to 28 km/h. The rider eases off after the voice warning.
