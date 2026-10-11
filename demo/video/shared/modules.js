/* Beat modules. Each has html() for its markup and anim() for its timeline.
   The generic runner in scene.js handles showing and hiding, so a module only
   describes what happens inside its own beat. Anything computed rather than
   tweened goes through E.onFrame so it stays a pure function of t. */
window.Modules = (function () {
  const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");
  const clamp01 = x => Math.max(0, Math.min(1, x));
  const easeOut = x => 1 - Math.pow(1 - x, 3);
  const money = v => "$" + Math.round(v).toLocaleString("en-US");

  function fit(cv) {
    const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    cv.width = Math.round(r.width * d); cv.height = Math.round(r.height * d);
    return cv.getContext("2d");
  }
  /* words rise into place out of a mask */
  const riseWords = (tl, E, sel, t, stag = .038) => {
    const w = E.words(sel); gsap.set(w, { yPercent: 110 });
    tl.to(w, { yPercent: 0, duration: .66, ease: "power3.out", stagger: stag }, t);
  };
  const riseChars = (tl, E, sel, t, stag = .028) => {
    const c = E.chars(sel); gsap.set(c, { yPercent: 112 });
    tl.to(c, { yPercent: 0, duration: .62, ease: "power3.out", stagger: stag }, t);
  };
  const fadeUp = (tl, sel, t, y = 16, d = .5) =>
    tl.fromTo(sel, { opacity: 0, y }, { opacity: 1, y: 0, duration: d, ease: "power3.out" }, t);

  return {
  /* ---------- hook ---------- */
  question: {
    html: (b, s) => `<div class="q-head" id="${s}h">${b.head}<i class="q-cursor" id="${s}c"></i></div>`,
    anim(b, s, tl, E, T) {
      riseChars(tl, E, `#${s}h`, T + .02, .026);
      E.onFrame(t => {
        const el = document.getElementById(s + "c");
        el.style.opacity = (Math.floor(t * 1.6) % 2 === 0) ? 1 : 0.12;
      });
    }
  },
  /* ---------- statement ---------- */
  statement: {
    html: (b, s) => `${b.kicker ? `<div class="st-kicker" id="${s}k">${esc(b.kicker)}</div>` : ""}
      <div class="st-head" id="${s}h">${b.head}</div>
      ${b.sub ? `<div class="st-sub" id="${s}s">${b.sub}</div>` : ""}`,
    anim(b, s, tl, E, T) {
      if (b.kicker) tl.fromTo(`#${s}k`, { opacity: 0 }, { opacity: 1, duration: .45 }, T);
      riseWords(tl, E, `#${s}h`, T + (b.kicker ? .30 : .04));
      if (b.sub) riseWords(tl, E, `#${s}s`, T + (b.subAt ?? 1.05), .032);
    }
  },
  /* ---------- rows, optionally with one missing ---------- */
  rows: {
    html: (b, s) => `<div class="rows">${b.items.map((it, i) =>
        `<div class="rw${b.missing === i ? " miss" : ""}" id="${s}r${i}">
           <span class="rw-l">${esc(it)}</span><i class="rw-m"></i></div>`).join("")}</div>`,
    anim(b, s, tl, E, T) {
      const step = b.step ?? .58;
      b.items.forEach((_, i) => {
        const at = T + (b.lead ?? .34) + i * step;
        tl.to(`#${s}r${i}`, { opacity: 1, duration: .01 }, at);
        riseChars(tl, E, `#${s}r${i} .rw-l`, at, .022);
        tl.fromTo(`#${s}r${i} .rw-m`, { scaleX: 0 }, { scaleX: 1, duration: .38, ease: "power2.out" }, at + .16);
      });
      if (b.missing != null) {            // dim what they have, spotlight what they don't
        const at = T + (b.lead ?? .34) + b.missing * step;
        b.items.forEach((_, i) => { if (i !== b.missing)
          tl.to(`#${s}r${i} .rw-l`, { color: "#6a6a6a", duration: .5 }, at - .1); });
        E.onFrame(t => {
          const el = document.querySelector(`#${s}r${b.missing} .rw-m`);
          if (el) el.style.opacity = (0.45 + 0.55 * (0.5 + 0.5 * Math.sin(t * 3.4))).toFixed(3);
        });
      }
    }
  },
  /* ---------- costs struck through ---------- */
  crossout: {
    html: (b, s) => `<div class="xo">${b.items.map((it, i) =>
        `<div class="xo-i" id="${s}x${i}">${esc(it)}<i></i></div>`).join("")}</div>`,
    anim(b, s, tl, E, T) {
      const step = b.step ?? .46;
      b.items.forEach((_, i) => {
        const at = T + (b.lead ?? .25) + i * step;
        tl.fromTo(`#${s}x${i}`, { opacity: 0, x: -14 }, { opacity: 1, x: 0, duration: .34 }, at);
        tl.fromTo(`#${s}x${i} i`, { scaleX: 0 }, { scaleX: 1, duration: .34, ease: "power2.out" }, at + .22);
        tl.to(`#${s}x${i}`, { color: "#8d8d8d", duration: .34 }, at + .30);
      });
    }
  },
  /* ---------- climbing figure, optionally snapping to the price ---------- */
  counter: {
    html: (b, s) => `<div class="ct-num" id="${s}n">$0</div>
      ${b.label ? `<div class="ct-lab" id="${s}l">${esc(b.label)}</div>` : ""}`,
    anim(b, s, tl, E, T) {
      const dur = b.climb ?? 2.6, to = b.to ?? 1460000;
      const snapAt = b.snap != null ? E.at(b.snap) + (b.snapOff ?? 1.1) : null;
      tl.fromTo(`#${s}n`, { opacity: 0 }, { opacity: 1, duration: .3 }, T);
      if (snapAt != null) {
        tl.fromTo(`#${s}n`, { scale: 1.14 }, { scale: 1, duration: .5, ease: "power4.out" }, snapAt);
        tl.to("#aura", { opacity: .9, duration: .1 }, snapAt)
          .to("#aura", { opacity: .3, duration: 1.1, ease: "power2.out" }, snapAt + .1);
        if (b.label) tl.fromTo(`#${s}l`, { opacity: 0 }, { opacity: 1, duration: .4 }, snapAt + .34);
      } else if (b.label) {
        tl.fromTo(`#${s}l`, { opacity: 0 }, { opacity: 1, duration: .4 }, T + dur * .7);
      }
      tl.to("#aura", { opacity: .45, duration: 1.4 }, T);
      const el = () => document.getElementById(s + "n");
      E.onFrame(t => {
        const n = el(); if (!n) return;
        if (snapAt != null && t >= snapAt) { n.textContent = b.snapTo ?? "$10,000"; return; }
        const p = clamp01((t - T) / dur);
        n.textContent = money(easeOut(p) * to);
      });
    }
  },
  /* ---------- price card ---------- */
  price: {
    html: (b, s) => `<div class="price" id="${s}p">${esc(b.value ?? "$10,000")}</div>
      <div class="pricecap" id="${s}c">${esc(b.cap ?? "per month · flat")}</div>`,
    anim(b, s, tl, E, T) {
      tl.fromTo(`#${s}p`, { opacity: 0, scale: 1.06 }, { opacity: 1, scale: 1, duration: .6, ease: "power4.out" }, T + .06);
      fadeUp(tl, `#${s}c`, T + .85, 12);
      tl.to("#aura", { opacity: .34, duration: .8 }, T + .1).to("#aura", { opacity: .14, duration: 1.2 }, T + 1.2);
    }
  },
  /* ---------- capability chips ---------- */
  chips: {
    html: (b, s) => `${b.head ? `<div class="headline" id="${s}h">${b.head}</div>` : ""}
      <div class="chips" id="${s}g" style="margin-top:${b.head ? "5rem" : "0"}">
      ${b.items.map((c, i) => `<span class="chip" id="${s}c${i}">${esc(c)}</span>`).join("")}</div>`,
    anim(b, s, tl, E, T) {
      if (b.head) riseWords(tl, E, `#${s}h`, T + .04);
      const lead = b.head ? .75 : .18, step = b.step ?? .105;
      b.items.forEach((_, i) =>
        tl.fromTo(`#${s}c${i}`, { opacity: 0, y: 16 },
          { opacity: 1, y: 0, duration: .32, ease: "power2.out" }, T + lead + i * step));
    }
  },
  /* ---------- grid of boxes lighting up ---------- */
  grid: {
    html: (b, s) => `<div class="gr" id="${s}g"
        style="grid-template-columns:repeat(${b.cols ?? 3},1fr);width:${b.width ?? "76rem"}">
      ${b.items.map((c, i) => `<div class="gr-c" id="${s}c${i}">${esc(c)}</div>`).join("")}</div>
      ${b.cap ? `<div class="gr-cap" id="${s}p">${b.cap}</div>` : ""}`,
    anim(b, s, tl, E, T) {
      const step = b.step ?? .17;
      b.items.forEach((_, i) =>
        tl.fromTo(`#${s}c${i}`, { opacity: 0, scale: .9 },
          { opacity: 1, scale: 1, duration: .34, ease: "back.out(1.6)" }, T + .22 + i * step));
      if (b.litAt != null)                       // everything switches on at once
        b.items.forEach((_, i) => tl.to(`#${s}c${i}`, { duration: .01,
          onComplete(){ document.getElementById(s+"c"+i)?.classList.add("lit"); },
          onReverseComplete(){ document.getElementById(s+"c"+i)?.classList.remove("lit"); }
        }, E.at(b.litAt) + .1 + i * .05));
      if (b.cap) fadeUp(tl, `#${s}p`, T + .22 + b.items.length * step + .2, 18);
    }
  },
  /* ---------- workload spikes, price stays flat ---------- */
  chart: {
    html: (b, s) => `<div class="ch-head" id="${s}h">${b.head}</div>
      <div class="ch-wrap"><canvas class="paint" id="${s}v"></canvas></div>
      <div class="ch-key">
        <span id="${s}k1" style="color:var(--dim)"><i style="background:#6a6a6a"></i>${esc(b.keyA ?? "Your workload")}</span>
        <span id="${s}k2" style="color:var(--bright)"><i style="background:#f0f0f0"></i>${esc(b.keyB ?? "Your price")}</span>
      </div>`,
    anim(b, s, tl, E, T) {
      riseWords(tl, E, `#${s}h`, T + .02, .034);
      tl.fromTo(`#${s}k1`, { opacity: 0 }, { opacity: 1, duration: .4 }, T + 1.5);
      tl.fromTo(`#${s}k2`, { opacity: 0 }, { opacity: 1, duration: .4 }, T + 1.9);
      const BARS = [.34,.52,.28,.71,.44,.93,.61,.38,.86,.55,.74,.41];
      let ctx = null;
      E.onFrame(t => {
        const cv = document.getElementById(s + "v"); if (!cv) return;
        if (!ctx) ctx = fit(cv);
        const w = ctx.canvas.width, h = ctx.canvas.height, D = window.devicePixelRatio || 1;
        ctx.clearRect(0, 0, w, h);
        const p = clamp01((t - (T + .4)) / 1.5); if (p <= 0) return;
        const n = BARS.length, gap = w * .018, bw = (w - gap * (n - 1)) / n;
        for (let i = 0; i < n; i++) {
          const bp = easeOut(clamp01((p - i * .035) / .55)), bh = BARS[i] * h * .92 * bp;
          ctx.fillStyle = "rgba(150,150,150,0.27)";
          ctx.fillRect(i * (bw + gap), h - bh, bw, bh);
        }
        const lp = easeOut(clamp01((p - .30) / .60)), y = h - h * .46;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w * lp, y);
        ctx.strokeStyle = "rgba(240,240,240,0.95)"; ctx.lineWidth = 3 * D; ctx.stroke();
      });
    }
  },
  /* ---------- many channels collapsing to one ---------- */
  converge: {
    html: (b, s) => `<canvas class="paint" id="${s}v"></canvas>
      <div class="st-head" id="${s}h" style="margin-top:46rem">${b.head}</div>
      ${b.sub ? `<div class="st-kicker" id="${s}s" style="margin:2.6rem 0 0">${esc(b.sub)}</div>` : ""}`,
    anim(b, s, tl, E, T) {
      riseWords(tl, E, `#${s}h`, T + .35);
      if (b.sub) tl.fromTo(`#${s}s`, { opacity: 0 }, { opacity: 1, duration: .5 }, T + 1.35);
      let ctx = null;
      E.onFrame(t => {
        const cv = document.getElementById(s + "v"); if (!cv) return;
        if (!ctx) ctx = fit(cv);
        const w = ctx.canvas.width, h = ctx.canvas.height, D = window.devicePixelRatio || 1;
        ctx.clearRect(0, 0, w, h);
        const p = clamp01((t - (T - .05)) / 1.5); if (p <= 0) return;
        const cx = w / 2, cy = h * .30, R = Math.min(w, h) * .46, N = 16;
        for (let i = 0; i < N; i++) {
          const a = (i / N) * Math.PI * 2 + .35;
          const lp = easeOut(clamp01((p - (i % 5) * .045) / .62));
          const x0 = cx + Math.cos(a) * R, y0 = cy + Math.sin(a) * R;
          ctx.beginPath(); ctx.moveTo(x0, y0);
          ctx.lineTo(x0 + (cx - x0) * lp, y0 + (cy - y0) * lp);
          ctx.strokeStyle = `rgba(240,240,240,${(0.05 + 0.17 * lp).toFixed(3)})`;
          ctx.lineWidth = 1 * D; ctx.stroke();
        }
        const g = easeOut(clamp01((p - .55) / .45));
        ctx.beginPath(); ctx.arc(cx, cy, (4 + 5 * g) * D, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(240,240,240,${(0.35 + 0.65 * g).toFixed(3)})`; ctx.fill();
      });
    }
  },
  /* ---------- the window of opportunity ---------- */
  window: {
    html: (b, s) => `<div class="win-wrap"><div class="win" id="${s}w"></div>
        <div class="win-core" id="${s}c"></div></div>
      <div class="win-lab" id="${s}h">${b.head}</div>`,
    anim(b, s, tl, E, T) {
      riseWords(tl, E, `#${s}h`, T + .20);
      tl.to("#aura", { opacity: .40, duration: .8 }, T + .1).to("#aura", { opacity: .12, duration: 1.8 }, T + 1.6);
      const closeAt = b.closeAt ? E.at(b.closeAt) : T + 2.2;
      E.onFrame(t => {
        const w = document.getElementById(s + "w"), c = document.getElementById(s + "c");
        if (!w) return;
        const open = easeOut(clamp01((t - (T - .10)) / .85));
        const close = clamp01((t - closeAt) / 2.2);
        w.style.height = ((1.2 + 8.6 * open) * (1 - .86 * easeOut(close))).toFixed(2) + "rem";
        w.style.opacity = (0.5 + 0.5 * open).toFixed(3);
        c.style.opacity = (0.35 + 0.65 * open * (1 - .35 * easeOut(close))).toFixed(3);
      });
    }
  },
  /* ---------- the filter ---------- */
  seats: {
    html: (b, s) => `<div class="seats">
        <div class="seat taken" id="${s}a"></div><div class="seat taken" id="${s}b"></div>
        <div class="seat" id="${s}c"></div></div>
      <div class="seatcap" id="${s}h">${esc(b.head ?? "Three seats a quarter.")}</div>
      ${b.sub ? `<div class="seatcap2" id="${s}s">${esc(b.sub)}</div>` : ""}`,
    anim(b, s, tl, E, T) {
      [["a", .06], ["b", .24], ["c", .42]].forEach(([k, o]) =>
        tl.fromTo(`#${s}${k}`, { opacity: 0, scale: .82 },
          { opacity: 1, scale: 1, duration: .4, ease: "back.out(2)" }, T + o));
      fadeUp(tl, `#${s}h`, T + .70, 18);
      if (b.sub) tl.fromTo(`#${s}s`, { opacity: 0 }, { opacity: 1, duration: .44 }, T + 1.18);
      E.onFrame(t => {
        const el = document.getElementById(s + "c"); if (!el || t < T) return;
        const pulse = .5 + .5 * Math.sin(t * 3.4);
        el.style.borderColor = `rgba(240,240,240,${(0.35 + 0.5 * pulse).toFixed(3)})`;
      });
    }
  },
  /* ---------- close ---------- */
  cta: {
    html: (b, s) => `<div class="bar" id="${s}b1"></div>
      <div class="cta" id="${s}h">${b.head ?? "Book your<br>free call."}</div>
      <div class="bar" id="${s}b2"></div>
      <div class="url" id="${s}u">onlyforambitiousfounders.com</div>
      <div class="note" id="${s}n">45 minutes · no cost · no commitment</div>
      <div class="mark" id="${s}m">Only for Ambitious Founders</div>`,
    anim(b, s, tl, E, T) {
      tl.fromTo(`#${s}b1, #${s}b2`, { scaleX: 0 }, { scaleX: 1, duration: .7, ease: "power3.out" }, T + .02);
      riseChars(tl, E, `#${s}h`, T + .12, .026);
      fadeUp(tl, `#${s}u`, T + 1.25, 14, .55);
      tl.fromTo(`#${s}n`, { opacity: 0 }, { opacity: 1, duration: .5 }, T + 1.9);
      tl.fromTo(`#${s}m`, { opacity: 0 }, { opacity: 1, duration: .6 }, T + 2.2);
      tl.to("#aura", { opacity: .26, duration: 1.4, ease: "power1.out" }, T + .1);
    }
  },
  };
})();
