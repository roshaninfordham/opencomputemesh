"""Derive sound events from an ad's beat config.

Hand-writing sfx.json for ten ads would drift the moment a script changed. Because
every beat already knows which spoken line it starts on, the sound can be inferred:
transitions get a whoosh, list items get ticks, the price lands on an impact, the
close gets a swell and a shimmer.
"""
import json, sys
from pathlib import Path


def build(ad_dir):
    ad = Path(ad_dir)
    beats = json.loads((ad / "ad.json").read_text())["beats"]
    ev = [{"at": beats[0]["at"], "off": -0.25, "type": "swell", "dur": 2.0, "gain": 0.6}]

    for i, b in enumerate(beats):
        at, t = b["at"], b["type"]
        if i:                                   # every scene change gets a transition
            ev.append({"at": at, "off": -0.32, "type": "whoosh", "dur": 0.55, "gain": 0.8})

        if t in ("rows", "crossout", "chips", "grid"):
            n = len(b["items"])
            lead = {"rows": 0.34, "crossout": 0.25, "chips": 0.75 if b.get("head") else 0.18,
                    "grid": 0.22}[t]
            step = b.get("step", {"rows": 0.58, "crossout": 0.46, "chips": 0.105, "grid": 0.17}[t])
            gain = 0.6 if t in ("chips",) else 0.85
            ev += [{"at": at, "off": round(lead + k * step, 3), "type": "tick", "gain": gain}
                   for k in range(n)]

        elif t == "counter":
            climb = b.get("climb", 2.6)
            ev.append({"at": at, "off": 0.0, "type": "riser",
                       "dur": round(climb + 1.2, 2), "gain": 0.85})
            if b.get("snap"):
                ev.append({"at": b["snap"], "off": b.get("snapOff", 1.1), "type": "impact", "gain": 1.0})
                ev.append({"at": b["snap"], "off": b.get("snapOff", 1.1) + 0.34,
                           "type": "swell", "dur": 1.8, "gain": 0.75})

        elif t == "price":
            ev.append({"at": at, "off": 0.06, "type": "impact", "gain": 0.85})
            ev.append({"at": at, "off": 0.42, "type": "swell", "dur": 1.6, "gain": 0.7})

        elif t == "window":
            ev.append({"at": at, "off": -0.05, "type": "impact", "gain": 0.65})
            ev.append({"at": at, "off": 0.60, "type": "riser", "dur": 2.0, "gain": 0.5})

        elif t == "seats":
            ev += [{"at": at, "off": o, "type": "tick", "gain": g}
                   for o, g in ((0.06, 0.9), (0.24, 0.9), (0.42, 1.2))]

        elif t == "cta":
            ev.append({"at": at, "off": 0.10, "type": "swell", "dur": 2.0, "gain": 0.9})
            ev.append({"at": at, "off": 1.25, "type": "shimmer", "dur": 2.4, "gain": 0.9})

        elif t == "converge":
            ev.append({"at": at, "off": 0.90, "type": "impact", "gain": 0.55})

    ev.sort(key=lambda e: (e["at"], e["off"]))
    (ad / "sfx.json").write_text(json.dumps({"events": ev}, indent=2))
    return len(ev)


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(f"  {d:20} {build(d)} events")
