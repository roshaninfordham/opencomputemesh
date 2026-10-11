"""Generate each ad's script.md from its script, beats and measured voice timings,
so the docs can never drift from what actually got rendered."""
import json, sys
from pathlib import Path

META = {
 "04-non-technical": ("Non-Technical Founder", "The founder with vision and capital but no engineers.",
   "Names the audience in the first two seconds, then attacks the cost of hiring rather than the founder."),
 "05-voice-agents": ("Voice Agents", "Anyone who wants voice/AI agents built — for themselves or their clients.",
   "Concedes the tools are good, which is what makes the real objection (bandwidth, expertise) land."),
 "06-agencies": ("Agencies & White-Label", "Agencies losing work because they can't deliver AI.",
   "Built on one turn: 'you keep saying no' → 'what if you could say yes'."),
 "07-solo-founder": ("Solo Founder", "One person wearing every hat.",
   "Lists the hats they already wear, then shows engineering as the one they shouldn't."),
 "08-multi-exit": ("Multi-Exit Founder", "Someone who has built and sold before.",
   "Assumes expertise rather than explaining. They already know what a team costs."),
 "09-family-office": ("Family Office", "Investors whose portfolio companies all need engineering.",
   "Reframes from one team to per-company capacity with no headcount."),
 "10-nonprofit": ("Mission-Driven", "Non-profits and mission-led organisations.",
   "Never implies the budget is the problem — the market's pricing is."),
 "11-restaurants": ("Restaurants & Hospitality", "Operators paying a cut to four platforms.",
   "The pain is concrete and daily: four apps, four commissions, no ownership."),
 "12-logistics": ("Logistics & Operations", "Operations run on spreadsheets and phone calls.",
   "'It works. Until it doesn't.' does the whole job in five words."),
 "13-ai-era": ("The AI Era", "The broad-net ad — any funded, ambitious, non-technical operator.",
   "Widest reach, so the natural one to put the most spend behind."),
}
DESC = {
 "question":"Hook — the audience named, with a blinking cursor",
 "statement":"Kinetic type",
 "rows":"A list revealing one line at a time, the missing one held bright",
 "crossout":"Costs appearing, then struck through",
 "counter":"A figure climbing",
 "price":"$10,000 · per month · flat",
 "chips":"Capability chips tiling in",
 "grid":"A grid of boxes lighting up",
 "chart":"Workload spiking while the price line stays flat",
 "converge":"Many channels collapsing to a single point of light",
 "window":"A slit of light opening, then narrowing",
 "seats":"Two seats filled, one open and pulsing",
 "cta":"Closing card — the URL",
}

for name in sys.argv[1:]:
    d = Path(name)
    sc = json.loads((d/"script.json").read_text())
    beats = json.loads((d/"ad.json").read_text())["beats"]
    man = json.loads((d/"audio/manifest.json").read_text())
    at = {b["at"]: b for b in beats}
    title, aud, note = META[name]
    rows = []
    for l in man["lines"]:
        b = at.get(l["id"])
        vis = DESC.get(b["type"], b["type"]) if b else "↳ continues"
        rows.append(f"| {l['start']:.2f} | {l['text']} | {vis} |")
    (d/"script.md").write_text(
f"""# {name} — "{title}"

**{man['duration']:.1f}s · 1080×1920 · {aud}**

{note}

| Time | Voice | On screen |
|------|-------|-----------|
""" + "\n".join(rows) + f"""

Beats: {' → '.join(b['type'] for b in beats)}
""")
    print(f"  {name:20} {man['duration']:.1f}s")
