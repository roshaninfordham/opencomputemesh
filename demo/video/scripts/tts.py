"""Voice generation. Each line is synthesized, trimmed and measured separately so the
animation timeline can be keyed to when words are actually spoken."""
import json, sys, warnings, numpy as np, soundfile as sf
from pathlib import Path
warnings.filterwarnings("ignore")

SR = 24000
LEAD_IN = 0.35   # breath before the first word


def trim(a, thresh=0.008):
    """Strip leading/trailing near-silence so gaps are exactly what we ask for."""
    idx = np.where(np.abs(a) > thresh)[0]
    if len(idx) == 0:
        return a
    pad = int(0.02 * SR)
    return a[max(0, idx[0] - pad): min(len(a), idx[-1] + pad)]


def build(script_path, out_dir):
    from kokoro import KPipeline
    script = json.loads(Path(script_path).read_text())
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    pipe = KPipeline(lang_code="a")
    voice = script.get("voice", "am_michael")

    track = [np.zeros(int(LEAD_IN * SR), dtype=np.float32)]
    cursor = LEAD_IN
    manifest = []

    for ln in script["lines"]:
        chunks = [a for _, _, a in pipe(ln["text"], voice=voice, speed=ln.get("speed", 1.0))]
        audio = trim(np.concatenate([np.asarray(c, dtype=np.float32) for c in chunks]))
        dur = len(audio) / SR
        manifest.append({
            "id": ln["id"], "text": ln["text"],
            "start": round(cursor, 3), "end": round(cursor + dur, 3),
        })
        track.append(audio)
        cursor += dur
        gap = ln.get("gap", 0.3)
        if gap > 0:
            track.append(np.zeros(int(gap * SR), dtype=np.float32))
            cursor += gap
        print(f"  {ln['id']:>4}  {dur:5.2f}s  {ln['text'][:58]}")

    tail = script.get("tail", 0.9)          # let the CTA breathe before cutting
    track.append(np.zeros(int(tail * SR), dtype=np.float32))
    cursor += tail

    voice_wav = np.concatenate(track)
    peak = np.max(np.abs(voice_wav)) or 1.0
    voice_wav = (voice_wav / peak) * 0.85   # headroom for the mix
    sf.write(out / "voice.wav", voice_wav, SR)

    meta = {"id": script["id"], "duration": round(cursor, 3), "sr": SR, "lines": manifest}
    (out / "manifest.json").write_text(json.dumps(meta, indent=2))
    print(f"\n  total {cursor:.2f}s -> {out/'voice.wav'}")
    return meta


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
