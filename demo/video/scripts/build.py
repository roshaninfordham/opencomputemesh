"""End-to-end build for one ad: voice -> sound design -> frames -> mixed master."""
import json, subprocess, sys, time
from pathlib import Path
import tts, sfx, render

ROOT = Path(__file__).resolve().parent.parent


def mix(ad, out_wav):
    """Voice sits on top; bed and effects duck under it via sidechain compression."""
    a = ad / "audio"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(a / "voice.wav"), "-i", str(a / "sfx.wav"), "-i", str(a / "bed.wav"),
        "-filter_complex",
        "[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.0[v];"
        "[0:a]aresample=48000,aformat=channel_layouts=stereo[key];"
        "[1:a]aformat=channel_layouts=stereo[s];"
        "[2:a]aformat=channel_layouts=stereo[b];"
        "[s][key]sidechaincompress=threshold=0.05:ratio=4:attack=12:release=260[sd];"
        "[b][key]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=380[bd];"
        "[v][sd][bd]amix=inputs=3:duration=first:normalize=0[mx];"
        "[mx]loudnorm=I=-14:TP=-1.5:LRA=11[out]",
        "-map", "[out]", "-ar", "48000", "-ac", "2", str(out_wav),
    ], check=True)


def build(ad_dir, skip_voice=False):
    ad = (ROOT / ad_dir).resolve()
    t0 = time.time()
    print(f"\n=== {ad.name} ===")

    if not skip_voice:
        print("[1/5] voice")
        tts.build(ad / "script.json", ad / "audio")
    manifest = ad / "audio" / "manifest.json"

    print("[2/5] sound design")
    sfx.build(ad / "sfx.json", manifest, ad / "audio")

    print("[3/5] mix")
    mix(ad, ad / "audio" / "mix.wav")

    print("[4/5] frames")
    silent = ad / "out" / "_video.mp4"
    render.render(ad / "scene.html", manifest, silent)

    print("[5/5] master")
    final = ad / "out" / f"{ad.name}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(silent), "-i", str(ad / "audio" / "mix.wav"),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(final),
    ], check=True)
    silent.unlink()
    print(f"  -> {final}  ({time.time()-t0:.0f}s)")
    return final


if __name__ == "__main__":
    build(sys.argv[1], skip_voice="--skip-voice" in sys.argv)
