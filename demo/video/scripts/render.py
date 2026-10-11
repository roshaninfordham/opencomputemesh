"""Deterministic frame capture. Renders at 2x and downsamples with lanczos, which is
what keeps thin light type crisp instead of shimmery. Frames are piped straight into
ffmpeg — nothing touches disk."""
import json, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from localserver import serve

W, H, FPS, SS = 1920, 1080, 30, 1       # SS = supersample factor


def render(scene_html, manifest_path, out_mp4, fps=FPS):
    scene = Path(scene_html).resolve()
    meta = json.loads(Path(manifest_path).read_text())
    duration = meta["duration"]
    total = int(round(duration * fps))

    ff = subprocess.Popen([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "image2pipe", "-vcodec", "png", "-framerate", str(fps), "-i", "-",
        "-vf", f"scale={W}:{H}:flags=lanczos",
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-pix_fmt", "yuv420p", str(out_mp4),
    ], stdin=subprocess.PIPE)

    with serve(scene.parent.parent) as base, sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb",
                                     "--disable-lcd-text",
                                     "--font-render-hinting=none"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=SS)
        pg.goto(f"{base}/{scene.parent.name}/{scene.name}")
        pg.wait_for_function("window.__ready === true", timeout=60000)

        for i in range(total):
            pg.evaluate("t => window.seek(t)", i / fps)
            ff.stdin.write(pg.screenshot(type="png", animations="disabled"))
            if i % 60 == 0:
                print(f"  frame {i}/{total}  ({i/fps:5.1f}s)", flush=True)
        b.close()

    ff.stdin.close()
    if ff.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    print(f"  {total} frames -> {out_mp4}")


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], sys.argv[3])
