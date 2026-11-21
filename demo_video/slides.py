import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome")
        page = await b.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto("http://localhost:8822/slides.html")
        for sid in ["title", "problem", "how", "features", "end"]:
            await page.evaluate(f"show('{sid}')")
            await page.wait_for_timeout(150)
            await page.screenshot(path=f"slide_{sid}.png")
        await b.close()
asyncio.run(main())
