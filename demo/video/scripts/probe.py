"""Grab a handful of stills so composition can be checked before committing to a full render."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
from localserver import serve

def probe(scene, manifest, out_dir, times=None):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    meta = json.loads(Path(manifest).read_text())
    if times is None:
        times = [round(l["start"] + (l["end"]-l["start"])*0.72, 2) for l in meta["lines"]]
    errs = []
    scene_p = Path(scene).resolve()
    with serve(scene_p.parent.parent) as base, sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb","--disable-lcd-text","--font-render-hinting=none"])
        pg = b.new_page(viewport={"width":1920,"height":1080}, device_scale_factor=1)
        pg.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type=="error" else None)
        pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        pg.goto(f"{base}/{scene_p.parent.name}/{scene_p.name}")
        pg.wait_for_function("window.__ready === true", timeout=60000)
        for t in times:
            pg.evaluate("t => window.seek(t)", t)
            pg.screenshot(path=str(out/f"t{t:06.2f}.png"), animations="disabled")
        b.close()
    print("ERRORS:", errs if errs else "none")
    print("stills:", ", ".join(f"t{t:06.2f}.png" for t in times))

if __name__ == "__main__":
    probe(sys.argv[1], sys.argv[2], sys.argv[3])
