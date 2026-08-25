/* Die Leinwand: Kamera, Zeichnen, Maus.

   17 370 Punkte sind fuer Canvas 2D unkritisch -- teuer waere nur, fuer jeden
   Punkt `fillStyle` neu zu setzen. Die Punkte werden deshalb einmal je
   Farbmodus nach Farbe sortiert und dann in Gruppen gezeichnet.

   Fotos erscheinen erst beim Hineinzoomen. In der Uebersicht waeren 17 000
   Bilder à 6 px ohnehin Matsch; sichtbar bleibt dort ein Leitbild je
   Kontinent. */

import { colorFor, spreadPoint } from "./model.js?v=6";
import { thumbUrl } from "../core/api.js?v=6";

//: Ab dieser Vergroesserung lohnen echte Fotos statt Punkte.
const THUMB_SCALE = 2600;
//: Mehr gleichzeitig sichtbare Bilder bringen nichts -- es passen keine hin.
const MAX_THUMBS = 420;
const MAX_INFLIGHT = 8;
const TRANSITION_MS = 700;

export function createScene(canvas, model, hooks = {}) {
  const ctx = canvas.getContext("2d", { alpha: false });
  const cam = { scale: 1, tx: 0, ty: 0 };

  let layout = "bedeutung";
  let colorMode = "kontinent";
  let mode = "fotos";       // "fotos" | "serien"
  let minEventSize = 3;
  // Regler, die direkt aufs Zeichnen wirken. Keine Messung, Geschmack --
  // deshalb einstellbar und nicht von mir festgelegt.
  let spread = 0;      // Kontinente auseinanderziehen
  let tileScale = 1;   // Kachelgroesse
  let declutter = 0;   // Mindestabstand gezeichneter Bilder in Pixeln
  let mask = new Uint8Array(model.n).fill(1);
  let selection = null; // Set<number> oder null
  let lassoPath = null;

  // Anordnungswechsel: alte und neue Position, dazwischen wird gemischt.
  let fromX = model.layouts.bedeutung.x, fromY = model.layouts.bedeutung.y;
  let toX = fromX, toY = fromY;
  let mix = 1, mixStart = 0;

  const px = new Float32Array(model.n);
  const py = new Float32Array(model.n);
  let buckets = [];

  const images = new Map();
  let inflight = 0;
  const queue = [];

  /* ---- Kamera ---------------------------------------------------------- */

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Entsteht die Leinwand, waehrend der Tab verborgen ist, meldet sie 0 x 0.
    // `fitAll` setzte dann Massstab 0 und die ganze Karte faellt auf einen
    // Punkt zusammen. Sobald sie eine Groesse hat, einmal neu einpassen.
    const wasEmpty = !canvas._w;
    canvas._w = r.width;
    canvas._h = r.height;
    if (wasEmpty) fitAll();
  }

  function fitAll() {
    const s = Math.min(canvas._w || 0, canvas._h || 0) * 0.92 || 1;
    cam.scale = s;
    cam.tx = (canvas._w - s) / 2;
    cam.ty = (canvas._h - s) / 2;
  }

  const toScreenX = (wx) => wx * cam.scale + cam.tx;
  const toScreenY = (wy) => wy * cam.scale + cam.ty;
  const toWorldX = (sx) => (sx - cam.tx) / cam.scale;
  const toWorldY = (sy) => (sy - cam.ty) / cam.scale;

  function zoomAt(sx, sy, factor) {
    const wx = toWorldX(sx), wy = toWorldY(sy);
    cam.scale = Math.max(120, Math.min(90000, cam.scale * factor));
    cam.tx = sx - wx * cam.scale;
    cam.ty = sy - wy * cam.scale;
    draw();
  }

  /** Auf einen Kontinent zufahren -- weich, damit der Bezug nicht abreisst. */
  function flyTo(wx, wy, scale) {
    const from = { ...cam };
    const t0 = performance.now();
    const target = { scale, tx: canvas._w / 2 - wx * scale, ty: canvas._h / 2 - wy * scale };
    (function step(now) {
      const p = Math.min(1, (now - t0) / 520);
      const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      cam.scale = from.scale + (target.scale - from.scale) * e;
      cam.tx = from.tx + (target.tx - from.tx) * e;
      cam.ty = from.ty + (target.ty - from.ty) * e;
      draw();
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  /** Auf eine Menge Punkte zufahren, so dass alle hineinpassen. */
  function flyToSet(indices) {
    if (!indices.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const i of indices) {
      x0 = Math.min(x0, px[i]); y0 = Math.min(y0, py[i]);
      x1 = Math.max(x1, px[i]); y1 = Math.max(y1, py[i]);
    }
    // Etwas Luft, und eine Untergrenze: liegen alle Treffer praktisch
    // aufeinander, soll die Kamera nicht ins Unendliche zoomen.
    const w = Math.max(x1 - x0, 0.012) * 1.35;
    const h = Math.max(y1 - y0, 0.012) * 1.35;
    const scale = Math.min(canvas._w / w, canvas._h / h);
    flyTo((x0 + x1) / 2, (y0 + y1) / 2, Math.min(scale, 26000));
  }

  /* ---- Farbgruppen ----------------------------------------------------- */

  function rebuildBuckets() {
    const byColor = new Map();
    for (let i = 0; i < model.n; i++) {
      const c = colorFor(model, i, colorMode);
      let list = byColor.get(c);
      if (!list) byColor.set(c, (list = []));
      list.push(i);
    }
    buckets = [...byColor.entries()].map(([color, list]) => ({
      color,
      idx: Int32Array.from(list),
    }));
  }

  /* ---- Bilder ---------------------------------------------------------- */

  function imageForId(id, size) {
    const key = `${id}:${size}`;
    const hit = images.get(key);
    if (hit !== undefined) return hit;
    images.set(key, null);
    queue.push({ key, id, size });
    pump();
    return null;
  }

  const imageFor = (i, size) => imageForId(model.ids[i], size);

  function pump() {
    while (inflight < MAX_INFLIGHT && queue.length) {
      // Der Reihe nach, nicht zuletzt-zuerst. Angefragt wird von der
      // Bildmitte nach aussen; bei eingeschalteter Entzerrung sind die
      // mittleren die *einzigen*, die gezeichnet werden. Auf einem Stapel
      // laegen sie ganz unten und die Karte bliebe minutenlang leer.
      const job = queue.shift();
      inflight++;
      const img = new Image();
      img.decoding = "async";
      img.onload = () => { images.set(job.key, img); inflight--; pump(); schedule(); };
      img.onerror = () => { images.set(job.key, false); inflight--; pump(); };
      img.src = thumbUrl(job.id, job.size);
    }
  }

  /* ---- Zeichnen -------------------------------------------------------- */

  let pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; draw(); });
  }

  function positions(now) {
    if (mix < 1) {
      const p = Math.min(1, (now - mixStart) / TRANSITION_MS);
      mix = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      if (p >= 1) mix = 1;
    }
    if (mix >= 1) {
      px.set(toX);
      py.set(toY);
      applySpread();
      return false;
    }
    for (let i = 0; i < model.n; i++) {
      px[i] = fromX[i] + (toX[i] - fromX[i]) * mix;
      py[i] = fromY[i] + (toY[i] - fromY[i]) * mix;
    }
    applySpread();
    return true;
  }

  function applySpread() {
    if (!spread) return;
    const cc = model.layouts[layout].centroids;
    for (let i = 0; i < model.n; i++) {
      const c = model.cl[i] * 2;
      px[i] = spreadPoint(px[i], cc[c], spread);
      py[i] = spreadPoint(py[i], cc[c + 1], spread);
    }
  }

  function draw() {
    const now = performance.now();
    const animating = positions(now);
    const w = canvas._w, h = canvas._h;
    ctx.fillStyle = "#12151a";
    ctx.fillRect(0, 0, w, h);

    if (layout === "zeit" && mix > 0.5) drawYearBands();

    const r = mode === "serien" ? 1 : Math.max(1.1, Math.min(7, cam.scale / 900));
    // `selection` ist null, solange nichts gewaehlt wurde. In der Serien-Ebene
    // treten die Punkte trotzdem zurueck -- dort sind sie Untergrund.
    const picked = selection && selection.size > 0;
    const dim = mode === "serien" || picked;
    const showThumbs = cam.scale > THUMB_SCALE && !animating;

    // Punkte
    for (const b of buckets) {
      ctx.fillStyle = dim ? fade(b.color) : b.color;
      const idx = b.idx;
      for (let k = 0; k < idx.length; k++) {
        const i = idx[k];
        if (!mask[i]) continue;
        if (picked && selection.has(i)) continue;
        const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
        if (sx < -8 || sy < -8 || sx > w + 8 || sy > h + 8) continue;
        ctx.fillRect(sx - r, sy - r, r * 2, r * 2);
      }
    }

    // Ausgewaehlte oben drauf, damit sie nicht verdeckt werden
    if (picked) {
      ctx.fillStyle = "#ffffff";
      for (const i of selection) {
        if (!mask[i]) continue;
        const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
        if (sx < -8 || sy < -8 || sx > w + 8 || sy > h + 8) continue;
        ctx.fillRect(sx - r - 0.5, sy - r - 0.5, r * 2 + 1, r * 2 + 1);
      }
    }

    if (mode === "serien") drawEvents(w, h);
    else if (showThumbs) drawThumbs(w, h, picked);
    drawClusterLabels(w, h);
    if (lassoPath) drawLasso();
    if (animating) schedule();
  }

  function fade(color) {
    // Nicht-Ausgewaehltes zuruecknehmen, ohne die Farbe zu verlieren.
    return color.startsWith("hsl") ? color.replace(")", " / 0.22)") : color + "38";
  }

  function drawYearBands() {
    const { years, start, width } = model.layouts.zeit;
    ctx.save();
    ctx.font = "500 11px system-ui, sans-serif";
    ctx.textBaseline = "top";
    for (const y of years) {
      const x0 = toScreenX(start.get(y));
      const x1 = toScreenX(start.get(y) + width.get(y));
      if (x1 < 0 || x0 > canvas._w) continue;
      ctx.fillStyle = y % 2 ? "#171b21" : "#151920";
      ctx.fillRect(x0, 0, x1 - x0, canvas._h);
      if (x1 - x0 > 26) {
        ctx.fillStyle = "#5d6773";
        ctx.fillText(String(y), x0 + 4, 6);
      }
    }
    ctx.restore();
  }

  function drawThumbs(w, h, picked) {
    const size = cam.scale > 9000 ? 320 : 160;
    // Von der Bildmitte nach aussen: passen nicht alle hin, gewinnt das
    // Naheliegende. Abstand einmal rechnen, nicht in jedem Vergleich.
    const cx = w / 2, cy = h / 2;
    const near = [];
    for (let i = 0; i < model.n; i++) {
      if (!mask[i]) continue;
      const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
      if (sx < -60 || sy < -60 || sx > w + 60 || sy > h + 60) continue;
      near.push([(sx - cx) ** 2 + (sy - cy) ** 2, i, sx, sy]);
    }
    near.sort((a, b) => a[0] - b[0]);

    const box = Math.max(22, Math.min(96, cam.scale / 90)) * tileScale;
    // Entzerren: in dichten Gegenden liegen tausend Bilder aufeinander und
    // alles wird zu Brei. Wer zu nah an einem schon gezeichneten Bild liegt,
    // wird uebersprungen -- dann bleibt bei *jeder* Vergroesserung lesbar,
    // was zu sehen ist, statt erst ganz weit unten.
    const grid = declutter > 0 ? new Set() : null;
    const cell = Math.max(1, declutter);
    // Mehr Bilder als Rasterfelder koennen ohnehin nicht gezeichnet werden --
    // ohne diese Schranke fragt jeder Durchlauf tausende Vorschaubilder an,
    // von denen die allermeisten sofort wieder verworfen werden.
    const maxCells = grid
      ? (Math.ceil(w / cell) + 2) * (Math.ceil(h / cell) + 2)
      : Infinity;
    let drawn = 0;
    for (const [, i, sx, sy] of near) {
      if (drawn >= MAX_THUMBS) break;
      if (grid) {
        if (grid.size >= maxCells) break;
        const key = `${Math.round(sx / cell)}:${Math.round(sy / cell)}`;
        if (grid.has(key)) continue;
        grid.add(key);
      }
      const img = imageFor(i, size);
      // Der Platz bleibt belegt, auch wenn das Bild noch laedt: sonst
      // uebernimmt bei jedem Ladevorgang ein anderes Foto die Stelle und
      // die Karte flackert.
      if (!img) continue;
      drawn++;
      const ar = img.naturalWidth / img.naturalHeight || 1;
      const bw = ar >= 1 ? box : box * ar;
      const bh = ar >= 1 ? box / ar : box;
      ctx.globalAlpha = picked && !selection.has(i) ? 0.28 : 1;
      ctx.drawImage(img, sx - bw / 2, sy - bh / 2, bw, bh);
      if (picked && selection.has(i)) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(sx - bw / 2, sy - bh / 2, bw, bh);
      }
      ctx.globalAlpha = 1;
    }
  }

  /** Eine Kachel je Gelegenheit statt eines Punkts je Foto.

      17 370 Einzelbilder sind nicht stoeberbar, 1 755 Serien ab drei Fotos
      schon. Die Groesse folgt der Wurzel der Fotozahl -- linear wuerde
      „Silvester 2012" mit 163 Bildern alles andere verdecken. */
  function drawEvents(w, h) {
    const visible = [];
    for (const ev of model.events) {
      if (ev.n < minEventSize) continue;
      const sx = toScreenX(ev.x), sy = toScreenY(ev.y);
      if (sx < -80 || sy < -80 || sx > w + 80 || sy > h + 80) continue;
      visible.push([ev, sx, sy]);
    }
    // Kleine zuerst zeichnen, grosse oben drauf.
    visible.sort((a, b) => a[0].n - b[0].n);
    for (const [ev, sx, sy] of visible) {
      const box = Math.max(16, Math.min(88, Math.sqrt(ev.n) * 5 * Math.max(1, cam.scale / 900)));
      const img = imageForId(ev.cover, box > 46 ? 320 : 160);
      if (img) {
        const ar = img.naturalWidth / img.naturalHeight || 1;
        const bw = ar >= 1 ? box : box * ar;
        const bh = ar >= 1 ? box / ar : box;
        ctx.drawImage(img, sx - bw / 2, sy - bh / 2, bw, bh);
        // Weit gestreute Serien markieren: sie halten inhaltlich nicht
        // zusammen und sind oft gar keine Gelegenheit, sondern ein Tag.
        if (ev.spread > 0.18) {
          ctx.strokeStyle = "#e0703c";
          ctx.lineWidth = 1.5;
          ctx.strokeRect(sx - bw / 2, sy - bh / 2, bw, bh);
        }
      } else {
        ctx.fillStyle = "#2b323f";
        ctx.fillRect(sx - box / 2, sy - box / 2, box, box);
      }
    }
  }

  /** Naechste Serie zum Zeiger -- gewichtet, damit grosse Kacheln leichter treffen. */
  /** Kontinentname unter dem Zeiger -- sein Schwerpunkt traegt die Schrift. */
  function labelAt(sx, sy) {
    if (mode === "serien" || cam.scale > 6000) return -1;
    const cc = model.layouts[layout].centroids;
    for (const c of model.clusters) {
      const cx = toScreenX(spread ? spreadPoint(c.x, cc[c.i * 2], spread) : c.x);
      const cy = toScreenY(spread ? spreadPoint(c.y, cc[c.i * 2 + 1], spread) : c.y);
      const label = model.clusterLabel[c.i];
      const half = label.length * 3.6 + 8;
      if (Math.abs(sx - cx) <= half && Math.abs(sy - cy) <= 11) return c.i;
    }
    return -1;
  }

  function nearestEvent(sx, sy) {
    let best = -1, bestD = Infinity;
    for (const ev of model.events) {
      if (ev.n < minEventSize) continue;
      const box = Math.max(16, Math.min(88, Math.sqrt(ev.n) * 5 * Math.max(1, cam.scale / 900)));
      const dx = toScreenX(ev.x) - sx, dy = toScreenY(ev.y) - sy;
      const d = Math.max(Math.abs(dx), Math.abs(dy)) - box / 2;
      if (d < bestD) { bestD = d; best = ev.i; }
    }
    return bestD <= 4 ? best : -1;
  }

  function drawClusterLabels(w, h) {
    // In der Uebersicht die Kontinentnamen, beim Hineinzoomen ausblenden --
    // dann sprechen die Fotos selbst.
    const strength = cam.scale > THUMB_SCALE ? 0.18 : 1;
    if (strength < 0.2 && cam.scale > 6000) return;
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const cc = model.layouts[layout].centroids;
    for (const c of model.clusters) {
      const sx = toScreenX(spread ? spreadPoint(c.x, cc[c.i * 2], spread) : c.x);
      const sy = toScreenY(spread ? spreadPoint(c.y, cc[c.i * 2 + 1], spread) : c.y);
      if (sx < -40 || sy < -20 || sx > w + 40 || sy > h + 20) continue;
      const label = model.clusterLabel[c.i];
      const weak = c.cap_share < 0.15;
      ctx.font = `${weak ? 400 : 600} ${Math.min(15, 10 + c.n / 90)}px system-ui, sans-serif`;
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = "rgba(10,12,16,0.85)";
      ctx.globalAlpha = strength * (weak ? 0.5 : 0.95);
      ctx.strokeText(label, sx, sy);
      ctx.fillStyle = weak ? "#9aa4b0" : "#e8edf3";
      ctx.fillText(label, sx, sy);
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function drawLasso() {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(lassoPath[0][0], lassoPath[0][1]);
    for (const [x, y] of lassoPath.slice(1)) ctx.lineTo(x, y);
    ctx.closePath();
    ctx.fillStyle = "rgba(120,190,255,0.10)";
    ctx.fill();
    ctx.strokeStyle = "#7ec2ff";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.restore();
  }

  /* ---- Treffer --------------------------------------------------------- */

  function nearest(sx, sy, maxPx = 14) {
    let best = -1, bestD = maxPx * maxPx;
    for (let i = 0; i < model.n; i++) {
      if (!mask[i]) continue;
      const dx = toScreenX(px[i]) - sx, dy = toScreenY(py[i]) - sy;
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  function inPolygon(path, x, y) {
    let inside = false;
    for (let i = 0, j = path.length - 1; i < path.length; j = i++) {
      const [xi, yi] = path[i], [xj, yj] = path[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function pickInPath(path) {
    const hit = new Set();
    // Erst die Huelle, dann der teure Test -- spart bei kleinen Lassos fast alles.
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of path) {
      x0 = Math.min(x0, x); y0 = Math.min(y0, y);
      x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    }
    for (let i = 0; i < model.n; i++) {
      if (!mask[i]) continue;
      const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
      if (sx < x0 || sx > x1 || sy < y0 || sy > y1) continue;
      if (inPolygon(path, sx, sy)) hit.add(i);
    }
    return hit;
  }

  /* ---- Maus ------------------------------------------------------------ */

  let drag = null;
  let lassoMode = false;

  canvas.addEventListener("mousedown", (e) => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (lassoMode || e.shiftKey) {
      lassoPath = [[sx, sy]];
      drag = { kind: "lasso" };
    } else {
      drag = { kind: "pan", sx, sy, tx: cam.tx, ty: cam.ty, moved: false };
    }
  });

  window.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (!drag) {
      if (sx < 0 || sy < 0 || sx > r.width || sy > r.height) return;
      if (mode === "serien") { hooks.onHoverEvent?.(nearestEvent(sx, sy), sx, sy); return; }
      const overLabel = labelAt(sx, sy) >= 0;
      canvas.classList.toggle("on-label", overLabel);
      hooks.onHover?.(overLabel ? -1 : nearest(sx, sy), sx, sy);
      return;
    }
    if (drag.kind === "lasso") {
      const last = lassoPath[lassoPath.length - 1];
      if ((last[0] - sx) ** 2 + (last[1] - sy) ** 2 > 9) lassoPath.push([sx, sy]);
      schedule();
    } else {
      cam.tx = drag.tx + (sx - drag.sx);
      cam.ty = drag.ty + (sy - drag.sy);
      if (Math.abs(sx - drag.sx) + Math.abs(sy - drag.sy) > 3) drag.moved = true;
      schedule();
    }
  });

  window.addEventListener("mouseup", (e) => {
    if (!drag) return;
    if (drag.kind === "lasso" && lassoPath && lassoPath.length > 2) {
      hooks.onLasso?.(pickInPath(lassoPath), e.altKey);
    } else if (drag.kind === "pan" && !drag.moved) {
      const r = canvas.getBoundingClientRect();
      const sx = e.clientX - r.left, sy = e.clientY - r.top;
      if (mode === "serien") {
        const ev = nearestEvent(sx, sy);
        if (ev >= 0) hooks.onPickEvent?.(ev, e.ctrlKey || e.metaKey);
        return;
      }
      // Ein Klick auf den Kontinentnamen waehlt den ganzen Kontinent. Das ist
      // die praezise Alternative zum Lasso: wo die Raender ineinander
      // sprenkeln, erwischt ein gezogener Kreis immer zu viel.
      const lab = labelAt(sx, sy);
      if (lab >= 0) { hooks.onPickCluster?.(lab, e.shiftKey); return; }
      const i = nearest(sx, sy);
      // Strg/Cmd nimmt den Faden auf, statt das Bild zu oeffnen.
      if (i >= 0) (e.ctrlKey || e.metaKey ? hooks.onThread : hooks.onPick)?.(i);
    }
    lassoPath = null;
    drag = null;
    schedule();
  });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
  }, { passive: false });

  canvas.addEventListener("mouseleave", () => hooks.onHover?.(-1));

  /* ---- Aussenschnittstelle --------------------------------------------- */

  const scene = {
    get camera() { return cam; },
    get layout() { return layout; },
    setLayout(name) {
      if (name === layout) return;
      layout = name;
      fromX = Float32Array.from(px);
      fromY = Float32Array.from(py);
      toX = model.layouts[name].x;
      toY = model.layouts[name].y;
      mix = 0;
      mixStart = performance.now();
      schedule();
    },
    setColorMode(mode) { colorMode = mode; rebuildBuckets(); schedule(); },
    setMask(m) { mask = m; schedule(); },
    setSelection(sel) { selection = sel; schedule(); },
    setMode(m) { mode = m; schedule(); },
    setSpread(k) { spread = k; schedule(); },
    setTileScale(k) { tileScale = k; schedule(); },
    setDeclutter(px_) { declutter = px_; schedule(); },
    setMinEventSize(n) { minEventSize = n; schedule(); },
    get mode() { return mode; },
    setLassoMode(on) { lassoMode = on; canvas.classList.toggle("lasso", on); },
    focusCluster(c) { flyTo(c.x, c.y, 5200); },
    focusSet(indices) { flyToSet(indices); },
    reset() { fitAll(); scene.setLayout("bedeutung"); schedule(); },
    resize() { resize(); schedule(); },
    fitAll() { fitAll(); schedule(); },
    draw: schedule,
    positionOf(i) { return [toScreenX(px[i]), toScreenY(py[i])]; },
  };

  new ResizeObserver(() => { resize(); schedule(); }).observe(canvas);
  resize();
  fitAll();
  rebuildBuckets();
  px.set(toX);
  py.set(toY);
  schedule();
  return scene;
}
