/* Generic beat runner. Reads ad.json, builds one .scene per beat from the module
   library, and lets each beat own the stage from its own line until the next one
   starts. Modules only describe what happens inside their beat; showing, hiding
   and ordering are handled here so every ad in the set is paced identically. */
(async () => {
  const cfg = await (await fetch("ad.json")).json();
  const stage = document.getElementById("stage");
  const anchor = document.querySelector(".vignette");

  cfg.beats.forEach((b, i) => {
    const s = "b" + i;
    const el = document.createElement("div");
    el.className = "scene";
    el.id = s + "S";
    if (b.pad) el.style.padding = `0 ${b.pad}`;
    el.innerHTML = Modules[b.type].html(b, s);
    stage.insertBefore(el, anchor);
  });

  Scene.boot("audio/manifest.json", (tl, E) => {
    cfg.beats.forEach((b, i) => {
      const s = "b" + i, sel = "#" + s + "S";
      gsap.set(sel, { opacity: 0 });
      const T = E.at(b.at);
      tl.to(sel, { opacity: 1, duration: .34, ease: "power2.out" }, T - .06);
      const next = cfg.beats[i + 1];
      if (next) tl.to(sel, { opacity: 0, duration: .34, ease: "power2.in" }, E.at(next.at) - .34);
      Modules[b.type].anim(b, s, tl, E, T);
    });
  });
})();
