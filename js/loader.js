/* Loading screen animation:
   1. the black line art draws itself outward from one point,
   2. colour is painted on in random brush strokes,
   3. the screen lifts away.
   If anything fails, the simple CSS fade in style.css is used instead. */
(function () {
  const loader = document.getElementById("loader");
  const colorImg = loader && loader.querySelector(".loader__color");
  const canvas = loader && loader.querySelector(".loader__canvas");
  if (!loader || !canvas || !window.LOADER_ART) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const S = 640;
  const DRAW_END = 1800, PAINT_START = 1850, PAINT_END = 3700, FILL_END = 4000;

  loader.classList.add("js-anim");
  const fail = () => loader.classList.remove("js-anim");

  const art = new Image();
  art.src = window.LOADER_ART;
  Promise.all([art.decode(), colorImg.decode()]).then(start).catch(fail);

  function start() {
    const ctx = canvas.getContext("2d");
    const mk = () => { const c = document.createElement("canvas"); c.width = c.height = S; return c; };
    const lineC = mk(), lctx = lineC.getContext("2d");
    const maskC = mk(), mctx = maskC.getContext("2d");
    const colC = mk(), cctx = colC.getContext("2d");

    // read drawing order + line strength
    lctx.drawImage(art, 0, 0, S, S);
    let src;
    try { src = lctx.getImageData(0, 0, S, S).data; } catch (e) { return fail(); }
    const idx = [], order = [], strength = [];
    for (let i = 0; i < S * S; i++) {
      const a = src[i * 4 + 3];
      if (a > 6) { idx.push(i); order.push(src[i * 4] / 255); strength.push(a); }
    }
    const out = lctx.createImageData(S, S);
    const px = out.data; // black lines, alpha set per frame

    // random paint blobs
    const blobs = [];
    for (let i = 0; i < 170; i++) {
      const ang = Math.random() * Math.PI * 2, rad = Math.sqrt(Math.random()) * 300;
      const pts = [];
      for (let k = 0; k < 16; k++) pts.push(0.75 + Math.random() * 0.45);
      blobs.push({
        x: S / 2 + Math.cos(ang) * rad, y: S / 2 + Math.sin(ang) * rad,
        r: 22 + Math.random() * 46, rot: Math.random() * 6.28, pts,
        t0: PAINT_START + Math.random() * (PAINT_END - PAINT_START - 500)
      });
    }

    const t0 = performance.now();
    let lineDone = false;
    function frame(now) {
      const t = now - t0;

      // 1. line drawing
      if (!lineDone) {
        const p = Math.min(1.1, t / DRAW_END * 1.1);
        for (let j = 0; j < idx.length; j++) {
          const v = Math.max(0, Math.min(1, (p - order[j]) * 14));
          px[idx[j] * 4 + 3] = strength[j] * v;
        }
        lctx.putImageData(out, 0, 0);
        if (p >= 1.1) lineDone = true;
      }

      // 2. paint mask
      mctx.clearRect(0, 0, S, S);
      if (t > PAINT_START) {
        mctx.fillStyle = "#000";
        for (const b of blobs) {
          const g = (t - b.t0) / 500;
          if (g <= 0) continue;
          const r = b.r * (1 - Math.pow(1 - Math.min(1, g), 3));
          mctx.beginPath();
          b.pts.forEach((m, k) => {
            const a = b.rot + (k / b.pts.length) * Math.PI * 2;
            const x = b.x + Math.cos(a) * r * m, y = b.y + Math.sin(a) * r * m;
            k ? mctx.lineTo(x, y) : mctx.moveTo(x, y);
          });
          mctx.closePath();
          mctx.fill();
        }
        if (t > PAINT_END) { // fill any gaps
          mctx.globalAlpha = Math.min(1, (t - PAINT_END) / (FILL_END - PAINT_END));
          mctx.fillRect(0, 0, S, S);
          mctx.globalAlpha = 1;
        }
      }
      cctx.globalCompositeOperation = "source-over";
      cctx.clearRect(0, 0, S, S);
      cctx.drawImage(colorImg, 0, 0, S, S);
      cctx.globalCompositeOperation = "destination-in";
      cctx.drawImage(maskC, 0, 0);

      // compose
      ctx.clearRect(0, 0, S, S);
      ctx.globalAlpha = t > PAINT_END ? Math.max(0, 1 - (t - PAINT_END) / (FILL_END - PAINT_END)) : 1;
      ctx.drawImage(lineC, 0, 0);
      ctx.globalAlpha = 1;
      ctx.drawImage(colC, 0, 0);

      if (t < FILL_END + 50 && !loader.hidden) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
})();
