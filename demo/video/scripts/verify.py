"""Check finished masters against platform spec.

A build exiting 0 says nothing about whether the file plays everywhere, so this
reads the actual container and measures real loudness.
"""
import json, subprocess, sys
from pathlib import Path

OK, BAD = "ok", "FAIL"

def probe(mp4):
    out = subprocess.run(["ffprobe","-v","error","-show_entries",
        "stream=codec_name,width,height,pix_fmt,sample_rate,channels",
        "-show_entries","format=duration","-of","json",str(mp4)],
        capture_output=True, text=True).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if "width" in s)
    a = next((s for s in d["streams"] if "sample_rate" in s), {})
    return v, a, float(d["format"]["duration"])

def loudness(mp4):
    err = subprocess.run(["ffmpeg","-hide_banner","-nostats","-i",str(mp4),
        "-af","ebur128=peak=true","-f","null","-"], capture_output=True, text=True).stderr
    def grab(tag):
        for ln in err.splitlines():
            if ln.strip().startswith(tag):
                return float(ln.split(":")[1].strip().split()[0])
        return None
    return grab("I:"), grab("Peak:")

def main(paths):
    bad = 0
    print(f"{'ad':22} {'dur':>6}  {'video':>22}  {'audio':>14}  {'LUFS':>7} {'peak':>7}  status")
    for p in paths:
        mp4 = Path(p)
        v, a, dur = probe(mp4)
        I, pk = loudness(mp4)
        checks = [
            v["codec_name"] == "h264", v["pix_fmt"] == "yuv420p",
            v["width"] == 1080, v["height"] == 1920,
            a.get("codec_name") == "aac", int(a.get("sample_rate", 0)) == 48000,
            dur <= 30.0, I is not None and -16 <= I <= -13, pk is not None and pk <= -1.0,
        ]
        status = OK if all(checks) else BAD
        bad += status == BAD
        print(f"{mp4.stem:22} {dur:6.2f}  {v['codec_name']:>5} {v['width']}x{v['height']} {v['pix_fmt']:>9}  "
              f"{a.get('codec_name','-'):>4} {a.get('sample_rate','-'):>6}Hz  {I:7.1f} {pk:7.1f}  {status}")
    print(f"\n{len(paths) - bad}/{len(paths)} passed")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
