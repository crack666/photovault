/* Der Tab „Atlas": Werkzeugleiste, Auswahl, Aktionen.

   Was diese Ansicht von einer Kachelwand unterscheidet, ist nicht das
   Aussehen, sondern dass man auf ihr arbeiten kann: ein Lasso um den
   Screenshot-Kontinent markiert tausend Bilder, und die bekommen in einem
   Zug eine Notiz. Genau das kann der Explorer nicht, weil er Aehnlichkeit
   nicht kennt. */

import { $, escapeHtml, num } from "../core/dom.js?v=2";
import { api, thumbUrl } from "../core/api.js?v=2";
import {
  COLOR_MODES, FILTERS, FLAG, countVisible, legendFor, loadAtlas, personNames,
  photosOfEvent, photosOfPerson, tidiness, visibleMask,
} from "./model.js?v=2";
import { createScene } from "./scene.js?v=2";

const LENSES = [
  { id: "bedeutung", label: "Bedeutung", hint: "Nähe heißt: sieht sich ähnlich" },
  { id: "zeit", label: "Zeit × Bedeutung", hint: "waagerecht die Jahre, senkrecht dieselbe Bedeutungsachse" },
];

let model = null;
let scene = null;
let selection = new Set();
let filters = { fold: false, open: false, camera: false };
let colorMode = "kontinent";
let showLightbox = () => {};
let booted = false;

export async function initAtlas(deps = {}) {
  if (booted) { scene?.resize(); return; }
  showLightbox = deps.showLightbox || showLightbox;

  const status = $("atlas-status");
  status.textContent = "Karte wird geladen …";
  try {
    model = await loadAtlas();
  } catch (err) {
    status.innerHTML = `<pre class="atlas-missing">${escapeHtml(err.message)}</pre>`;
    return;
  }
  status.textContent = "";
  booted = true;

  buildToolbar();
  scene = createScene($("atlas-canvas"), model, {
    onHover: showHover,
    onPick: (i) => openAt(i),
    onThread: (i) => followThread([i]),
    onHoverEvent: showEventHover,
    onPickEvent: openEvent,
    onLasso: (hit, subtract) => applyLasso(hit, subtract),
  });
  applyFilters();
  paintLegend();
  paintBriefing();
  document.addEventListener("keydown", onKey);
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

  $("atlas-colors").innerHTML = "<span class='atlas-label'>Farbe</span>" + COLOR_MODES.map((m, i) =>
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

  $("atlas-filters").innerHTML = FILTERS.map((f) =>
    `<button class="chip" data-filter="${f.id}" title="${escapeHtml(f.hint)}">${f.label}</button>`
  ).join("") + `<button class="chip" id="atlas-lasso" title="Auswahl mit der Maus umkreisen (oder Shift halten)">Lasso</button>`;
  $("atlas-filters").onclick = (e) => {
    const b = e.target.closest("[data-filter]");
    if (b) {
      filters[b.dataset.filter] = !filters[b.dataset.filter];
      b.classList.toggle("on", filters[b.dataset.filter]);
      applyFilters();
      return;
    }
    if (e.target.id === "atlas-lasso") {
      const on = e.target.classList.toggle("on");
      scene.setLassoMode(on);
    }
  };

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

  const who = $("atlas-who");
  const counts = new Map();
  for (let i = 0; i < model.n; i++) for (const k of model.pe[i] || []) counts.set(k, (counts.get(k) || 0) + 1);
  who.innerHTML = "<option value=''>Person hervorheben …</option>" +
    [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) =>
      `<option value="${k}">${escapeHtml(model.persons[k])} — ${num(n)}</option>`
    ).join("");
  who.onchange = () => {
    if (who.value === "") { clearSelection(); return; }
    selection = photosOfPerson(model, Number(who.value), visibleMask(model, filters));
    scene.setSelection(selection);
    paintSelection();
  };

  $("atlas-reset").onclick = () => { scene.fitAll(); clearSelection(); };
  $("atlas-built").textContent =
    `${num(model.n)} Fotos · ${model.clusters.length} Kontinente · ${model.space.toUpperCase()} · ${model.builtAt.slice(0, 10)}`;
}

function applyFilters() {
  const mask = visibleMask(model, filters);
  scene.setMask(mask);
  // Ausgewaehltes, das gerade weggefiltert wurde, gehoert nicht mehr dazu --
  // sonst faerbt eine Aktion Fotos, die niemand sieht.
  if (selection.size) {
    selection = new Set([...selection].filter((i) => mask[i]));
    scene.setSelection(selection);
    paintSelection();
  }
  updateCount(mask);
}

function updateCount(mask) {
  if (scene.mode === "serien") {
    const n = Number($("atlas-minsize").value);
    const sel = model.events.filter((e) => e.n >= n);
    $("atlas-count").textContent =
      `${num(sel.length)} Serien · ${num(sel.reduce((a, e) => a + e.n, 0))} Fotos`;
    return;
  }
  $("atlas-count").textContent = `${num(countVisible(mask || visibleMask(model, filters)))} sichtbar`;
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

//: Unter so vielen offenen Fotos lohnt der Weg nicht.
const WORTH_A_TRIP = 25;

function paintBriefing() {
  const box = $("atlas-brief");
  const untouched = [];
  for (let i = 0; i < model.n; i++) if (tidiness(model.fl[i]) === 0) untouched.push(i);

  const perCluster = new Map();
  for (const i of untouched) perCluster.set(model.cl[i], (perCluster.get(model.cl[i]) || 0) + 1);

  // Nach *Anzahl* sortieren, nicht nach Anteil. Ein Anteilsschwellwert lieferte
  // nach dem Caption-Lauf gar keinen Vorschlag mehr -- die Karte nannte eine
  // Zahl und liess einen stehen. Wo am meisten liegt, lohnt der Weg.
  const offen = model.clusters
    .filter((c) => (perCluster.get(c.i) || 0) >= WORTH_A_TRIP)
    .sort((a, b) => (perCluster.get(b.i) || 0) - (perCluster.get(a.i) || 0))
    .slice(0, 5);

  const faces = [];
  for (let i = 0; i < model.n; i++) if (model.fl[i] & FLAG.FACES_UNNAMED) faces.push(i);

  box.innerHTML = `
    <h2>${num(untouched.length)} Fotos noch unberührt</h2>
    <p class="muted">ohne Person, ohne Beschreibung, ohne Serie, Datum geraten.</p>
    ${offen.length ? `<ul>${offen.map((c) => `
      <li><button data-go="${c.i}">
        <img src="${thumbUrl(c.cover, 160)}" alt="">
        <span><b>${escapeHtml(model.clusterLabel[c.i])}</b>
        <em>${num(perCluster.get(c.i) || 0)} von ${num(c.n)} offen</em></span>
      </button></li>`).join("")}</ul>`
      : `<p class="muted">Kein Kontinent mit mehr als ${WORTH_A_TRIP} offenen Fotos —
         was übrig ist, liegt verstreut.</p>`}
    ${faces.length ? `<p class="brief-next">
      <button data-faces="1">${num(faces.length)} Fotos zeigen Gesichter ohne Namen →</button></p>` : ""}
    <footer>
      <button class="chip" id="atlas-brief-close">schließen</button>
      <span class="muted">Strg+Klick auf ein Foto: mehr davon</span>
    </footer>`;

  box.onclick = (e) => {
    if (e.target.id === "atlas-brief-close") { box.classList.add("hidden"); return; }
    if (e.target.closest("[data-faces]")) {
      // Die naechste Arbeit liegt woanders -- dann soll die Karte dorthin
      // zeigen und nicht selbst so tun, als koennte sie Gesichter benennen.
      selection = new Set(faces.filter((i) => visibleMask(model, filters)[i]));
      scene.setSelection(selection);
      scene.focusSet([...selection]);
      paintSelection();
      box.classList.add("hidden");
      return;
    }
    const b = e.target.closest("[data-go]");
    if (!b) return;
    const c = model.clusters[Number(b.dataset.go)];
    const mask = visibleMask(model, filters);
    selection = new Set(untouched.filter((i) => model.cl[i] === c.i && mask[i]));
    scene.setSelection(selection);
    scene.focusCluster(c);
    paintSelection();
    box.classList.add("hidden");
  };
  box.classList.remove("hidden");
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
  const idx = photosOfEvent(model, e, visibleMask(model, filters));
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

function applyLasso(hit, subtract) {
  if (subtract) for (const i of hit) selection.delete(i);
  else for (const i of hit) selection.add(i);
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
  if (!selection.size) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");

  const list = [...selection];
  const withPerson = list.filter((i) => model.fl[i] & FLAG.PERSON).length;
  const withCap = list.filter((i) => model.fl[i] & FLAG.CAPTION).length;
  const untouched = list.filter((i) => tidiness(model.fl[i]) === 0).length;
  const years = new Set(list.map((i) => model.year[i]).filter((y) => y > 0));
  const chans = [...new Set(list.map((i) => model.channels[model.ch[i]]))];
  const strip = list.slice(0, 10);

  panel.innerHTML = `
    <header>
      <b>${num(selection.size)} Fotos</b>
      <button class="link" id="atlas-clear">leeren</button>
    </header>
    <div class="atlas-strip">
      ${strip.map((i) => `<img src="${thumbUrl(model.ids[i], 160)}" alt="">`).join("")}
      ${selection.size > strip.length ? `<span class="more">+${num(selection.size - strip.length)}</span>` : ""}
    </div>
    <dl>
      <dt>Jahre</dt><dd>${years.size ? `${Math.min(...years)}–${Math.max(...years)}` : "—"}</dd>
      <dt>Herkunft</dt><dd>${escapeHtml(chans.join(", "))}</dd>
      <dt>mit Person</dt><dd>${num(withPerson)}</dd>
      <dt>mit Beschreibung</dt><dd>${num(withCap)}</dd>
      <dt>unberührt</dt><dd>${num(untouched)}</dd>
    </dl>
    <div class="atlas-actions">
      <button id="atlas-more" class="primary-action">Mehr davon →</button>
      ${history.length ? `<button id="atlas-back">← zurück zum vorigen Stand</button>` : ""}
      <button id="atlas-show">Diaschau</button>
      <button id="atlas-note">Notiz anhängen …</button>
      <button id="atlas-cap">Beschreibung setzen …</button>
    </div>
    <label class="atlas-reembed">
      <input type="checkbox" id="atlas-reembed"> Textvektoren sofort neu rechnen
      <span class="muted">belegt die GPU — sonst greift die Notiz zwar als Filter, aber noch nicht in der Rangfolge</span>
    </label>
    <p class="atlas-note" id="atlas-msg"></p>`;

  $("atlas-clear").onclick = clearSelection;
  $("atlas-more").onclick = () => followThread([...selection]);
  if ($("atlas-back")) $("atlas-back").onclick = threadBack;
  $("atlas-show").onclick = () => showLightbox(selectedIds().map((id) => ({ id })), 0);
  $("atlas-note").onclick = addNote;
  $("atlas-cap").onclick = setCaption;
}

async function addNote() {
  const text = prompt(
    `Notiz für ${num(selection.size)} Fotos:\n` +
    `(wird als Schlagwort gespeichert und ist danach exakt filterbar)`,
  );
  if (!text || !text.trim()) return;
  await run(`/api/photos/annotate`, {
    photo_ids: selectedIds(),
    annotations: [text.trim()],
    mode: "add",
    reembed: $("atlas-reembed").checked,
  }, (r) => `${num(r.changed)} Fotos notiert${r.reembedded ? `, ${num(r.reembedded)} neu eingebettet` : ""}.`);
}

async function setCaption() {
  const text = prompt(
    `Beschreibung für ${num(selection.size)} Fotos:\n` +
    `(überschreibt vorhandene und wird gegen neue Vision-Läufe gesperrt)`,
  );
  if (!text || !text.trim()) return;
  await run(`/api/photos/caption/bulk`, {
    photo_ids: selectedIds(),
    caption_de: text.trim(),
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

  const mask = visibleMask(model, filters);
  const hit = new Set(seedIndices.filter((i) => mask[i]));
  let unbekannt = 0;
  for (const r of res.results) {
    const i = model.indexOfId.get(r.id);
    // Die Karte kann aelter sein als der Index -- neu Hinzugekommenes hat
    // hier noch keinen Platz. Das sagen wir, statt es zu verschweigen.
    if (i === undefined) { unbekannt++; continue; }
    if (mask[i]) hit.add(i);
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
  const mask = visibleMask(model, filters);
  const out = [];
  for (let k = 0; k < model.n; k++) if (mask[k] && model.cl[k] === c) out.push(k);
  return out;
}

function onKey(e) {
  if ($("view-atlas").classList.contains("hidden")) return;
  if (e.key === "Escape" && selection.size) { clearSelection(); e.stopPropagation(); }
  if (e.key === "0") scene.fitAll();
}
