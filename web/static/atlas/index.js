/* Der Tab „Atlas": Werkzeugleiste, Auswahl, Aktionen.

   Was diese Ansicht von einer Kachelwand unterscheidet, ist nicht das
   Aussehen, sondern dass man auf ihr arbeiten kann: ein Lasso um den
   Screenshot-Kontinent markiert tausend Bilder, und die bekommen in einem
   Zug eine Notiz. Genau das kann der Explorer nicht, weil er Aehnlichkeit
   nicht kennt. */

import { $, escapeHtml, num } from "../core/dom.js?v=22";
import { api, thumbUrl } from "../core/api.js?v=22";
import { openModal } from "../core/modal.js?v=22";
import { createPathPick } from "../core/pathpick.js?v=22";
import { feature, gate } from "../core/capabilities.js?v=22";
import {
  COLOR_MODES, FILTERS, FLAG, countVisible, foldedAway, legendFor, loadAtlas,
  personNames, photosOfCluster, photosOfEvent, photosOfPerson, photosOfTag,
  spaceCounts, tagCounts, tidiness, visibleMask,
} from "./model.js?v=22";
import { createScene } from "./scene.js?v=22";

const LENSES = [
  { id: "bedeutung", label: "Bedeutung", hint: "Nähe heißt: sieht sich ähnlich" },
  { id: "zeit", label: "Zeit × Bedeutung", hint: "waagerecht die Jahre, senkrecht dieselbe Bedeutungsachse" },
];

/* Wie die Vorschaubilder auf das Fenster verteilt werden.

   Es gibt kein richtig. Die Punktansicht zeigt Farbgruppen unverdeckt -- wo
   ein Kontinent aufhört, liest man daran besser ab als an Bildern. Der Kegel
   folgt der Bildmitte und lässt den Rand als Punkte stehen: eine Taschenlampe,
   gut zum Verfolgen einer Spur. Die Fläche verteilt dieselbe Zahl Bilder übers
   ganze Fenster. „Alles" hat keine Schranke und ist zum Vergleichen da --
   was es kostet, steht in der Fußzeile. */
const THUMB_MODES = [
  { id: "punkte", label: "Punkte",
    hint: "Keine Bilder. Farbgruppen bleiben unverdeckt — Kontinente und Zustände liest man so am besten." },
  { id: "kegel", label: "Kegel",
    hint: "Bilder um die Bildmitte, außen Punkte. Wie eine Taschenlampe: folgt dem, worauf man zielt." },
  { id: "flaeche", label: "Fläche",
    hint: "Dieselbe Zahl Bilder, aber übers ganze Fenster verteilt statt in der Mitte gehäuft." },
  { id: "alles", label: "Alles",
    hint: "Jedes sichtbare Bild, ohne Schranke. Zum Vergleichen — die Fußzeile zeigt, was es kostet." },
];

//: Die Wahl bleibt erhalten: wer die Punktansicht mag, will sie nicht bei
//: jedem Aufruf neu einstellen.
const THUMB_KEY = "pv-atlas-thumbmode";

/* Werkzeuge -- absichtlich anders gebaut als die Umschalter darüber: sie
   verändern nicht, *was* man sieht, sondern *wie* man arbeitet. */
const TOOLS = [
  {
    id: "atlas-lasso", glyph: "◌", label: "Lasso",
    hint: "Auswahl umkreisen (oder Shift halten). Der Zug ersetzt die Auswahl; Strg/Cmd nimmt "
        + "dazu, Alt zieht ab, Strg+Alt grenzt die bestehende Auswahl ein — dann kommen Nachbarn "
        + "aus anderen Kontinenten nicht mit, auch wenn der Zug sie streift. "
        + "Wo Ränder ineinander laufen, trifft ein Klick auf den Kontinentnamen genauer.",
    run: (b) => scene.setLassoMode(b.classList.toggle("on")),
  },
  {
    id: "atlas-knobs", glyph: "◑", label: "Darstellung",
    hint: "Wie weit auseinander, wie groß, wie überlappend",
    run: (b) => $("atlas-controls").classList.toggle("hidden", !b.classList.toggle("on")),
  },
  {
    id: "atlas-reopen", glyph: "◆", label: "Aufgaben",
    hint: "Was ist noch offen?",
    run: () => paintBriefing(),
  },
  {
    id: "atlas-stats-btn", glyph: "◍", label: "Aufwand",
    hint: "Wie viele Bilder gezeichnet werden, wie lange es dauert, wie viel im Speicher liegt",
    run: (b) => { statsOn = b.classList.toggle("on"); pollStats(); paintStats(); },
  },
  {
    id: "atlas-reset", glyph: "⊙", label: "Übersicht",
    hint: "Alles zeigen (Taste 0)",
    run: () => { scene.fitAll(); clearSelection(); },
  },
];

let model = null;
let scene = null;
let selection = new Set();
let filters = { fold: false, open: false, camera: false, spacesOff: new Set() };
let colorMode = "kontinent";
let thumbMode = "flaeche";
let statsOn = false;
let showLightbox = () => {};
let booted = false;

/* Weggeräumtes. Die Karte ist ein Standbild: verschiebt man Fotos, stünden sie
   bis zum nächsten `atlas_build` weiter da. Die UI merkt sich deshalb, was weg
   ist — und vergisst es, sobald eine neue Karte gerechnet wurde. */
const HIDDEN_KEY = "pv-atlas-hidden";
let hidden = new Set();

function loadHidden(builtAt) {
  try {
    const raw = JSON.parse(localStorage.getItem(HIDDEN_KEY) || "null");
    if (raw && raw.builtAt === builtAt) return new Set(raw.ids);
  } catch (e) { /* ein kaputter Eintrag ist kein Grund, die Ansicht zu verweigern */ }
  return new Set();
}

function saveHidden() {
  try {
    localStorage.setItem(HIDDEN_KEY, JSON.stringify({ builtAt: model.builtAt, ids: [...hidden] }));
  } catch (e) { /* voller Speicher darf die Karte nicht kippen */ }
}

/** Was gerade sichtbar ist. Einmal benannt, damit `hidden` nirgends vergessen wird. */
function mask() {
  return visibleMask(model, filters, hidden);
}

export async function initAtlas(deps = {}) {
  if (booted) { scene?.resize(); return; }
  showLightbox = deps.showLightbox || showLightbox;

  const status = $("atlas-status");
  status.textContent = "Karte wird geladen …";
  try {
    model = await loadAtlas();
  } catch (err) {
    // „Rechne die Karte" ist ein schlechter Rat, wenn der Befehl ohne
    // Zusatzpaket scheitert. Dann steht hier, was wirklich fehlt.
    const build = await feature("atlas_build");
    status.innerHTML = `<pre class="atlas-missing">${escapeHtml(err.message)}${
      build.ok ? "" : `

${escapeHtml(build.why)}`}</pre>`;
    return;
  }
  status.textContent = "";
  hidden = loadHidden(model.builtAt);
  booted = true;

  buildToolbar();
  scene = createScene($("atlas-canvas"), model, {
    onHover: showHover,
    onPick: (i) => openAt(i),
    onThread: (i) => followThread([i]),
    onHoverEvent: showEventHover,
    onPickEvent: openEvent,
    onPickCluster: pickCluster,
    onLasso: (hit, subtract) => applyLasso(hit, subtract),
  });
  scene.setThumbMode(thumbMode);
  applyFilters();
  paintLegend();
  watchBarHeight();
  makeDraggable($("atlas-brief"), "h2");
  makeDraggable($("atlas-sel"), "header");
  // Beim Aufschlagen nur zeigen, wenn es etwas zu tun gibt. Ein Kasten, der
  // "nichts offen" meldet und dabei die Werkzeugleiste verdeckt, ist keine
  // Hilfe -- ueber das Werkzeug "Aufgaben" ist er jederzeit erreichbar.
  paintBriefing({ onlyIfWork: true });
  document.addEventListener("keydown", onKey);
}

/* ---- Panels verschieben ------------------------------------------------
   Zwei Kaesten liegen ueber der Karte, und wo sie stehen ist manchmal genau
   dort, wo man hinsehen will. Angefasst wird an der Ueberschrift -- ein
   Klick auf einen Knopf darin soll nicht ziehen. Die Lage bleibt erhalten,
   sonst muesste man sie nach jedem Oeffnen neu wegschieben. */

const DRAG_KEY = "pv-atlas-panel-";

function panelOffset(id) {
  try {
    const raw = JSON.parse(localStorage.getItem(DRAG_KEY + id) || "null");
    if (raw && Number.isFinite(raw.x) && Number.isFinite(raw.y)) return raw;
  } catch { /* privater Modus */ }
  return { x: 0, y: 0 };
}

function placePanel(el) {
  const { x, y } = panelOffset(el.id);
  el.style.transform = x || y ? `translate(${x}px, ${y}px)` : "";
}

function makeDraggable(el, handleSel) {
  if (!el || el.dataset.dragReady) return;
  el.dataset.dragReady = "1";
  placePanel(el);
  el.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(handleSel);
    if (!handle || !el.contains(handle)) return;
    // Knoepfe in der Ueberschrift bleiben Knoepfe.
    if (e.target.closest("button, a, input, select")) return;
    e.preventDefault();
    const start = panelOffset(el.id);
    const x0 = e.clientX, y0 = e.clientY;
    el.classList.add("dragging");
    el.setPointerCapture(e.pointerId);

    const move = (ev) => {
      const box = el.getBoundingClientRect();
      const wrap = el.parentElement.getBoundingClientRect();
      let x = start.x + (ev.clientX - x0);
      let y = start.y + (ev.clientY - y0);
      // Nicht aus dem Fenster schieben koennen -- ein Kasten, den man nicht
      // mehr sieht, laesst sich auch nicht zurueckholen.
      const minX = wrap.left - box.left + start.x + 20 - box.width;
      const maxX = wrap.right - box.left + start.x - 20;
      const minY = wrap.top - box.top + start.y;
      const maxY = wrap.bottom - box.top + start.y - 36;
      x = Math.max(minX, Math.min(maxX, x));
      y = Math.max(minY, Math.min(maxY, y));
      el.style.transform = `translate(${x}px, ${y}px)`;
      el._drag = { x, y };
    };
    const up = () => {
      el.classList.remove("dragging");
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      if (el._drag) {
        try { localStorage.setItem(DRAG_KEY + el.id, JSON.stringify(el._drag)); }
        catch { /* privater Modus */ }
      }
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  });
}

/** Die Werkzeugleiste bricht um; die Panels muessen wissen, wie hoch sie ist. */
function watchBarHeight() {
  const bar = $("atlas-bar");
  const wrap = bar?.parentElement;
  if (!bar || !wrap) return;
  const apply = () =>
    wrap.style.setProperty("--atlas-bar-h", `${Math.round(bar.getBoundingClientRect().height)}px`);
  new ResizeObserver(apply).observe(bar);
  apply();
}

/* ---- Werkzeugleiste ---------------------------------------------------- */

function buildToolbar() {
  $("atlas-lenses").innerHTML = LENSES.map((l, i) =>
    `<button class="chip${i === 0 ? " on" : ""}" data-lens="${l.id}" title="${escapeHtml(l.hint)}">${l.label}</button>`
  ).join("");
  $("atlas-lenses").onclick = (e) => {
    const b = e.target.closest("[data-lens]");
    if (!b) return;
    $("atlas-lenses").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
    scene.setLayout(b.dataset.lens);
  };

  $("atlas-levels").innerHTML =
    `<button class="chip on" data-level="fotos" title="Jeder Punkt ein Foto">Fotos</button>` +
    `<button class="chip" data-level="serien" title="Jede Kachel eine Gelegenheit — ${num(model.events.length)} Serien">Serien</button>`;
  $("atlas-levels").onclick = (e) => {
    const b = e.target.closest("[data-level]");
    if (!b) return;
    $("atlas-levels").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
    scene.setMode(b.dataset.level);
    $("atlas-hover").classList.add("hidden");
    hoverIdx = -1;
    $("atlas-minsize").classList.toggle("hidden", b.dataset.level !== "serien");
    updateCount();
  };

  const min = $("atlas-minsize");
  min.onchange = () => { scene.setMinEventSize(Number(min.value)); updateCount(); };

  // Wie die Vorschaubilder verteilt werden. Vier Antworten auf dieselbe
  // Frage -- welche taugt, haengt daran, was man gerade sucht, und nicht
  // daran, was ich fuer richtig halte.
  // Nur lesen, nicht anwenden: die Leiste entsteht vor der Szene, und
  // `scene` ist hier noch null.
  try {
    const saved = localStorage.getItem(THUMB_KEY);
    if (THUMB_MODES.some((m) => m.id === saved)) thumbMode = saved;
  } catch { /* privater Modus */ }
  $("atlas-thumbs").innerHTML = THUMB_MODES.map((m) =>
    `<button class="chip${m.id === thumbMode ? " on" : ""}" data-thumb="${m.id}" ` +
    `title="${escapeHtml(m.hint)}">${m.label}</button>`).join("");
  $("atlas-thumbs").onclick = (e) => {
    const b = e.target.closest("[data-thumb]");
    if (!b) return;
    $("atlas-thumbs").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
    thumbMode = b.dataset.thumb;
    scene.setThumbMode(thumbMode);
    try { localStorage.setItem(THUMB_KEY, thumbMode); } catch { /* privater Modus */ }
    paintStats();
  };

  // Die Gruppenüberschrift kommt aus dem `data-group` im HTML, nicht aus einem
  // eingeschobenen <span> -- sonst sitzt sie in der Reihe statt darüber.
  $("atlas-colors").innerHTML = COLOR_MODES.map((m, i) =>
    `<button class="chip${i === 0 ? " on" : ""}" data-color="${m.id}">${m.label}</button>`
  ).join("");
  $("atlas-colors").onclick = (e) => {
    const b = e.target.closest("[data-color]");
    if (!b) return;
    $("atlas-colors").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
    colorMode = b.dataset.color;
    scene.setColorMode(colorMode);
    paintLegend();
  };

  // Bereiche: die erste Ordnerebene auf der Platte. Ausschalten heisst
  // „gerade nicht ansehen", nicht „ausgeschlossen" -- ein Klick nimmt sie
  // wieder herein, und in Suche und Serien stehen sie unverändert.
  const perSpace = spaceCounts(model);
  $("atlas-spaces").innerHTML = model.spaces.map((name, i) =>
    perSpace.get(i)
      ? `<button class="tog on" data-space="${i}" title="${escapeHtml(model.root)}/${escapeHtml(name)} — Klick blendet aus, erneuter Klick zeigt wieder">${escapeHtml(name)} <b>${num(perSpace.get(i))}</b></button>`
      : ""
  ).join("");
  $("atlas-spaces").onclick = (e) => {
    const b = e.target.closest("[data-space]");
    if (!b) return;
    const i = Number(b.dataset.space);
    if (filters.spacesOff.has(i)) filters.spacesOff.delete(i);
    else filters.spacesOff.add(i);
    b.classList.toggle("on", !filters.spacesOff.has(i));
    applyFilters();
  };

  // Die Beschriftung nennt die Wirkung, nicht den Mechanismus: „Stapel falten"
  // sagt niemandem, was passiert.
  const folded = foldedAway(model);
  $("atlas-filters").innerHTML = FILTERS.map((f) =>
    `<button class="tog" data-filter="${f.id}" title="${escapeHtml(f.hint)}">${
      f.id === "fold" ? `Dubletten falten <b>−${num(folded)}</b>` : f.label}</button>`
  ).join("");
  $("atlas-filters").onclick = (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    filters[b.dataset.filter] = !filters[b.dataset.filter];
    b.classList.toggle("on", filters[b.dataset.filter]);
    applyFilters();
  };

  $("atlas-tools").innerHTML = TOOLS.map((t) =>
    `<button class="tool" id="${t.id}" title="${escapeHtml(t.hint)}"><i>${t.glyph}</i>${t.label}</button>`
  ).join("");
  $("atlas-tools").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    TOOLS.find((t) => t.id === b.id)?.run(b);
  };

  buildControls();

  const jump = $("atlas-jump");
  jump.innerHTML = "<option value=''>Kontinent anfliegen …</option>" +
    [...model.clusters].sort((a, b) => b.n - a.n).map((c) =>
      `<option value="${c.i}">${escapeHtml(model.clusterLabel[c.i])} — ${num(c.n)}</option>`
    ).join("");
  jump.onchange = () => {
    const c = model.clusters[Number(jump.value)];
    if (!c) return;
    scene.focusCluster(c);
    jump.value = "";
  };

  // Szenen: die Kontinente heißen teils „screenshot, dokument", aber
  // Screenshots liegen in neun davon. Ein Griff statt neun.
  //
  // Bewusst *nicht* `scene` genannt — so heißt schon die Leinwand, und ein
  // überschatteter Name hat mich hier eine Runde gekostet.
  const sceneSel = $("atlas-scene");
  const perTag = tagCounts(model);
  sceneSel.innerHTML = "<option value=''>Szene wählen …</option>" +
    [...perTag.entries()].sort((a, b) => b[1] - a[1]).map(([t, n]) =>
      `<option value="${t}">${escapeHtml(model.tags[t])} — ${num(n)}</option>`
    ).join("");
  sceneSel.onchange = () => {
    const t = sceneSel.value;
    // Zurücksetzen, damit dieselbe Szene wieder wählbar ist: `change` feuert
    // sonst nicht erneut.
    sceneSel.selectedIndex = 0;
    sceneSel.blur();
    if (t === "") return;
    selection = photosOfTag(model, Number(t), mask());
    scene.setSelection(selection);
    if (selection.size) scene.focusSet([...selection]);
    paintSelection();
  };

  const who = $("atlas-who");
  const counts = new Map();
  for (let i = 0; i < model.n; i++) for (const k of model.pe[i] || []) counts.set(k, (counts.get(k) || 0) + 1);
  who.innerHTML = "<option value=''>Person hervorheben …</option>" +
    [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) =>
      `<option value="${k}">${escapeHtml(model.persons[k])} — ${num(n)}</option>`
    ).join("");
  who.onchange = () => {
    if (who.value === "") { clearSelection(); return; }
    selection = photosOfPerson(model, Number(who.value), mask());
    scene.setSelection(selection);
    paintSelection();
  };

  $("atlas-built").textContent =
    `${num(model.n)} Fotos · ${model.clusters.length} Kontinente · ${model.space.toUpperCase()} · ${model.builtAt.slice(0, 10)}`;
}

/* ---- Regler -----------------------------------------------------------
   „Das exakt feinzutunen ist quasi unmöglich" — stimmt. Also nicht ich,
   sondern der Betrachter: drei Werte, die unmittelbar aufs Zeichnen wirken
   und nichts neu rechnen. */

const KNOBS = [
  {
    id: "spread", label: "Kontinente auseinander", min: 0, max: 2, step: 0.05, start: 0,
    hint: "Die Kontinente stoßen einander ab, bis sie sich nicht mehr überdecken, und werden dabei "
        + "kompakter. Innerhalb eines Kontinents bleibt die Anordnung, wie sie ist — nur die Ränder "
        + "hören auf, ineinander zu sprenkeln. Danach wird alles zurück ins Bild gestaucht.",
    apply: (v) => scene.setSpread(v), fmt: (v) => (v ? `${v.toFixed(2)}×` : "aus"),
  },
  {
    id: "declutter", label: "Bilder abstoßen", min: 0, max: 2.5, step: 0.1, start: 0,
    hint: "Mindestabstand zwischen zwei Bildern, in Kacheln gemessen — bei 1,0 stoßen sie gerade "
        + "aneinander. Die Bilder schieben einander weg, bis der Abstand steht, wie die Knoten in "
        + "einem Graphen. Es wird also nichts weggelassen, der Haufen wird aufgemacht. "
        + "Der Preis ist die genaue Lage: wie weit ein Bild wandern darf, wächst mit dem Zoom — "
        + "in der Übersicht unsichtbar, ganz nah bis zu einer halben Bildschirmbreite. "
        + "Der Punkt darunter bleibt liegen, wo das Foto hingehört.",
    apply: (v) => scene.setDeclutter(v),
    fmt: (v) => (v ? (v < 1 ? `${v.toFixed(1)}× — überlappt noch` : `${v.toFixed(1)}× Kachel`) : "aus"),
  },
  {
    id: "tiles", label: "Kachelgröße", min: 0.4, max: 2.5, step: 0.1, start: 1,
    hint: "Die Kachelgröße folgt dem Zoom von selbst — beim Herauszoomen kleiner für den Überblick, "
        + "ganz nah größer zum Ansehen. Dieser Regler korrigiert das nach oben oder unten; "
        + "1,0 heißt „so wie automatisch“. Nur die Darstellung, nicht die Auswahl.",
    apply: (v) => scene.setTileScale(v),
    fmt: (v) => (Math.abs(v - 1) < 0.05 ? "automatisch" : `${v.toFixed(1)}× davon`),
  },
];

function buildControls() {
  const box = $("atlas-controls");
  box.innerHTML = KNOBS.map((k) => `
    <label title="${escapeHtml(k.hint)}">
      <span>${k.label}<em id="k-${k.id}-out">${k.fmt(k.start)}</em></span>
      <input type="range" id="k-${k.id}" min="${k.min}" max="${k.max}" step="${k.step}" value="${k.start}">
    </label>`).join("") + `<button class="chip" id="k-reset">zurücksetzen</button>`;

  for (const k of KNOBS) {
    const el = $(`k-${k.id}`);
    el.oninput = () => {
      const v = Number(el.value);
      $(`k-${k.id}-out`).textContent = k.fmt(v);
      k.apply(v);
    };
  }
  $("k-reset").onclick = () => {
    for (const k of KNOBS) {
      $(`k-${k.id}`).value = k.start;
      $(`k-${k.id}-out`).textContent = k.fmt(k.start);
      k.apply(k.start);
    }
  };
}

function applyFilters() {
  const m = mask();
  scene.setMask(m);
  // Ausgewaehltes, das gerade weggefiltert wurde, gehoert nicht mehr dazu --
  // sonst faerbt eine Aktion Fotos, die niemand sieht.
  if (selection.size) {
    selection = new Set([...selection].filter((i) => m[i]));
    scene.setSelection(selection);
    paintSelection();
  }
  updateCount(m);
}

function updateCount(m) {
  if (scene.mode === "serien") {
    const n = Number($("atlas-minsize").value);
    const sel = model.events.filter((e) => e.n >= n);
    $("atlas-count").textContent =
      `${num(sel.length)} Serien · ${num(sel.reduce((a, e) => a + e.n, 0))} Fotos`;
    return;
  }
  const shown = countVisible(m || mask());
  $("atlas-count").textContent = hidden.size
    ? `${num(shown)} sichtbar · ${num(hidden.size)} weggeräumt`
    : `${num(shown)} sichtbar`;
}

/* Was der letzte Durchlauf gekostet hat.

   Die Zahl der gezeichneten Bilder gegen die der sichtbaren ist der ganze
   Unterschied zwischen den Modi -- und die Millisekunden sagen, ob der
   naechste Schritt noch bezahlbar ist. Der Zwischenspeicher steht daneben,
   weil er nie geleert wird: ein 160er Vorschaubild belegt entpackt 100 kB,
   ein 320er 400 kB. Auf dem Rechner egal, auf dem Handy nicht. */
let statsTimer = null;

function pollStats() {
  clearInterval(statsTimer);
  statsTimer = null;
  // Bewusst neben dem Zeichenlauf und nicht darin: eine Messung, die im
  // gemessenen Durchlauf steckt, misst sich selbst mit.
  if (statsOn) statsTimer = setInterval(paintStats, 250);
}

function paintStats() {
  const box = $("atlas-stats");
  if (!box || !scene || !scene.stats) return;
  const s = scene.stats();
  const speicher = Math.round(s.cached * 0.1);   // grob, 160 px entpackt
  box.classList.toggle("hidden", !statsOn);
  if (!statsOn) return;
  const je = s.perThumb ? ` (${(s.perThumb * 1000).toFixed(0)} µs je Bild)` : "";
  const budget = s.budget === Infinity
    ? "ohne Schranke" : `Budget ${num(s.budget)}${je}`;
  const was = s.off
    ? `<b>keine Bilder</b> — ${escapeHtml(s.off)}`
    : `<b>${num(s.drawn)}</b> von ${num(s.visible)} sichtbaren gezeichnet`
      + ` · Kachel ${num(s.tile)} px (${s.tileAuto}× Zoom)`
      + ` · ${budget}${s.gap ? ` · ${s.gap} px Abstand${
            s.gapReason ? ` (${escapeHtml(s.gapReason)})` : ""}` : ""}`
      + (s.shift ? ` · bis ${s.shift} px verschoben` : "");
  const teile = [];
  if (s.imgMs) teile.push(`${s.imgMs} ms zeichnen`);
  if (s.fanMs) teile.push(`${s.fanMs} ms schieben`);
  box.innerHTML = `${was} · ${s.ms} ms`
    + (teile.length ? ` (${teile.join(", ")})` : "")
    + ` · ${num(s.cached)} Bilder im Speicher (~${num(speicher)} MB)`
    + (s.queued ? ` · ${num(s.queued)} warten` : "");
}

function paintLegend() {
  const items = legendFor(model, colorMode);
  $("atlas-legend").innerHTML = items.map((it) =>
    `<span class="lg"><i style="background:${it.color}"></i>${escapeHtml(it.label)}</span>`
  ).join("");
  $("atlas-legend").classList.toggle("hidden", !items.length);
}

/* ---- Einstieg ---------------------------------------------------------
   Eine Karte, die beim Oeffnen nichts vorschlaegt, ist ein Poster. Der
   Serien-Tab sagt „251 unbenannte Serien" und man weiss, wo man anfaengt.
   Hier steht dieselbe Art Satz -- gerechnet aus dem, was die Karte ohnehin
   weiss. */

/* Was ist hier noch offen?

   Die Karte weiss es und soll es sagen. Frueher stand hier nur eine Zahl --
   "N Fotos noch unberuehrt" -- und darunter die Kontinente mit den meisten
   davon. Nach dem Caption-Lauf sind es drei, und der Kasten meldete
   dreimal nichts und verdeckte dabei die Werkzeugleiste.

   Die Arbeit ist ja nicht weg, sie hat nur die Form gewechselt: 3.000 Fotos
   zeigen Gesichter ohne Namen, Tausende haben ein geratenes Datum. Also
   werden mehrere Sorten gezaehlt und die groesste zuerst genannt -- jede
   mit einem Knopf, der genau diese Fotos auf der Karte auswaehlt. */

const JOBS = [
  {
    id: "unberuehrt", label: "ganz unberührt",
    hint: "ohne Person, ohne Beschreibung, ohne Serie, Datum geraten",
    pick: (i) => tidiness(model.fl[i]) === 0,
  },
  {
    id: "gesichter", label: "Gesichter ohne Namen",
    hint: "jemand ist drauf, aber niemand weiß wer",
    pick: (i) => model.fl[i] & FLAG.FACES_UNNAMED,
  },
  {
    id: "beschreibung", label: "ohne Beschreibung",
    hint: "kein Satz, der sie in der Suche findbar macht",
    pick: (i) => !(model.fl[i] & FLAG.CAPTION),
  },
  {
    id: "datum", label: "Datum nicht aus der Kamera",
    hint: "geschätzt aus Dateiname, Ordner oder Nachbarn — oft trotzdem richtig",
    pick: (i) => !(model.fl[i] & FLAG.EXIF_DATE),
  },
  {
    // Die Flagge steht fuer *benannt*, nicht fuer *vorhanden*. Fast jedes
    // Foto gehoert zu einer Serie; die wenigsten Serien haben einen Namen.
    id: "serie", label: "in keiner benannten Serie",
    hint: "gehören zu einer Gelegenheit, die noch keinen Namen hat",
    pick: (i) => !(model.fl[i] & FLAG.EVENT),
  },
];

//: Unter so vielen offenen Fotos lohnt der Weg nicht.
const WORTH_A_TRIP = 25;

function briefingJobs() {
  const m = mask();
  const found = JOBS.map((j) => ({ ...j, ids: [] }));
  for (let i = 0; i < model.n; i++) {
    if (!m[i]) continue;
    for (const j of found) if (j.pick(i)) j.ids.push(i);
  }
  return found.filter((j) => j.ids.length).sort((a, b) => b.ids.length - a.ids.length);
}

function paintBriefing({ onlyIfWork = false } = {}) {
  const box = $("atlas-brief");
  const jobs = briefingJobs();
  if (onlyIfWork && !jobs.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");

  // Die groesste Sorte bekommt zusaetzlich die Kontinente mit den meisten
  // davon -- dort anzufangen spart den Weg.
  const top = jobs[0];
  const perCluster = new Map();
  if (top) for (const i of top.ids) perCluster.set(model.cl[i], (perCluster.get(model.cl[i]) || 0) + 1);
  const orte = top
    ? model.clusters
        .filter((c) => (perCluster.get(c.i) || 0) >= WORTH_A_TRIP)
        .sort((a, b) => (perCluster.get(b.i) || 0) - (perCluster.get(a.i) || 0))
        .slice(0, 4)
    : [];

  box.innerHTML = `
    <h2>Was ist noch offen?</h2>
    ${jobs.length ? `
      <ul class="brief-jobs">${jobs.map((j) => `
        <li><button data-job="${j.id}" title="${escapeHtml(j.hint)}">
          <b>${num(j.ids.length)}</b> <span>${escapeHtml(j.label)}</span>
        </button></li>`).join("")}</ul>
      ${orte.length ? `
        <p class="muted brief-where">Am meisten ${escapeHtml(top.label)} liegt hier:</p>
        <ul>${orte.map((c) => `
          <li><button data-go="${c.i}">
            <img src="${thumbUrl(c.cover, 160)}" alt="">
            <span><b>${escapeHtml(model.clusterLabel[c.i])}</b>
            <em>${num(perCluster.get(c.i) || 0)} von ${num(c.n)}</em></span>
          </button></li>`).join("")}</ul>`
        : `<p class="muted">Kein Kontinent mit mehr als ${WORTH_A_TRIP} davon —
           was übrig ist, liegt verstreut.</p>`}`
      : `<p class="muted">Nichts offen: jedes sichtbare Foto hat Person,
         Beschreibung, Serie und ein Datum aus der Kamera.</p>`}
    <footer>
      <button class="chip" id="atlas-brief-close">schließen</button>
      <span class="muted">Strg+Klick auf ein Foto: mehr davon</span>
    </footer>`;

  box.onclick = (e) => {
    if (e.target.id === "atlas-brief-close") { box.classList.add("hidden"); return; }

    const jb = e.target.closest("[data-job]");
    if (jb) {
      const j = jobs.find((x) => x.id === jb.dataset.job);
      if (!j) return;
      selection = new Set(j.ids);
      scene.setSelection(selection);
      scene.focusSet([...selection]);
      paintSelection();
      return;
    }

    const b = e.target.closest("[data-go]");
    if (!b || !top) return;
    const c = model.clusters[Number(b.dataset.go)];
    selection = new Set(top.ids.filter((i) => model.cl[i] === c.i));
    scene.setSelection(selection);
    scene.focusCluster(c);
    paintSelection();
    box.classList.add("hidden");
  };
}

/* ---- Schweben ---------------------------------------------------------- */

let hoverIdx = -1;
function showHover(i, sx, sy) {
  const box = $("atlas-hover");
  if (i < 0) { box.classList.add("hidden"); hoverIdx = -1; return; }
  if (i !== hoverIdx) {
    hoverIdx = i;
    const c = model.clusters[model.cl[i]];
    const stack = model.st[i] >= 0;
    const wer = personNames(model, i);
    box.innerHTML = `
      <img src="${thumbUrl(model.ids[i], 160)}" alt="">
      <div>
        <b>${escapeHtml(model.clusterLabel[c.i])}</b>
        ${wer.length ? `<span class="who">${escapeHtml(wer.join(", "))}</span>` : ""}
        <span>${model.year[i] > 0 ? model.year[i] : "ohne Datum"} · ${escapeHtml(model.channels[model.ch[i]])}</span>
        <span class="muted">${stateWords(model.fl[i])}${stack ? " · im Stapel" : ""}</span>
        <span class="muted hint">Strg+Klick: mehr davon</span>
      </div>`;
  }
  box.classList.remove("hidden");
  box.style.left = `${Math.min(sx + 16, $("atlas-canvas").clientWidth - 230)}px`;
  box.style.top = `${Math.min(sy + 16, $("atlas-canvas").clientHeight - 110)}px`;
}

let hoverEv = -1;
function showEventHover(e, sx, sy) {
  const box = $("atlas-hover");
  if (e < 0) { box.classList.add("hidden"); hoverEv = -1; return; }
  if (e !== hoverEv) {
    hoverEv = e;
    const ev = model.events[e];
    const wann = ev.start ? ev.start.slice(0, 10) : "ohne Datum";
    box.innerHTML = `
      <img src="${thumbUrl(ev.cover, 160)}" alt="">
      <div>
        <b>${escapeHtml(ev.name || ev.folder || "ohne Namen")}</b>
        ${ev.people.length ? `<span class="who">${escapeHtml(ev.people.join(", "))}</span>` : ""}
        <span>${wann} · ${num(ev.n)} Fotos · ${escapeHtml(ev.channel)}</span>
        ${ev.spread > 0.18
          ? `<span class="warn">hält inhaltlich nicht zusammen — eher ein Tag als eine Gelegenheit</span>`
          : `<span class="muted hint">Klick: ansehen · Strg+Klick: auswählen</span>`}
      </div>`;
  }
  box.classList.remove("hidden");
  box.style.left = `${Math.min(sx + 16, $("atlas-canvas").clientWidth - 250)}px`;
  box.style.top = `${Math.min(sy + 16, $("atlas-canvas").clientHeight - 120)}px`;
}

function openEvent(e, select) {
  const idx = photosOfEvent(model, e, mask());
  if (!idx.length) return;
  if (select) {
    // Strg: die Serie wird zur Auswahl -- ab hier greifen Notiz, Beschreibung
    // und „Mehr davon" auf die ganze Gelegenheit.
    selection = new Set(idx);
    scene.setSelection(selection);
    paintSelection();
    return;
  }
  showLightbox(idx.map((k) => ({ id: model.ids[k] })), 0);
}

function stateWords(f) {
  const have = [];
  if (f & FLAG.PERSON) have.push("Person");
  if (f & FLAG.CAPTION) have.push("Beschreibung");
  if (f & FLAG.EVENT) have.push("Serie");
  if (f & FLAG.EXIF_DATE) have.push("EXIF-Datum");
  return have.length ? have.join(" · ") : "noch unberührt";
}

/* ---- Auswahl ----------------------------------------------------------- */

/* Was ein Lassozug tut.

   Vorher hat er immer hinzugefuegt. Bei einer bestehenden Auswahl war damit
   alles im Lasso schon gewaehlt, sichtbar aenderte sich nichts -- es sah aus,
   als ginge das Lasso nicht.

   Jetzt gilt, was man aus Bildbearbeitung kennt:

     Zug          ersetzt die Auswahl
     Strg/Cmd     nimmt dazu
     Alt          zieht ab -- das Negativ-Lasso
     Strg+Alt     grenzt ein: nur was im Lasso liegt *und* schon gewaehlt
                  war. Dafuer gibt es keine Entsprechung in Bildprogrammen,
                  hier aber den Anlass: aus einer Auswahl eine Teilgruppe
                  herausgreifen, ohne die Nachbarn aus anderen Kontinenten
                  mitzunehmen, die der Zug streift.

   Jeder Zug sagt hinterher, was er getan hat. Ohne das ist "ersetzen" von
   "nichts getroffen" nicht zu unterscheiden -- und beides sieht wie ein
   Fehler aus.

   Eingrenzen und Abziehen koennen eine Auswahl leeren. Das waere ein Zug,
   der stillschweigend die ganze Arbeit wegwirft; also passiert dann nichts,
   und der Kasten sagt warum. */
let lassoHint = "";

function applyLasso(hit, mods = {}) {
  const { subtract = false, add = false } = mods;
  const vorher = selection.size;

  if (subtract && add) {
    const kept = new Set([...hit].filter((i) => selection.has(i)));
    if (!kept.size) {
      lassoHint = "Nichts eingegrenzt — im Lasso lag keines der gewählten Fotos.";
    } else {
      selection = kept;
      lassoHint = `Eingegrenzt: ${num(kept.size)} von ${num(vorher)}.`;
    }
  } else if (subtract) {
    for (const i of hit) selection.delete(i);
    lassoHint = vorher === selection.size
      ? "Nichts abgezogen — im Lasso lag keines der gewählten Fotos."
      : `${num(vorher - selection.size)} abgezogen, ${num(selection.size)} übrig.`;
  } else if (add) {
    for (const i of hit) selection.add(i);
    lassoHint = selection.size === vorher
      ? "Nichts dazugekommen — alles im Lasso war schon gewählt."
      : `${num(selection.size - vorher)} dazugenommen.`;
  } else {
    if (!hit.size) {
      lassoHint = vorher
        ? "Im Lasso lag kein Foto — Auswahl unverändert."
        : "Im Lasso lag kein Foto.";
    } else {
      selection = new Set(hit);
      lassoHint = vorher
        ? `${num(hit.size)} gewählt (die vorherigen ${num(vorher)} ersetzt). `
          + "Strg hält die alte Auswahl, Strg+Alt grenzt darin ein."
        : `${num(hit.size)} gewählt.`;
    }
  }

  scene.setSelection(selection);
  paintSelection();
}

/** Ganzen Kontinent wählen — die genaue Alternative zum Lasso.

    Wo die Ränder ineinander sprenkeln, erwischt ein gezogener Kreis immer zu
    viel oder zu wenig. Der Kontinent ist dagegen exakt definiert: er kommt aus
    demselben k-means, das auch seinen Namen trägt. */
function pickCluster(c, add) {
  const idx = photosOfCluster(model, c, mask());
  if (!add) selection = new Set(idx);
  else for (const i of idx) selection.add(i);
  scene.setSelection(selection);
  paintSelection();
}

/** Auswahl auf eine Teilmenge eindampfen. */
function refine(keep) {
  selection = new Set([...selection].filter(keep));
  scene.setSelection(selection);
  paintSelection();
}

function clearSelection() {
  const who = $("atlas-who");
  if (who) who.value = "";
  history.length = 0;
  selection = new Set();
  scene.setSelection(selection);
  paintSelection();
}

function selectedIds() {
  return [...selection].map((i) => model.ids[i]);
}

function paintSelection() {
  const panel = $("atlas-sel");
  // Einmal zeigen, dann vergessen: er gehoert zum letzten Lassozug, nicht
  // zur Auswahl. Sonst steht er noch da, wenn man laengst weitergeklickt hat.
  const hinweis = lassoHint;
  lassoHint = "";
  if (!selection.size) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");

  const list = [...selection];
  const withPerson = list.filter((i) => model.fl[i] & FLAG.PERSON).length;
  const withCap = list.filter((i) => model.fl[i] & FLAG.CAPTION).length;
  const untouched = list.filter((i) => tidiness(model.fl[i]) === 0).length;
  const years = new Set(list.map((i) => model.year[i]).filter((y) => y > 0));
  const chans = [...new Set(list.map((i) => model.channels[model.ch[i]]))];
  const strip = list.slice(0, 10);

  // Aus wie vielen Kontinenten stammt die Auswahl? Ein Lasso erwischt fast
  // immer mehrere -- dann soll man sie eindampfen koennen, statt neu zu ziehen.
  const perCluster = new Map();
  for (const i of list) perCluster.set(model.cl[i], (perCluster.get(model.cl[i]) || 0) + 1);
  const ranked = [...perCluster.entries()].sort((a, b) => b[1] - a[1]);
  const [topCluster, topCount] = ranked[0];

  panel.innerHTML = `
    <header>
      <b>${num(selection.size)} Fotos</b>
      <button class="link" id="atlas-clear">leeren</button>
    </header>
    ${hinweis ? `<p class="atlas-lasso-hint">${escapeHtml(hinweis)}</p>` : ""}
    <div class="atlas-strip">
      ${strip.map((i) => `<img src="${thumbUrl(model.ids[i], 160)}" alt="">`).join("")}
      ${selection.size > strip.length ? `<span class="more">+${num(selection.size - strip.length)}</span>` : ""}
    </div>
    <dl>
      <dt>Jahre</dt><dd>${years.size ? `${Math.min(...years)}–${Math.max(...years)}` : "—"}</dd>
      <dt>Herkunft</dt><dd>${escapeHtml(chans.join(", "))}</dd>
      <dt>Kontinente</dt><dd>${num(ranked.length)}</dd>
      <dt>mit Person</dt><dd>${num(withPerson)}</dd>
      <dt>mit Beschreibung</dt><dd>${num(withCap)}</dd>
      <dt>unberührt</dt><dd>${num(untouched)}</dd>
    </dl>
    <div class="atlas-refine">
      ${ranked.length > 1
        ? `<button data-only="cluster">nur „${escapeHtml(model.clusterLabel[topCluster])}" (${num(topCount)})</button>` : ""}
      <button data-all="cluster">ganzer Kontinent „${escapeHtml(model.clusterLabel[topCluster])}" (${num(model.clusters[topCluster].n)})</button>
      ${untouched && untouched < selection.size
        ? `<button data-only="open">nur unberührte (${num(untouched)})</button>` : ""}
    </div>
    <div class="atlas-actions">
      <button id="atlas-more" class="primary-action">Mehr davon →</button>
      ${history.length ? `<button id="atlas-back">← zurück zum vorigen Stand</button>` : ""}
      <button id="atlas-show">Diaschau</button>
      <button id="atlas-note">Notiz anhängen …</button>
      <button id="atlas-cap">Beschreibung setzen …</button>
      <button id="atlas-move" class="danger-action">In eigenen Ordner legen …</button>
      <button id="atlas-trash" class="danger-action">In den Papierkorb</button>
    </div>
    <label class="atlas-reembed">
      <input type="checkbox" id="atlas-reembed"> Textvektoren sofort neu rechnen
      <span class="muted">belegt die GPU — sonst greift die Notiz zwar als Filter, aber noch nicht in der Rangfolge</span>
    </label>
    <p class="gate hidden" id="atlas-reembed-gate"></p>
    <p class="atlas-note" id="atlas-msg"></p>`;

  gate("reembed", $("atlas-reembed"), $("atlas-reembed-gate"));
  $("atlas-clear").onclick = clearSelection;
  $("atlas-more").onclick = () => followThread([...selection]);
  if ($("atlas-back")) $("atlas-back").onclick = threadBack;
  $("atlas-show").onclick = () => showLightbox(selectedIds().map((id) => ({ id })), 0);
  $("atlas-note").onclick = addNote;
  $("atlas-cap").onclick = setCaption;
  $("atlas-move").onclick = moveToFolder;
  $("atlas-trash").onclick = toTrash;
  panel.querySelector(".atlas-refine").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.only === "cluster") refine((i) => model.cl[i] === topCluster);
    else if (b.dataset.only === "open") refine((i) => tidiness(model.fl[i]) === 0);
    else if (b.dataset.all === "cluster") pickCluster(topCluster, false);
  };
}

/* ---- Wegräumen --------------------------------------------------------
   Screenshots und Dokumente sind ein eigenes Thema und gehören nicht in
   dieselbe Sammlung wie die Fotos von Menschen. Löschen wäre zu viel; sie in
   einen eigenen Ordner zu legen ist genau richtig — danach fallen sie aus der
   Bibliothek heraus und aus der Karte auch.

   Zwei Schritte, weil das Verschieben Dateien anfasst: erst der Plan, dann
   die Bestätigung. Bei zweieinhalbtausend Dateien ist das kein Komfort. */

/** Ordnername oder ganzer Pfad. Ein Name landet im Bibliotheksordner,
    ein absoluter Pfad bestimmt das Ziel genau. */
function splitDest(input) {
  const value = input.trim().replace(/[/\\]+$/, "");
  if (!value.startsWith("/")) return { folder_name: value };
  const cut = value.lastIndexOf("/");
  return { dest_parent: value.slice(0, cut) || "/", folder_name: value.slice(cut + 1) };
}

/** Ordnername aus dem, was ausgewählt ist.

    Der Kontinentname kommt aus den Captions und beschreibt genau das, was in
    der Auswahl steckt: „Screenshot · anzeigt" wird zu „Screenshots".
    Zusammengesetzte Namen taugen nicht als Ordner, deshalb nur das erste
    Wort, groß und im Plural. */
function suggestFolder() {
  const perCluster = new Map();
  for (const i of selection) perCluster.set(model.cl[i], (perCluster.get(model.cl[i]) || 0) + 1);
  const top = [...perCluster.entries()].sort((a, b) => b[1] - a[1])[0];
  const word = model.clusters[top[0]].terms[0] || "Aussortiert";
  const name = word.charAt(0).toUpperCase() + word.slice(1);
  return /(s|en|er)$/.test(name) ? name : `${name}s`;
}

/** Welche Bereiche gibt es schon? Sie sind die naheliegenden Ziele.

    Wo die Fotos gerade *herkommen*, ist als Ziel unbrauchbar -- steht aber
    trotzdem in der Liste, weil „innerhalb desselben Dumps umsortieren" ein
    legitimer Wunsch ist. Es ist nur nie die Vorauswahl. */
function targetChoices(name) {
  // Wie viele der Ausgewählten liegen schon in diesem Bereich? „Ist Herkunft
  // ja/nein" war unbrauchbar: in einem Screenshot-Kontinent liegt irgendein
  // Foto in jedem Bereich, und dann stand an jedem Ziel dasselbe Etikett.
  const from = new Map();
  for (const i of selection) from.set(model.sp[i], (from.get(model.sp[i]) || 0) + 1);
  const total = selection.size || 1;
  const out = model.spaces.map((space, i) => ({
    space,
    path: `${model.root}/${space}/${name}`,
    shown: !filters.spacesOff.has(i),
    here: from.get(i) || 0,
    // Als Herkunft gilt, wo die Mehrheit schon liegt -- dorthin zu verschieben
    // ist meist keine Ordnung, sondern eine Verschiebung im Kreis.
    source: (from.get(i) || 0) / total > 0.5,
  }));
  if (!model.spaces.includes("Sonstiges")) {
    out.push({
      space: "Sonstiges", path: `${model.root}/Sonstiges/${name}`,
      shown: false, here: 0, source: false, isNew: true,
    });
  }
  return out;
}

async function moveToFolder() {
  const ids = selectedIds();
  const name = suggestFolder();
  const choices = targetChoices(name);
  // Vorauswahl in dieser Reihenfolge: ein Bereich, der schon ausgeblendet ist
  // (dann ist die Wirkung sofort da), sonst „Sonstiges", sonst irgendein
  // Bereich, aus dem die Auswahl *nicht* stammt.
  const preferred = choices.find((c) => !c.shown && !c.source)
    || choices.find((c) => c.space === "Sonstiges")
    || choices.find((c) => !c.source)
    || choices[0];
  let target = preferred.path;

  const dlg = openModal({
    title: `${num(ids.length)} Fotos verschieben`,
    lead: "Die Dateien werden <b>bewegt, nicht kopiert</b>. Der Ordnername kommt "
        + "aus den Bildbeschreibungen der Auswahl.",
    body: `
      <div class="mv-choices">
        ${choices.map((c) => `
          <button type="button" class="mv-choice${c.path === target ? " on" : ""}" data-path="${c.path}">
            <b>${c.space}${c.isNew ? " <em>(neu)</em>" : ""}</b>
            <span>${c.path}</span>
            <i>${[
              c.here ? `${num(c.here)} der Auswahl liegen schon hier` : "",
              c.shown ? "sichtbar" : "ausgeblendet",
            ].filter(Boolean).join(" · ")}</i>
          </button>`).join("")}
      </div>
      <div class="mv-path" id="mv-path"></div>
      <label class="mv-hide" id="mv-hide-row">
        <input type="checkbox" id="mv-hide"> Zielbereich danach ausblenden
        <span>ein Klick in der Leiste nimmt ihn wieder herein</span>
      </label>
      <p class="mv-plan" id="mv-plan"></p>`,
    buttons: [
      { id: "cancel", label: "Abbrechen" },
      { id: "go", label: "Verschieben", kind: "danger" },
    ],
  });

  /** Bereich des gerade gewählten Ziels, oder -1 wenn er noch nicht existiert. */
  const targetSpace = () => {
    if (!target.startsWith(model.root)) return -1;
    const seg = target.slice(model.root.length).replace(/^\/+/, "").split("/")[0];
    return model.spaces.indexOf(seg);
  };

  const syncHideRow = () => {
    const i = targetSpace();
    const row = $("mv-hide-row");
    // Einen Bereich ausblenden, der schon aus ist, ist keine Wahl.
    const useful = i < 0 || !filters.spacesOff.has(i);
    row.classList.toggle("hidden", !useful);
    if (!useful) $("mv-hide").checked = false;
  };

  const pick = createPathPick($("mv-path"), target, (v) => {
    target = v;
    dlg.root.querySelectorAll(".mv-choice").forEach((b) =>
      b.classList.toggle("on", b.dataset.path === v));
    $("mv-plan").textContent = "";
    syncHideRow();
    plan();
  });
  dlg.root.querySelector(".mv-choices").onclick = (e) => {
    const b = e.target.closest("[data-path]");
    if (b) pick.set(b.dataset.path);
  };
  syncHideRow();

  // Trockenlauf, sobald der Dialog steht und bei jeder Pfadänderung: die Zahl
  // im Dialog ist dann die echte, nicht die erhoffte. Als
  // Funktionsdeklaration, damit die Pfadwahl oben sie schon kennt.
  async function plan() {
    const mine = target;
    try {
      const p = await api("/api/photos/relocate", {
        method: "POST",
        body: JSON.stringify({ photo_ids: ids, ...splitDest(mine), confirm: false }),
      });
      if (mine !== target) return;   // inzwischen woanders hin gewählt
      const skipped = p.skipped?.length || 0;
      $("mv-plan").textContent =
        `${num(p.photos)} Dateien wandern nach ${p.dest}`
        + (skipped ? ` · ${num(skipped)} bleiben liegen (schon dort oder Datei fehlt)` : "");
    } catch (e) {
      if (mine === target) $("mv-plan").textContent = `Geht nicht: ${e.message}`;
    }
  }
  plan();

  // Referenz vor dem Warten festhalten: `close()` raeumt den Dialoginhalt aus
  // dem Dokument, das Element selbst behaelt seinen Zustand aber.
  const hideBox = $("mv-hide");
  const answer = await dlg.wait();
  if (answer !== "go") return;
  const hideAfter = !!hideBox?.checked;
  const hideSpace = targetSpace();
  const chosen = splitDest(target);
  if (!chosen.folder_name) return;

  const msg = $("atlas-msg");
  msg.textContent = "verschiebt …";
  try {
    const res = await api("/api/photos/relocate", {
      method: "POST",
      body: JSON.stringify({
        photo_ids: ids,
        ...chosen,
        confirm: true,
        reembed: $("atlas-reembed").checked,
      }),
    });
    // Ob sie von der Karte verschwinden, entscheidet das Ziel -- nicht die
    // Handlung. Bleibt der Bereich sichtbar, bleiben auch die Fotos: sie sind
    // nur woanders einsortiert.
    const bereich = (res.dest || "").startsWith(model.root)
      ? res.dest.slice(model.root.length).replace(/^\/+/, "").split("/")[0]
      : "";
    // Wollte der Mensch den Zielbereich loswerden, wird er jetzt ausgeblendet
    // -- der Schalter in der Leiste nimmt ihn mit einem Klick wieder herein.
    if (hideAfter && hideSpace >= 0) {
      filters.spacesOff.add(hideSpace);
      const chip = document.querySelector(`#atlas-spaces [data-space="${hideSpace}"]`);
      if (chip) chip.classList.remove("on");
    }
    const stays = model.spaces.some(
      (space, i) => space === bereich && !filters.spacesOff.has(i),
    );
    if (!stays) {
      for (const id of ids) hidden.add(id);
      saveHidden();
    }
    clearSelection();
    applyFilters();
    $("atlas-msg").textContent =
      `${num(res.migrated ?? 0)} verschoben nach ${res.dest}.`
      + (res.failed?.length ? ` ${num(res.failed.length)} fehlgeschlagen.` : "")
      + (stays
        ? ` Bereich „${bereich}" bleibt sichtbar — beim nächsten atlas_build stehen sie dort.`
        : ` Bereich „${bereich}" ausgeblendet — der Schalter in der Leiste holt ihn zurück.`);
  } catch (e) {
    msg.textContent = `Verschieben fehlgeschlagen: ${e.message}`;
  }
}

/** Einen Text erfragen -- im eigenen Dialog, nicht per Systemabfrage.

    `prompt()` sieht auf dem Handy aus wie eine Warnung der Website und ist in
    manchen eingebetteten Browsern ganz gesperrt. */
async function askText({ title, lead, placeholder, ok, rows = 2 }) {
  const dlg = openModal({
    title, lead,
    body: `<textarea class="pv-text" id="pv-text" rows="${rows}"
            placeholder="${placeholder}" spellcheck="false"></textarea>`,
    buttons: [
      { id: "cancel", label: "Abbrechen" },
      { id: "ok", label: ok, kind: "primary" },
    ],
  });
  const field = $("pv-text");
  field.focus();
  field.onkeydown = (e) => {
    // Eingabetaste bestätigt, Umschalt+Eingabe macht einen Absatz.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      dlg.root.querySelector('[data-modal="ok"]').click();
    }
  };
  const answer = await dlg.wait();
  const text = (answer === "ok" ? field.value : "").trim();
  return text || null;
}

async function addNote() {
  const text = await askText({
    title: `Notiz für ${num(selection.size)} Fotos`,
    lead: "Wird als Schlagwort gespeichert und ist danach exakt filterbar.",
    placeholder: "z. B. Stripclub, Omas Garten",
    ok: "Anhängen",
  });
  if (!text) return;
  await run(`/api/photos/annotate`, {
    photo_ids: selectedIds(),
    annotations: [text],
    mode: "add",
    reembed: $("atlas-reembed").checked,
  }, (r) => `${num(r.changed)} Fotos notiert${r.reembedded ? `, ${num(r.reembedded)} neu eingebettet` : ""}.`);
}

async function setCaption() {
  const text = await askText({
    title: `Beschreibung für ${num(selection.size)} Fotos`,
    lead: "Überschreibt vorhandene Beschreibungen und wird gegen neue "
        + "Vision-Läufe <b>gesperrt</b>.",
    placeholder: "Ein Satz, der für alle ausgewählten Fotos gilt",
    ok: "Setzen",
    rows: 3,
  });
  if (!text) return;
  await run(`/api/photos/caption/bulk`, {
    photo_ids: selectedIds(),
    caption_de: text,
    lock: true,
  }, (r) => `${num(r.updated)} Fotos beschriftet.`);
}

async function run(path, body, done) {
  const msg = $("atlas-msg");
  msg.textContent = "läuft …";
  try {
    const res = await api(path, { method: "POST", body: JSON.stringify(body) });
    msg.textContent = `${done(res)} Die Karte zeigt es nach dem nächsten \`atlas_build\`.`;
  } catch (e) {
    msg.textContent = `Fehlgeschlagen: ${e.message}`;
  }
}

/** Zur Löschung vormerken. Fasst keine Datei an — das tut erst der Papierkorb. */
async function toTrash() {
  const ids = selectedIds();
  const dlg = openModal({
    title: `${num(ids.length)} Fotos in den Papierkorb`,
    lead: "Es wird <b>nichts angefasst</b> — nur vermerkt. Im Reiter "
        + "<em>Papierkorb</em> kannst du sie einzeln retten oder alle endgültig "
        + "löschen. Von der Karte sind sie sofort weg.",
    buttons: [{ id: "cancel", label: "Abbrechen" },
              { id: "go", label: "Vormerken", kind: "primary" }],
  });
  if (await dlg.wait() !== "go") return;
  const msg = $("atlas-msg");
  msg.textContent = "merkt vor …";
  try {
    const res = await api("/api/trash", {
      method: "POST", body: JSON.stringify({ photo_ids: ids, trashed: true }),
    });
    // Sofort von der Karte nehmen: sie ist ein Standbild und wuerde sie sonst
    // bis zum naechsten atlas_build weiter anbieten.
    for (const id of ids) hidden.add(id);
    saveHidden();
    clearSelection();
    applyFilters();
    $("atlas-msg").textContent =
      `${num(res.trashed)} vorgemerkt. Im Reiter Papierkorb rettbar oder endgültig löschbar.`;
  } catch (e) {
    msg.textContent = `Vormerken fehlgeschlagen: ${e.message}`;
  }
}

/* ---- Der Faden --------------------------------------------------------
   Was eine Karte von einem Poster unterscheidet: man kann etwas verfolgen.
   Die Auswahl wird zur Abfrage, Qdrant antwortet mit den Nachbarn, die
   Kamera faehrt hin. Von dort weiter -- so entsteht aus einem Becken ein
   Netz, in dem man sich bewegt. */

//: Zurueck zum vorigen Stand. Einen Faden aufzunehmen darf nicht bedeuten,
//: dass die vorherige Auswahl weg ist -- sonst traut man sich nicht.
const history = [];

async function followThread(seedIndices, opts = {}) {
  const msg = $("atlas-msg");
  if (msg) msg.textContent = "sucht Ähnliches …";
  const ids = seedIndices.map((i) => model.ids[i]);
  let res;
  try {
    res = await api("/api/photos/similar", {
      method: "POST",
      body: JSON.stringify({
        photo_ids: ids,
        using: opts.using || "clip",
        strategy: seedIndices.length > 1 ? "average" : "best",
        limit: opts.limit || 200,
      }),
    });
  } catch (e) {
    if (msg) msg.textContent = `Ähnlichkeitssuche fehlgeschlagen: ${e.message}`;
    return;
  }

  const m = mask();
  const hit = new Set(seedIndices.filter((i) => m[i]));
  let unbekannt = 0;
  for (const r of res.results) {
    const i = model.indexOfId.get(r.id);
    // Die Karte kann aelter sein als der Index -- neu Hinzugekommenes hat
    // hier noch keinen Platz. Das sagen wir, statt es zu verschweigen.
    if (i === undefined) { unbekannt++; continue; }
    if (m[i]) hit.add(i);
  }
  history.push(new Set(selection));
  selection = hit;
  scene.setSelection(selection);
  scene.focusSet([...selection]);
  paintSelection();
  const note = $("atlas-msg");
  if (note) {
    note.textContent = `${num(selection.size)} ähnliche Fotos`
      + (unbekannt ? ` · ${num(unbekannt)} noch nicht auf der Karte (atlas_build neu laufen lassen)` : "");
  }
}

function threadBack() {
  const prev = history.pop();
  if (!prev) return;
  selection = prev;
  scene.setSelection(selection);
  if (selection.size) scene.focusSet([...selection]);
  paintSelection();
}

/* ---- Oeffnen ----------------------------------------------------------- */

function openAt(i) {
  // Wenn eine Auswahl steht, ist sie die Diaschau -- sonst der Kontinent,
  // auf den geklickt wurde. Ein einzelnes Foto allein zu oeffnen waere ein
  // Rueckschritt hinter das, was die Karte gerade zeigt.
  const pool = selection.size
    ? [...selection]
    : indicesOfCluster(model.cl[i]);
  const at = Math.max(0, pool.indexOf(i));
  showLightbox(pool.map((k) => ({ id: model.ids[k] })), at);
}

function indicesOfCluster(c) {
  const out = [];
  const m = mask();
  for (let k = 0; k < model.n; k++) if (m[k] && model.cl[k] === c) out.push(k);
  return out;
}

function onKey(e) {
  if ($("view-atlas").classList.contains("hidden")) return;
  if (e.key === "Escape" && selection.size) { clearSelection(); e.stopPropagation(); }
  if (e.key === "0") scene.fitAll();
}
