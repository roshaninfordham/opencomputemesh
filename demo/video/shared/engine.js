/* Deterministic render engine. Nothing animates on a clock — every visual state is a
   pure function of time t, so Playwright can seek to any frame and get identical output. */
window.Scene = (function () {
  let tl = null, manifest = null, grainTiles = [], grainEl = null;
  const hooks = [];                       // extra per-frame painters (canvas / three.js)

  /* Pre-baked noise tiles. Cycling them by frame index gives moving film grain
     that is still fully deterministic. */
  function makeGrain(n = 10, size = 180) {
    for (let k = 0; k < n; k++) {
      const c = document.createElement("canvas");
      c.width = c.height = size;
      const ctx = c.getContext("2d"), img = ctx.createImageData(size, size);
      let seed = (k + 1) * 9301;
      const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
      for (let i = 0; i < img.data.length; i += 4) {
        const v = 110 + rnd() * 145;
        img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
        img.data[i + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      grainTiles.push(c.toDataURL());
    }
  }

  return {
    /* time of a spoken line, from the voice manifest */
    at(id, off = 0) {
      const l = manifest.lines.find(x => x.id === id);
      if (!l) throw new Error("no line " + id);
      return l.start + off;
    },
    end(id, off = 0) {
      const l = manifest.lines.find(x => x.id === id);
      return l.end + off;
    },
    dur(id) { const l = manifest.lines.find(x => x.id === id); return l.end - l.start; },
    manifest() { return manifest; },
    onFrame(fn) { hooks.push(fn); },

    /* split an element into per-character spans for staggered reveals */
    chars(el) {
      return new SplitText(el, { type: "chars", mask: "chars" }).chars;
    },
    words(el) {
      return new SplitText(el, { type: "words", mask: "words" }).words;
    },

    async boot(manifestUrl, buildFn) {
      manifest = await (await fetch(manifestUrl)).json();
      await document.fonts.ready;
      await new Promise(r => setTimeout(r, 60));   // let variable-font metrics settle

      makeGrain();
      grainEl = document.querySelector(".grain");

      gsap.ticker.remove(gsap.updateRoot);          // never advance on wall clock
      tl = gsap.timeline({ paused: true });
      buildFn(tl, this);
      tl.pause(0);

      window.__ready = true;
      window.__duration = manifest.duration;
    },

    /* the only thing Playwright calls */
    seek(t) {
      tl.time(t, false);
      if (grainEl) {
        const f = Math.floor(t * 24);
        grainEl.style.backgroundImage = `url(${grainTiles[f % grainTiles.length]})`;
        grainEl.style.backgroundPosition = `${(f * 37) % 180}px ${(f * 53) % 180}px`;
      }
      for (const fn of hooks) fn(t);
    },
  };
})();
window.seek = t => window.Scene.seek(t);
