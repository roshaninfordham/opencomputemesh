"""Procedural sound design. Every element is synthesized from scratch (no stock samples,
so nothing to license) then run through real studio effects to give it weight."""
import json, sys, numpy as np, soundfile as sf
from pathlib import Path
from pedalboard import Pedalboard, Reverb, Compressor, HighpassFilter, LowpassFilter, Gain

SR = 48000
rng = np.random.default_rng(7)          # fixed seed -> identical sound every build


def env(n, a=0.01, d=0.3, curve=2.5):
    """Percussive attack/decay envelope."""
    at = max(1, int(a * SR))
    e = np.ones(n, dtype=np.float32)
    e[:at] = np.linspace(0, 1, at)
    tail = np.linspace(0, 1, n - at)
    e[at:] = (1 - tail) ** curve
    return e


def noise(n):
    return rng.standard_normal(n).astype(np.float32)


def tick(gain=1.0):
    n = int(0.05 * SR)
    t = np.arange(n) / SR
    s = noise(n) * 0.5 + np.sin(2 * np.pi * 2400 * t) * 0.5
    return s * env(n, 0.0006, 0.05, 5.0) * 0.16 * gain


def whoosh(dur=0.55, gain=1.0):
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    s = noise(n)
    # sweep a resonant band upward so it reads as motion, not hiss
    sweep = np.sin(2 * np.pi * (300 + 2600 * t) * np.arange(n) / SR) * 0.35
    e = np.sin(np.pi * t) ** 1.8
    return (s * 0.55 + sweep) * e * 0.13 * gain


def riser(dur=2.6, gain=1.0):
    """Tension under the climbing cost figure."""
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    ph = np.cumsum(2 * np.pi * (110 + 300 * t ** 2.2) / SR)
    tone = np.sin(ph) * 0.30 + np.sin(ph * 1.5) * 0.13
    hiss = noise(n) * (0.05 + 0.16 * t ** 2)
    return (tone + hiss) * (t ** 1.7) * 0.30 * gain


def impact(gain=1.0):
    """The snap to $10,000 — a pitch-dropping sub with a transient on top."""
    n = int(1.5 * SR)
    t = np.arange(n) / SR
    f = 105 * np.exp(-t * 7.5) + 32
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.001, 1.5, 3.2)
    click = noise(int(0.02 * SR)) * env(int(0.02 * SR), 0.0004, 0.02, 6) * 0.35
    out = body * 0.62
    out[:len(click)] += click
    return out * gain


def swell(dur=1.6, gain=1.0):
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    ph = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * f * ph) * a for f, a in
            [(220, .30), (330, .18), (440, .12), (660, .06)])
    return s * (np.sin(np.pi * t) ** 2) * 0.16 * gain


def shimmer(dur=2.2, gain=1.0):
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    ph = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * f * ph) * a for f, a in
            [(1320, .18), (1760, .12), (2640, .07)])
    return s * ((1 - t) ** 2.0) * 0.10 * gain


def bed(duration):
    """A quiet sub-drone that holds the whole ad together without becoming music."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    d = (np.sin(2 * np.pi * 55 * t) * 0.34
         + np.sin(2 * np.pi * 82.5 * t) * 0.16
         + np.sin(2 * np.pi * 110 * t) * 0.08)
    breathe = 0.72 + 0.28 * np.sin(2 * np.pi * t / 7.5)
    pulse = 1 + 0.16 * (np.sin(2 * np.pi * t / 2.0) ** 8)   # slow heartbeat
    fade = np.clip(np.minimum(t / 1.5, (duration - t) / 1.4), 0, 1)
    return (d * breathe * pulse * fade * 0.085).astype(np.float32)


MAKERS = {"tick": tick, "whoosh": whoosh, "riser": riser,
          "impact": impact, "swell": swell, "shimmer": shimmer}


def build(spec_path, manifest_path, out_dir):
    spec = json.loads(Path(spec_path).read_text())
    meta = json.loads(Path(manifest_path).read_text())
    out = Path(out_dir)
    dur = meta["duration"]
    n = int(dur * SR) + SR
    track = np.zeros(n, dtype=np.float32)

    def line_start(i):
        return next(l["start"] for l in meta["lines"] if l["id"] == i)

    for e in spec["events"]:
        t0 = (line_start(e["at"]) + e.get("off", 0.0)) if "at" in e else e["t"]
        kwargs = {k: v for k, v in e.items() if k in ("dur", "gain")}
        s = MAKERS[e["type"]](**kwargs)
        i = int(t0 * SR)
        seg = s[:max(0, n - i)]
        track[i:i + len(seg)] += seg

    board = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=28),
        Compressor(threshold_db=-19, ratio=2.6, attack_ms=4, release_ms=140),
        Reverb(room_size=0.30, damping=0.62, wet_level=0.10, dry_level=0.92, width=0.85),
        LowpassFilter(cutoff_frequency_hz=15500),
        Gain(gain_db=-1.0),
    ])
    track = board(track, SR)

    peak = float(np.max(np.abs(track))) or 1.0
    if peak > 0.92:
        track *= 0.92 / peak

    sf.write(out / "sfx.wav", track, SR)
    sf.write(out / "bed.wav", bed(dur), SR)
    print(f"  sfx: {len(spec['events'])} events, bed {dur:.2f}s")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], sys.argv[3])
