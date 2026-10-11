"""Capture real product footage from a running coordinator for the demo video."""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE, CODE, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(f"{BASE}/join/{CODE}"); pg.wait_for_timeout(1200)
    pg.screenshot(path=OUT / "join.png")
    pg.fill("#nick", "maya"); pg.press("#nick", "Enter"); pg.wait_for_selector("text=Models available now"); pg.wait_for_timeout(1500)
    pg.screenshot(path=OUT / "overview.png")
    pg.goto(f"{BASE}/nodes"); pg.wait_for_timeout(1500); pg.screenshot(path=OUT / "nodes.png")
    pg.goto(f"{BASE}/run"); pg.wait_for_timeout(1500)
    if pg.locator("text=I understand").count(): pg.click("text=I understand")
    pg.fill("textarea", "In three sentences, explain how sharing idle laptops could make AI cheaper for a student team.")
    pg.wait_for_timeout(400); pg.screenshot(path=OUT / "run-ready.png")
    pg.click('button:text-is("Run")')
    t0 = time.time(); i = 0
    while time.time() - t0 < 25:
        pg.screenshot(path=OUT / "stream" / f"f{i:03d}.png"); i += 1
        if pg.locator("text=Copy request ID").count(): break
        pg.wait_for_timeout(60)
    pg.wait_for_timeout(800)
    pg.screenshot(path=OUT / "run-done.png", full_page=True)
    pg.goto(f"{BASE}/usage"); pg.wait_for_timeout(1500); pg.screenshot(path=OUT / "usage.png", full_page=True)
    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3).new_page()
    m.goto(f"{BASE}/join/{CODE}"); m.wait_for_timeout(1200); m.screenshot(path=OUT / "mobile-join.png")
    b.close()
    print("stream frames:", i)
