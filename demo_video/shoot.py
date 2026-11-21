"""Render film frames. Usage: python shoot.py preview  |  python shoot.py A  |  python shoot.py B"""
import asyncio, json, sys, os
from playwright.async_api import async_playwright

async def main(mode):
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome")
        page = await b.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        await page.goto("http://localhost:8822/film.html")
        await page.evaluate("film.init()")
        await page.wait_for_timeout(500)
        keys = ["A", "B"] if mode == "preview" else [mode]
        for key in keys:
            n = await page.evaluate(f"film.startRide('{key}')")
            if mode == "preview":
                tl = json.load(open("timeline.json"))["rides"][key]["frames"]
                # pick key moments: approach, first caution, urgent+braking, curve, hotspot
                picks = {}
                for i, f in enumerate(tl):
                    tag = f["level"] + ("_brake" if f["braking"] else "")
                    picks.setdefault(tag, i)
                    if f["phase"] == "curve": picks.setdefault("curve", i + 30)
                    if f["along"] > 350: picks.setdefault("hotspot", i)
                want = sorted(set(picks.values()))
                j = 0
                for i in range(n):
                    await page.evaluate(f"film.drawFrame({i})")
                    if i in want:
                        await page.screenshot(path=f"prev_{key}_{i:04d}.jpg", type="jpeg", quality=85)
            else:
                os.makedirs(f"frames_{key}", exist_ok=True)
                for i in range(n):
                    await page.evaluate(f"film.drawFrame({i})")
                    await page.screenshot(path=f"frames_{key}/{i:05d}.jpg", type="jpeg", quality=92)
            ev = await page.evaluate("film.events()")
            json.dump(ev, open(f"events_{key}.json", "w"))
            print(key, n, "frames; events:", [(round(e['t'],2), e['type'], e.get('name') or e.get('text')) for e in ev])
        print("errors:", errs)
        await b.close()
asyncio.run(main(sys.argv[1]))
