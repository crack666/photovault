import { $, escapeHtml, num } from "./core/dom.js?v=55";
import { api, cropUrl } from "./core/api.js?v=55";
import { rememberTab, renderNav, tabFromUrl } from "./core/nav.js?v=55";
import { feature, gate } from "./core/capabilities.js?v=55";
import { refreshEventNames, refreshPersonNames } from "./core/names.js?v=55";
import { bindLightbox, showLightbox } from "./lightbox/index.js?v=55";
import { fillGallery } from "./gallery/index.js?v=55";
import { bindEvents, loadEventTab } from "./events/index.js?v=55";
import { renderPager } from "./core/pager.js?v=55";
import { faceStatsLine } from "./core/format.js?v=55";
import { askConfirm, askText, notify } from "./core/modal.js?v=55";

const state = { clusters: [], index: 0, remaining: 0 };

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $(`view-${name}`).classList.remove("hidden");
  rememberTab(name);
  if (name === "people") loadPeople();
  if (name === "search") loadPersonPicker();
  if (name === "unknown") { loadUnknown(true); loadCandidates(); }
  if (name === "events") loadEventTab();
  if (name === "atlas") openAtlas();
  if (name === "trash") openTrash();
}

/* Der Atlas kommt als eigenes Modul und erst, wenn er gebraucht wird.

   Das `?v=55` an jedem Import ist kein Schmuck: ein `import "./model.js"` ohne
   Parameter liefert aus dem Browser-Cache beliebig lange die alte Fassung,
   auch wenn index.html schon die neue erwartet. Genau das ist passiert. Die
   Zahl gilt fuer alle Module gemeinsam und wird gemeinsam erhoeht.

   Die Grossansicht bekam er frueher hineingereicht, weil sie in app.js lag
   und app.js nichts exportiert. Seit sie ein eigenes Modul ist, importiert
   er sie selbst -- der deps-Umweg ist ersatzlos entfallen. */
async function openAtlas() {
  const { initAtlas } = await import("./atlas/index.js?v=55");
  await initAtlas();
}

let trashBound = false;
async function openTrash() {
  const mod = await import("./trash/index.js?v=55");
  if (!trashBound) { mod.bindTrash(); trashBound = true; }
  await mod.initTrash();
}

/* ---- Unbekannte Gesichter: gezielt aussortieren ----
   Je mehr Personen benannt sind, desto mehr Beifang bleibt übrig. Die
   Cluster-Ansicht arbeitet von vorn; hier sucht man gezielt. */

const ukSel = new Set();
let ukOffset = 0;

let ukCluster = null;

function showUkPane(id) {
  $("uk-overview").classList.toggle("hidden", id !== "uk-overview");
  $("uk-detail").classList.toggle("hidden", id !== "uk-detail");
}

async function loadUnknown(reset) {
  showUkPane("uk-overview");
  const sort = $("uk-sort").value;
  const groups = sort === "groups";
  $("uk-clusters").classList.toggle("hidden", !groups);
  $("uk-faces").classList.toggle("hidden", groups);
  $("uk-name").classList.toggle("hidden", groups);
  $("uk-ignore").classList.toggle("hidden", groups);
  $("uk-none").classList.toggle("hidden", groups);
  $("uk-sel").classList.toggle("hidden", groups);
  $("uk-more").classList.toggle("hidden", groups);

  if (groups) {
    const d = await api("/api/persons/unlabeled?limit=80");
    $("uk-meta").textContent =
      `${d.groups_usable ?? d.clusters.length} Gruppen ab ${d.min_size ?? 3} Gesichtern` +
      (d.remaining ? ` · ${d.clusters.length} gezeigt, +${d.remaining} weitere` : "") +
      faceStatsLine(d.stats);
    $("uk-hint").textContent = (d.stats && d.stats.faces_small)
      ? "Gruppen unter 10 stehen unter „einzeln“. Übersprungene bleiben draußen, bis du sie unter Personen zurückholst."
      : "Eine Karte ist eine vermutete Person. Rein klicken für alle Fotos, dann benennen.";
    const box = $("uk-clusters");
    box.innerHTML = "";
    if (!d.clusters.length) {
      box.innerHTML = "<p class='muted'>Keine unbekannten Gruppen.</p>";
      return;
    }
    d.clusters.forEach((c) => {
      const el = document.createElement("div");
      el.className = "person";
      el.innerHTML = `
        <img class="avatar" src="${cropUrl(c.cover_face_id)}?size=160" alt="" />
        <div class="who">Unbekannt</div>
        <div class="muted">${c.size} Gesicht${c.size === 1 ? "" : "er"}</div>`;
      el.querySelector(".avatar").addEventListener("click", () => openUnknownDetail(c));
      el.querySelector(".who").addEventListener("click", () => openUnknownDetail(c));
      el.querySelector(".who").style.cursor = "pointer";
      box.appendChild(el);
    });
    return;
  }

  if (reset) { ukOffset = 0; ukSel.clear(); $("uk-faces").innerHTML = ""; }
  const d = await api(`/api/persons/unknown?limit=200&offset=${ukOffset}&sort=${sort}`);
  $("uk-meta").textContent =
    `${d.total} unbenannt` + faceStatsLine(d.stats);
  $("uk-hint").textContent = d.has_landmarks
    ? "Bewertet nach Frontalität, Größe und Erkennungssicherheit. Zum Benennen lieber „Personen-Gruppen“."
    : "Hinweis: Für diese Gesichter fehlt noch die Frontalitäts-Messung.";

  const box = $("uk-faces");
  d.faces.forEach((f) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "face";
    const badge = f.frontality != null
      ? `${Math.round(f.frontality * 100)}%`
      : `${Math.round((f.score || 0) * 100)}`;
    el.title = `${f.folder_name || ""} · ${f.size_px}px${f.frontality != null ? ` · frontal ${badge}` : ""}`;
    el.innerHTML = `<img loading="lazy" src="${cropUrl(f.face_id)}?size=160" alt="" /><span>${badge}</span>`;
    el.addEventListener("click", () => {
      ukSel.has(f.face_id) ? ukSel.delete(f.face_id) : ukSel.add(f.face_id);
      el.classList.toggle("on", ukSel.has(f.face_id));
      updateUkSel();
    });
    box.appendChild(el);
  });
  ukOffset += d.returned;
  $("uk-more").classList.toggle("hidden", ukOffset >= d.total);
  updateUkSel();
}

function renderUnknownFaces(faces) {
  const box = $("ukd-faces");
  box.innerHTML = "";
  faces.forEach((f) => {
    const id = f.face_id || f;
    const el = document.createElement("button");
    el.type = "button";
    el.className = "face";
    const pct = f.score == null ? "" : `${Math.round(f.score * 100)}%`;
    el.innerHTML = `<img loading="lazy" src="${cropUrl(id)}?size=160" alt="" /><span>${pct}</span>`;
    el.addEventListener("click", () => {
      ukSel.has(id) ? ukSel.delete(id) : ukSel.add(id);
      el.classList.toggle("on", ukSel.has(id));
    });
    box.appendChild(el);
  });
}

async function openUnknownDetail(cluster) {
  ukCluster = cluster;
  ukSel.clear();
  showUkPane("uk-detail");
  $("ukd-face").src = `${cropUrl(cluster.cover_face_id)}?size=160`;
  $("ukd-name").textContent = "Unbekannt";
  $("ukd-meta").textContent = "Lade Fotos …";
  $("ukd-stream").innerHTML = "";
  $("ukd-timeline").innerHTML = "";
  $("ukd-faces").innerHTML = "";
  $("ukd-input").value = "";
  $("ukd-suggest").innerHTML = "";

  let data;
  try {
    data = await api("/api/persons/gallery", {
      method: "POST",
      body: JSON.stringify({ face_ids: cluster.face_ids }),
    });
  } catch (err) {
    $("ukd-meta").textContent =
      `Fotos konnten nicht geladen werden (${String(err.message || err).slice(0, 160)})`;
    renderUnknownFaces((cluster.face_ids || []).map((id) => ({ face_id: id })));
    return;
  }
  ukCluster.face_ids = (data.faces || []).map((f) => f.face_id);
  const span = data.span ? ` · ${data.span.from.slice(0, 4)}–${data.span.to.slice(0, 4)}` : "";
  $("ukd-meta").textContent =
    `${data.face_count} Gesicht${data.face_count === 1 ? "" : "er"} · ${data.total} Foto${data.total === 1 ? "" : "s"}${span}`;
  fillGallery($("ukd-timeline"), $("ukd-stream"), data);

  (data.suggestions || []).forEach((s) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.innerHTML = `<strong>${escapeHtml(s.name)}</strong> · ${Math.round((s.score || 0) * 100)}%`;
    chip.addEventListener("click", () => assignUnknown(s.name));
    $("ukd-suggest").appendChild(chip);
  });

  renderUnknownFaces(data.faces || []);
}

async function assignUnknown(name) {
  if (!ukCluster || !name) return;
  const ids = ukSel.size ? [...ukSel] : ukCluster.face_ids;
  await api("/api/persons", {
    method: "POST",
    body: JSON.stringify({ name, face_ids: ids }),
  });
  ukSel.clear();
  peopleCache = [];
  refreshPersonNames();
  showUkPane("uk-overview");
  loadUnknown(true);
}

$("uk-back").addEventListener("click", () => { ukSel.clear(); loadUnknown(true); });
$("ukd-form").addEventListener("submit", (e) => {
  e.preventDefault();
  assignUnknown($("ukd-input").value.trim());
});
$("ukd-ignore").addEventListener("click", async () => {
  if (!ukCluster) return;
  const n = ukCluster.face_ids.length;
  if (!await askConfirm({
    title: `${n} Gesicht${n === 1 ? "" : "er"} dauerhaft ignorieren`,
    lead: "Sie verschwinden aus „Wer ist das?“ und tauchen nicht wieder auf. "
        + "Die Fotos bleiben, nur diese Gesichter werden nicht mehr gefragt.",
    ok: "Ignorieren", danger: true,
  })) return;
  await api("/api/persons/ignore", {
    method: "POST", body: JSON.stringify({ face_ids: ukCluster.face_ids }),
  });
  loadUnknown(true);
});
$("ukd-check").addEventListener("click", () => {
  $("ukd-faces").scrollIntoView({ behavior: "smooth", block: "start" });
});

function updateUkSel() {
  $("uk-sel").textContent = ukSel.size ? `${ukSel.size} gewählt` : "nichts gewählt";
}

$("uk-sort").addEventListener("change", () => loadUnknown(true));
$("uk-more").addEventListener("click", () => loadUnknown(false));
$("uk-none").addEventListener("click", () => {
  ukSel.clear();
  $("uk-faces").querySelectorAll(".face.on").forEach((e) => e.classList.remove("on"));
  updateUkSel();
});

$("uk-ignore").addEventListener("click", async () => {
  const n = ukSel.size;
  if (!n) return;
  if (!await askConfirm({
    title: `${n} Gesicht${n === 1 ? "" : "er"} dauerhaft ignorieren`,
    lead: "Sie verschwinden aus „Wer ist das?“ und tauchen nicht wieder auf.",
    ok: "Ignorieren", danger: true,
  })) return;
  try {
    const res = await api("/api/persons/ignore", {
      method: "POST", body: JSON.stringify({ face_ids: [...ukSel] }),
    });
    notify(`${res.ignored} ignoriert.`);
  } catch (e) {
    notify(`Konnte nicht ignorieren: ${e.message || e}`, { kind: "error" });
    return;
  }
  loadUnknown(true);
});

$("uk-name").addEventListener("click", async () => {
  const n = ukSel.size;
  if (!n) return;
  const name = await askText({
    title: `${n} Gesicht${n === 1 ? "" : "er"} zuordnen`,
    lead: "Bestehender oder neuer Name. Ein Vorname genügt, wenn er eindeutig ist.",
    placeholder: "Name",
    ok: "Zuordnen",
  });
  if (!name) return;
  try {
    const res = await api("/api/persons/faces/move", {
      method: "POST", body: JSON.stringify({ face_ids: [...ukSel], name }),
    });
    notify(`${res.moved} zugeordnet zu „${res.to.name}“.`);
  } catch (e) {
    notify(`Konnte nicht zuordnen: ${e.message || e}`, { kind: "error" });
    return;
  }
  loadUnknown(true);
});

/* ---- Ausdrucks-Builder ----
   Der Ausdrucksbaum hier ist derselbe, den das Backend nach Qdrant übersetzt. Die
   Klartextform holen wir uns von dort zurück, damit Anzeige und tatsächliche
   Abfrage nicht auseinanderlaufen können. */

const FIELDS = [
  { key: "person",     label: "zeigt Person",   kind: "person" },
  { key: "year",       label: "aus dem Jahr",   kind: "text", ph: "2015" },
  { key: "date_from",  label: "aufgenommen ab", kind: "text", ph: "2015-06-01" },
  { key: "date_to",    label: "aufgenommen bis",kind: "text", ph: "2015-08-31" },
  { key: "location",   label: "am Ort",         kind: "text", ph: "griechenland" },
  { key: "tag",        label: "mit Szene",      kind: "text", ph: "strand" },
  { key: "annotation", label: "mit Notiz",      kind: "text", ph: "Stripclub" },
  { key: "folder",     label: "im Album",       kind: "text", ph: "Abi 08" },
];

/* Bereichs-Wähler: der Bereich schränkt *jede* Suche ein, auch die
   Bedeutungssuche. Weil eine unsichtbare Einschränkung eine Falle wäre,
   stehen die Chips über dem Formular und der Umfang im Ergebnissatz. Leere
   Auswahl heißt „alle" -- nicht „keine", denn niemand sucht absichtlich im
   Nichts. Die Wahl bleibt über Sitzungen erhalten, sonst müsste man sie bei
   jedem Aufräumdurchgang neu treffen. */
const SCOPE_KEY = "pv-search-spaces";
let spacesCache = [];
let scopePick = new Set();

let peopleCache = [];
let peopleFilter = "";
let peopleLimit = 16;
let qbTree = { op: "and", children: [] };

async function loadPersonPicker() {
  // Das Feld nahm bisher jede Eingabe an und antwortete erst beim Suchen mit
  // 503. Wer kein Ollama hat, tippt sonst einmal ins Leere und weiss nicht,
  // warum -- obwohl der Rest der Suche vollstaendig funktioniert.
  gate("freetext", $("q-text"), $("q-text-gate"));
  if (!peopleCache.length) {
    try { peopleCache = await api("/api/persons"); } catch { peopleCache = []; }
  }
  peopleCache.sort((a, b) => (b.face_count || 0) - (a.face_count || 0));
  await loadScope();
  renderBuilder();
  renderPeopleChips();
  await renderSearchExamples();
}

async function loadScope() {
  const bar = $("search-scope");
  if (!bar) return;
  if (!spacesCache.length) {
    try {
      spacesCache = (await api("/api/search/spaces")).spaces || [];
    } catch (e) {
      // Nicht wegfallen lassen: ohne Wähler sucht man ungewollt überall.
      $("scope-chips").innerHTML = "";
      $("scope-hint").textContent =
        `Bereiche nicht abrufbar (${String(e.message || e).slice(0, 90)}) — Suche läuft über alles.`;
      return;
    }
  }
  // Nur einen Bereich gibt es nichts zu wählen.
  bar.classList.toggle("hidden", spacesCache.length < 2);
  const known = new Set(spacesCache.map((s) => s.name));
  try {
    const saved = JSON.parse(localStorage.getItem(SCOPE_KEY) || "[]");
    scopePick = new Set(saved.filter((n) => known.has(n)));
  } catch { scopePick = new Set(); }
  renderScope();
}

function renderScope() {
  $("scope-chips").innerHTML = spacesCache.map((s) => `
    <button type="button" class="scope-chip${scopePick.has(s.name) ? " on" : ""}"
            data-space="${escapeHtml(s.name)}">
      ${escapeHtml(s.name)} <em>${num(s.count)}</em>
    </button>`).join("");
  $("scope-hint").textContent = scopePick.size
    ? `Suche nur in ${[...scopePick].join(", ")}.`
    : "Alle Bereiche — nichts eingeschränkt.";
}

$("scope-chips")?.addEventListener("click", (e) => {
  const chip = e.target.closest(".scope-chip");
  if (!chip) return;
  const name = chip.dataset.space;
  if (scopePick.has(name)) scopePick.delete(name);
  else scopePick.add(name);
  // Alle angehakt ist dasselbe wie keiner -- dann lieber keiner, damit der
  // Satz "nichts eingeschränkt" sagt statt alle drei Namen aufzuzählen.
  if (scopePick.size === spacesCache.length) scopePick.clear();
  try { localStorage.setItem(SCOPE_KEY, JSON.stringify([...scopePick])); } catch { /* privater Modus */ }
  renderScope();
});

function renderBuilder() {
  const root = $("qb-root");
  if (!root) return;
  root.innerHTML = "";
  root.appendChild(renderGroup(qbTree, [], true));
  if (!qbTree.children.length) {
    const p = document.createElement("p");
    p.className = "muted hint";
    p.textContent = "Keine extra Bedingung — Personen oben, Jahr oder Album hier.";
    root.appendChild(p);
  }
  updateExpression();
  renderPeopleChips();
}

function renderGroup(group, path, isRoot) {
  const box = document.createElement("div");
  box.className = isRoot ? "qb-group root" : "qb-group";
  group.children.forEach((child, i) => {
    if (i > 0) box.appendChild(renderJoiner(group, path));
    const row = child.children ? renderGroup(child, [...path, i], false) : renderCond(child, [...path, i]);
    box.appendChild(row);
  });
  if (!isRoot) {
    const foot = document.createElement("div");
    foot.className = "qb-subadd";
    foot.innerHTML = `<button type="button" class="mini" data-sub="cond">+ Bedingung</button>
                      <button type="button" class="mini undo" data-sub="del">Klammer auflösen</button>`;
    foot.querySelector('[data-sub="cond"]').addEventListener("click", () => {
      group.children.push({ field: "person", value: "", label: "" });
      renderBuilder();
    });
    foot.querySelector('[data-sub="del"]').addEventListener("click", () => {
      removeAt(path);
      renderBuilder();
    });
    box.appendChild(foot);
  }
  return box;
}

function renderJoiner(group) {
  const wrap = document.createElement("div");
  wrap.className = "qb-join";
  wrap.innerHTML = `
    <button type="button" class="joiner ${group.op === "and" ? "on" : ""}" data-op="and">UND</button>
    <button type="button" class="joiner ${group.op === "or" ? "on" : ""}" data-op="or">ODER</button>`;
  wrap.querySelectorAll(".joiner").forEach((b) => {
    b.addEventListener("click", () => { group.op = b.dataset.op; renderBuilder(); });
  });
  return wrap;
}

function renderCond(cond, path) {
  const row = document.createElement("div");
  row.className = "qb-cond";
  const spec = FIELDS.find((f) => f.key === cond.field) || FIELDS[0];

  const sel = document.createElement("select");
  sel.innerHTML = FIELDS.map((f) =>
    `<option value="${f.key}" ${f.key === cond.field ? "selected" : ""}>${f.label}</option>`).join("");
  sel.addEventListener("change", () => {
    cond.field = sel.value; cond.value = ""; cond.label = "";
    renderBuilder();
  });
  row.appendChild(sel);

  if (spec.kind === "person") {
    const pick = document.createElement("select");
    pick.className = "person-select";
    pick.innerHTML = `<option value="">— Person wählen —</option>` +
      peopleCache.map((p) =>
        `<option value="${escapeHtml(p.id)}" ${p.id === cond.value ? "selected" : ""}>${escapeHtml(p.name)} (${p.face_count})</option>`).join("");
    pick.addEventListener("change", () => {
      cond.value = pick.value;
      cond.label = (peopleCache.find((p) => p.id === pick.value) || {}).name || pick.value;
      updateExpression();
      showFaceFor(row, cond.value);
    });
    row.appendChild(pick);
    showFaceFor(row, cond.value);
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = spec.ph || "";
    input.value = cond.value || "";
    input.addEventListener("input", () => { cond.value = input.value; updateExpression(); });
    row.appendChild(input);
  }

  const del = document.createElement("button");
  del.type = "button";
  del.className = "mini undo qb-del";
  del.textContent = "✕";
  del.title = "Bedingung entfernen";
  del.addEventListener("click", () => { removeAt(path); renderBuilder(); });
  row.appendChild(del);
  return row;
}

/* Das Gesicht neben der Auswahl: zeigt sofort, ob die richtige Person gemeint ist. */
function showFaceFor(row, personId) {
  row.querySelector(".qb-face")?.remove();
  const p = peopleCache.find((x) => x.id === personId);
  if (!p) return;
  const img = document.createElement("img");
  img.className = "qb-face";
  img.src = `${cropUrl(p.cover_face_id)}?size=160`;
  img.alt = p.name;
  img.title = `${p.name} — ${p.face_count} Gesichter`;
  row.insertBefore(img, row.querySelector(".qb-del"));
}

function nodeAt(path) {
  let node = qbTree;
  for (const i of path.slice(0, -1)) node = node.children[i];
  return node;
}

function removeAt(path) {
  const parent = nodeAt(path);
  parent.children.splice(path[path.length - 1], 1);
}

function selectedPeople() {
  return (qbTree.children || []).filter((c) => c.field === "person" && c.value);
}

function setPersonInQuery(person, on) {
  const kids = qbTree.children;
  const i = kids.findIndex((c) => c.field === "person" && c.value === person.id);
  if (on && i < 0) kids.push({ field: "person", value: person.id, label: person.name });
  if (!on && i >= 0) kids.splice(i, 1);
}

function renderPeopleChips() {
  const host = $("search-people");
  if (!host) return;
  if (!peopleCache.length) {
    host.innerHTML = "<p class='muted hint'>Noch niemand benannt — zuerst unter „Wer ist das?“.</p>";
    return;
  }
  const chosen = new Set(selectedPeople().map((c) => c.value));
  const needle = peopleFilter.trim().toLowerCase();
  const hit = (p) => !needle || (p.name || "").toLowerCase().includes(needle)
    || (p.aliases || []).some((a) => String(a).toLowerCase().includes(needle));

  // Bei 114 benannten Personen waren 98 unerreichbar: die Liste endete nach
  // den 16 mit den meisten Gesichtern. Angehakte gehören immer dazu -- sonst
  // sieht man nicht mehr, wen man gewählt hat, sobald man tippt.
  const matching = peopleCache.filter(hit);
  const visible = [
    ...peopleCache.filter((p) => chosen.has(p.id)),
    ...matching.filter((p) => !chosen.has(p.id)).slice(0, peopleLimit),
  ];
  const rest = matching.filter((p) => !chosen.has(p.id)).length - peopleLimit;

  host.innerHTML = `
    <div class="person-find">
      <input type="search" id="person-find" placeholder="Namen suchen — ${peopleCache.length} benannt"
             value="${escapeHtml(peopleFilter)}" autocomplete="off" />
      ${chosen.size ? `<button type="button" class="mini" id="person-none">Auswahl leeren</button>` : ""}
    </div>
    ${visible.map((p) => `
      <button type="button" class="search-person${chosen.has(p.id) ? " on" : ""}" data-id="${escapeHtml(p.id)}">
        <img src="${cropUrl(p.cover_face_id)}?size=80" alt="" />
        ${escapeHtml(p.name)}
      </button>`).join("")}
    ${rest > 0 ? `<button type="button" class="mini" id="person-more">+${rest} weitere</button>` : ""}
    ${!visible.length ? `<p class="muted hint">Niemand mit „${escapeHtml(peopleFilter)}“.</p>` : ""}`;

  const find = $("person-find");
  find.oninput = () => {
    peopleFilter = find.value;
    peopleLimit = 16;
    const at = find.selectionStart;
    renderPeopleChips();
    // Nach dem Neuzeichnen ist das Feld ein anderes -- Fokus und Schreibmarke
    // müssen zurück, sonst tippt man nach jedem Zeichen ins Nichts.
    const again = $("person-find");
    again.focus();
    again.setSelectionRange(at, at);
  };
  if ($("person-more")) {
    $("person-more").onclick = () => { peopleLimit += 32; renderPeopleChips(); };
  }
  if ($("person-none")) {
    $("person-none").onclick = () => {
      selectedPeople().forEach((c) => {
        const p = peopleCache.find((x) => x.id === c.value);
        if (p) setPersonInQuery(p, false);
      });
      renderBuilder();
      renderPeopleChips();
    };
  }
  host.querySelectorAll(".search-person").forEach((b) => {
    b.addEventListener("click", () => {
      const p = peopleCache.find((x) => x.id === b.dataset.id);
      if (!p) return;
      setPersonInQuery(p, !b.classList.contains("on"));
      renderBuilder();
      renderPeopleChips();
    });
  });
}

/* Beispiele duerfen nicht an der Sperre vorbeischreiben.

   Zwei der Kacheln setzen Freitext. Ohne Ollama ist das Feld gesperrt, die
   Kachel schrieb aber direkt hinein -- und die Suche antwortete mit 503. Die
   Sperre war gut begruendet und griff an genau einer von vier Stellen. */
async function renderSearchExamples() {
  const host = $("search-examples");
  if (!host) return;
  const freitext = (await feature("freetext")).ok;
  const named = peopleCache.filter((p) => (p.face_count || 0) >= 8);
  const a = named[0], b = named[1];
  const first = (p) => (p.name || "").split(/\s+/)[0] || p.name;
  const items = [];
  if (a && b) {
    items.push({
      title: `${first(a)} und ${first(b)}`,
      hint: "beide auf einem Foto",
      run: () => {
        qbTree = { op: "and", children: [] };
        setPersonInQuery(a, true);
        setPersonInQuery(b, true);
        $("q-text").value = "";
      },
    });
    // Das Beispielwort war „Bier" -- fest verdrahtet, und die Person davor
    // kommt aus den Daten. Bei einem Kind an erster Stelle stand da dann
    // „<Kind> · Bier". Ein Wort, das zu jedem passt, oder keins.
    items.push({
      title: `${first(a)} · Geburtstag`,
      hint: "Person plus was im Bild ist",
      needs: "freetext",
      run: () => {
        qbTree = { op: "and", children: [] };
        setPersonInQuery(a, true);
        $("q-text").value = "Geburtstag";
      },
    });
  }
  items.push({
    title: "Feuerwerk in der Nacht",
    hint: "nur Freitext, sortiert",
    needs: "freetext",
    run: () => {
      qbTree = { op: "and", children: [] };
      $("q-text").value = "Feuerwerk in der Nacht";
    },
  });
  items.push({
    title: "Strand",
    hint: "Szene aus den Tags",
    run: () => {
      qbTree = { op: "and", children: [{ field: "tag", value: "strand", label: "" }] };
      $("q-text").value = "";
    },
  });
  items.push({
    title: "Jahr 2015",
    hint: "ein ganzes Jahr",
    run: () => {
      qbTree = { op: "and", children: [{ field: "year", value: "2015", label: "" }] };
      $("q-text").value = "";
    },
  });
  const zeigbar = items.filter((ex) => freitext || ex.needs !== "freetext");
  host.innerHTML = zeigbar.map((ex, i) =>
    `<button type="button" class="search-ex" data-i="${i}">
       <strong>${escapeHtml(ex.title)}</strong>
       <span>${escapeHtml(ex.hint)}</span>
     </button>`).join("");
  host.querySelectorAll(".search-ex").forEach((btn) => {
    btn.addEventListener("click", async () => {
      zeigbar[Number(btn.dataset.i)].run();
      renderBuilder();
      await runSearch();
    });
  });
}

document.querySelectorAll("[data-add]").forEach((b) => {
  b.addEventListener("click", () => {
    if (b.dataset.add === "cond") {
      qbTree.children.push({ field: "person", value: "", label: "" });
    } else {
      qbTree.children.push({ op: "or", children: [{ field: "year", value: "", label: "" }] });
    }
    renderBuilder();
  });
});

/* Vorschau lokal, damit es beim Tippen mitläuft; die verbindliche Fassung
   liefert der Server mit dem Suchergebnis. */
function localExpression(node, top = true) {
  if (!node.children) {
    const spec = FIELDS.find((f) => f.key === node.field);
    const val = (node.label || node.value || "").trim();
    return spec && val ? `${spec.label.toLowerCase()} ${val}` : "";
  }
  const parts = node.children.map((c) => localExpression(c, false)).filter(Boolean);
  if (!parts.length) return "";
  if (parts.length === 1) return parts[0];
  const joined = parts.join(` ${node.op === "or" ? "ODER" : "UND"} `);
  return top ? joined : `(${joined})`;
}

function updateExpression() {
  const text = localExpression(qbTree);
  const free = ($("q-text") && $("q-text").value.trim()) || "";
  let line = text ? `Fotos, die ${text}` : "Noch keine Bedingung — alle Fotos";
  if (free) line += `, sortiert nach „${free}“`;
  $("qb-expr").textContent = line;
}

/* Die Leiste kommt aus core/nav.js, damit die Jobs-Seite dieselbe zeigt. Der
   Tab steht dabei in der Adresse: von der Jobs-Seite führt jeder Eintrag auf
   `/?tab=…`, und ein geteilter Link landet dort, wo er soll. */
renderNav($("nav"), { active: tabFromUrl(), inPlace: true });
$("nav").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-tab]");
  if (btn) showTab(btn.dataset.tab);
});

async function loadQueue() {
  $("queue-meta").textContent = "Gesichter werden gruppiert …";
  const data = await api("/api/persons/unlabeled");
  state.clusters = data.clusters || [];
  state.remaining = data.remaining || 0;
  state.stats = data.stats || null;
  state.index = 0;
  renderCard();
}

function progressLine() {
  return faceStatsLine(state.stats);
}

function renderCard() {
  const cluster = state.clusters[state.index];
  $("empty").classList.toggle("hidden", Boolean(cluster));
  $("card").classList.toggle("hidden", !cluster);
  const left = state.clusters.length - state.index + (state.remaining || 0);
  $("queue-meta").textContent = cluster
    ? `${left} unbekannte Gruppe${left === 1 ? "" : "n"}${progressLine()}`
    : "";
  if (!cluster) return;

  // Ausgeschlossene Gesichter bleiben sichtbar, werden aber nicht mitzugeordnet.
  cluster.excluded = cluster.excluded || new Set();
  showCover(cluster, cluster.cover_face_id);
  renderThumbs(cluster);

  const known = cluster.kind === "known" && cluster.person_name;
  $("card-title").textContent = known
    ? `Noch mehr ${cluster.person_name}?`
    : "Wer ist das?";
  $("name-form").classList.remove("hidden");
  $("btn-confirm-known").classList.toggle("hidden", !known);
  $("btn-confirm-known").textContent = known
    ? `Ja, ${cluster.size} Gesichter ${cluster.person_name} zuordnen`
    : "";

  $("suggestions").innerHTML = "";
  (cluster.suggestions || []).forEach((s) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    const pct = Math.round((s.score || 0) * 100);
    chip.innerHTML = `<strong>${escapeHtml(s.name)}</strong> · ${pct}%`;
    chip.addEventListener("click", () => assignExisting(s.id, keptFaces(cluster)));
    $("suggestions").appendChild(chip);
  });
  $("name-input").value = "";
  $("name-input").placeholder = known
    ? "Nein — anderer Name (z. B. Schwester)"
    : "Name eingeben — neue Person oder bekanntes Gesicht";
  $("name-input").focus();
}

function showCover(cluster, faceId) {
  cluster.shown = faceId;
  $("cover").src = `${cropUrl(faceId)}?size=640`;
  $("thumbs").querySelectorAll("figure").forEach((f) => {
    f.classList.toggle("on", f.dataset.id === faceId);
  });
}

/* Jedes Gesicht des Clusters ist anklickbar: groß ansehen, und wenn es nicht
   dazugehört, hier gleich ausschließen -- sonst zieht ein Fremder mit ein. */
function renderThumbs(cluster) {
  const box = $("thumbs");
  box.innerHTML = "";
  cluster.face_ids.forEach((id) => {
    const fig = document.createElement("figure");
    fig.className = "thumb";
    fig.dataset.id = id;
    fig.classList.toggle("out", cluster.excluded.has(id));
    fig.innerHTML = `<img loading="lazy" src="${cropUrl(id)}?size=160" alt="" />
                     <button type="button" class="drop" title="Gehört nicht dazu">✕</button>`;
    fig.querySelector("img").addEventListener("click", () => showCover(cluster, id));
    fig.querySelector(".drop").addEventListener("click", (e) => {
      e.stopPropagation();
      cluster.excluded.has(id) ? cluster.excluded.delete(id) : cluster.excluded.add(id);
      fig.classList.toggle("out", cluster.excluded.has(id));
      updateClusterCount(cluster);
    });
    box.appendChild(fig);
  });
  updateClusterCount(cluster);
  showCover(cluster, cluster.shown || cluster.cover_face_id);
}

function keptFaces(cluster) {
  return cluster.face_ids.filter((id) => !cluster.excluded.has(id));
}

function updateClusterCount(cluster) {
  const kept = keptFaces(cluster).length;
  const out = cluster.face_ids.length - kept;
  $("cluster-size").textContent =
    (kept === 1 ? "1 Gesicht" : `${kept} ähnliche Gesichter — eine Zuordnung gilt für alle`)
    + (out ? ` · ${out} ausgeschlossen` : "");
}

$("btn-confirm-known").addEventListener("click", () => {
  const cluster = state.clusters[state.index];
  if (!cluster || cluster.kind !== "known") return;
  assignExisting(cluster.person_id, keptFaces(cluster));
});

async function assignExisting(personId, faceIds) {
  await api(`/api/persons/${encodeURIComponent(personId)}/assign`, {
    method: "POST",
    body: JSON.stringify({ face_ids: faceIds }),
  });
  next();
}

async function assignNew(name, faceIds) {
  await api("/api/persons", {
    method: "POST",
    body: JSON.stringify({ name, face_ids: faceIds }),
  });
  next();
}

function next() {
  state.clusters.splice(state.index, 1);
  if (!state.clusters.length) loadQueue();
  else renderCard();
}

$("name-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const cluster = state.clusters[state.index];
  const name = $("name-input").value.trim();
  if (!cluster || !name) return;
  assignNew(name, keptFaces(cluster));
});

$("btn-skip").addEventListener("click", async () => {
  const cluster = state.clusters[state.index];
  if (!cluster) return;
  await api("/api/persons/skip", {
    method: "POST",
    body: JSON.stringify({ face_ids: keptFaces(cluster) }),
  });
  next();
});

function personCard(p, { onOpen, extraActions } = {}) {
  const el = document.createElement("div");
  el.className = "person" + (p.pin === "favorite" ? " fav" : p.pin === "muted" ? " muted-card" : "");
  const star = p.pin === "favorite" ? "★" : "☆";
  el.innerHTML = `
    <button type="button" class="pin${p.pin === "favorite" ? " on" : ""}" title="Favorit">${star}</button>
    <img class="avatar" src="${cropUrl(p.cover_face_id)}?size=160" alt="" />
    <div class="who">${escapeHtml(p.name)}</div>
    <div class="muted">${p.face_count} Gesicht${p.face_count === 1 ? "" : "er"}</div>
    ${(p.aliases || []).length ? `<div class="muted aliases">„${p.aliases.map(escapeHtml).join("“, „")}“</div>` : ""}
    <div class="person-actions">
      <button type="button" class="mini rename">Umbenennen</button>
      <button type="button" class="mini alias">Spitznamen</button>
      <button type="button" class="mini mute">${p.pin === "muted" ? "Wieder zeigen" : "Ausblenden"}</button>
      <button type="button" class="mini undo">Zuordnung lösen</button>
      ${extraActions || ""}
    </div>`;
  el.querySelector(".avatar").addEventListener("click", () => (onOpen || openPersonPhotos)(p));
  el.querySelector(".who").addEventListener("click", () => (onOpen || openPersonPhotos)(p));
  el.querySelector(".who").style.cursor = "pointer";
  el.querySelector(".pin").addEventListener("click", (e) => { e.stopPropagation(); togglePin(p, "favorite"); });
  el.querySelector(".rename").addEventListener("click", (e) => { e.stopPropagation(); renamePerson(p); });
  el.querySelector(".alias").addEventListener("click", (e) => { e.stopPropagation(); editAliases(p); });
  el.querySelector(".mute").addEventListener("click", (e) => { e.stopPropagation(); togglePin(p, "muted"); });
  el.querySelector(".undo").addEventListener("click", (e) => { e.stopPropagation(); unassignPerson(p); });
  return el;
}

async function togglePin(p, kind) {
  const next = p.pin === kind ? null : kind;
  await api(`/api/persons/${encodeURIComponent(p.id)}/pin`, {
    method: "POST",
    body: JSON.stringify({ pin: next }),
  });
  peopleCache = [];
  loadPeople();
}

function appendSection(box, title, people) {
  if (!people.length) return;
  const h = document.createElement("h2");
  h.className = "people-section";
  h.textContent = title;
  box.appendChild(h);
  people.forEach((p) => box.appendChild(personCard(p)));
}

async function loadPeople() {
  const people = await api("/api/persons");
  peopleCache = people;
  const box = $("people-list");
  box.innerHTML = "";
  if (!people.length) {
    box.innerHTML = "<p class='muted'>Noch keine zugeordneten Personen.</p>";
    return;
  }
  const fav = people.filter((p) => p.pin === "favorite");
  const rest = people.filter((p) => p.pin !== "favorite" && p.pin !== "muted");
  const muted = people.filter((p) => p.pin === "muted");
  if (!fav.length && !muted.length) {
    rest.forEach((p) => box.appendChild(personCard(p)));
    return;
  }
  appendSection(box, "Favoriten", fav);
  appendSection(box, "Weitere Personen", rest);
  appendSection(box, "Ausgeblendet", muted);
}

/* ---- Fotos einer Person, chronologisch ---- */

async function openPersonPhotos(p) {
  showPane("person-photos");
  $("pp-name").textContent = p.name;
  $("pp-face").src = `${cropUrl(p.cover_face_id)}?size=160`;
  $("pp-meta").textContent = "Lade Fotos …";
  $("pp-stream").innerHTML = "";
  $("pp-timeline").innerHTML = "";
  $("btn-check-faces").onclick = () => openPersonFaces(p);

  const data = await api(`/api/persons/${encodeURIComponent(p.id)}/photos`);
  fillGallery($("pp-timeline"), $("pp-stream"), data, {
    meta: $("pp-meta"),
    // Solange die Auswahl laeuft, verbraucht der Klick sich hier und die
    // Grossansicht bleibt zu. `true` heisst genau das.
    onPick: (ph, im) => {
      if (!selectMode) return false;
      photoSel.has(ph.id) ? photoSel.delete(ph.id) : photoSel.add(ph.id);
      im.classList.toggle("picked", photoSel.has(ph.id));
      updatePhotoSel();
      return true;
    },
  });
}

/* ---- Mehrere Fotos auf einmal beschriften ----
   Wissen, das kein Modell aus Pixeln holt: „das war im Stripclub“. Auf 50
   Fotos eines Abends angewandt macht es diesen Abschnitt auffindbar. */

let selectMode = false;
const photoSel = new Set();

$("btn-select-mode").addEventListener("click", () => {
  selectMode = !selectMode;
  $("btn-select-mode").classList.toggle("on", selectMode);
  $("btn-select-mode").textContent = selectMode ? "Auswahl beenden" : "Fotos auswählen";
  $("pp-stream").classList.toggle("selecting", selectMode);
  $("pp-actions").classList.toggle("hidden", !selectMode);
  if (!selectMode) clearPhotoSel();
  updatePhotoSel();
});

function clearPhotoSel() {
  photoSel.clear();
  $("pp-stream").querySelectorAll(".picked").forEach((e) => e.classList.remove("picked"));
}

function updatePhotoSel() {
  $("pp-sel").textContent = photoSel.size
    ? `${photoSel.size} Foto${photoSel.size === 1 ? "" : "s"} gewählt`
    : "Fotos anklicken zum Auswählen";
}

$("pp-clear").addEventListener("click", () => { clearPhotoSel(); updatePhotoSel(); });

/* Die drei Handlungen der Fotoauswahl.

   Sie liefen ueber `prompt()` und `alert()`. Wo `prompt()` gesperrt ist --
   eingebettete Browser, oder Chrome nach mehreren Aufrufen -- bricht die
   Zusage ab und *nichts* passiert: drei Knoepfe, die aussehen wie kaputt,
   ohne eine Zeile Erklaerung. Genau so ist es gemeldet worden.

   Jetzt derselbe Dialog wie im Atlas, und das Ergebnis steht in der Leiste
   statt in einem Systemfenster -- samt Fehlern, die vorher gar nicht
   ankamen. */
function selNote(text) {
  const el = $("pp-sel-note");
  if (el) el.textContent = text;
}

async function runOnSelection(path, body, done) {
  // Die Notiz allein waere sofort da; das Neurechnen der Textvektoren dauert
  // rund 130 ms je Foto. Bei fuenfzig Fotos sind das sieben Sekunden, in
  // denen sonst nur "läuft" stünde und man das Schlimmste annimmt.
  const n = (body.photo_ids || []).length;
  selNote(`läuft … ${n === 1 ? "ein Foto" : `${n} Fotos`}, Textvektoren `
        + "werden mitgerechnet");
  try {
    const res = await api(path, { method: "POST", body: JSON.stringify(body) });
    selNote(done(res));
  } catch (e) {
    selNote(`Fehlgeschlagen: ${String(e.message || e).slice(0, 160)}`);
    return;
  }
  clearPhotoSel();
  updatePhotoSel();
}

async function annotateSelection(mode) {
  const n = photoSel.size;
  if (!n) { selNote("Erst Fotos anhaken."); return; }
  const ids = [...photoSel];
  const weg = mode === "remove";
  const text = await askText({
    title: `Notiz ${weg ? "entfernen von" : "für"} ${n} Foto${n === 1 ? "" : "s"}`,
    lead: weg
      ? "Wird von diesen Fotos gelöst. Mehrere mit Komma trennen."
      : "Wird als Schlagwort gespeichert und ist danach exakt filterbar. "
        + "Mehrere mit Komma trennen.",
    placeholder: "z. B. Omas Garten, Umzug",
    ok: weg ? "Entfernen" : "Anhängen",
  });
  if (!text) return;
  await runOnSelection("/api/photos/annotate", {
    photo_ids: ids,
    annotations: text.split(",").map((s) => s.trim()).filter(Boolean),
    mode,
    reembed: true,
  }, (r) => `${r.changed} ${r.changed === 1 ? "Foto" : "Fotos"} geändert`
        + (r.reembedded ? `, ${r.reembedded} neu eingebettet` : "") + ".");
}

$("pp-tag").addEventListener("click", () => annotateSelection("add"));
$("pp-untag").addEventListener("click", () => annotateSelection("remove"));

$("pp-cap").addEventListener("click", async () => {
  const n = photoSel.size;
  if (!n) { selNote("Erst Fotos anhaken."); return; }
  const ids = [...photoSel];
  const text = await askText({
    title: `Beschreibung für ${n} Foto${n === 1 ? "" : "s"}`,
    lead: "Überschreibt vorhandene und wird gegen neue Vision-Läufe gesperrt.",
    placeholder: "Ein Satz, der beschreibt was zu sehen ist",
    ok: "Setzen",
    rows: 3,
  });
  if (!text) return;
  await runOnSelection("/api/photos/caption/bulk",
    { photo_ids: ids, caption_de: text, lock: true },
    (r) => `${r.updated} ${r.updated === 1 ? "Foto" : "Fotos"} beschriftet`
         + (r.reembedded ? `, ${r.reembedded} neu eingebettet` : "") + ".");
});

function showPane(id) {
  ["people-overview", "person-photos", "person-detail"].forEach((p) => {
    $(p).classList.toggle("hidden", p !== id);
  });
}

document.querySelectorAll(".back-people").forEach((b) => {
  b.addEventListener("click", () => { showPane("people-overview"); loadPeople(); });
});

/* Einzelne Gesichter korrigieren: Clustering irrt sich, und dann steckt
   ein fremdes Gesicht in einer sonst richtigen Gruppe. */
const selectedFaces = new Set();

async function openPersonFaces(p) {
  selectedFaces.clear();
  showPane("person-detail");
  $("detail-name").textContent = p.name;
  $("detail-faces").innerHTML = "<p class='muted'>Lade Gesichter …</p>";
  const data = await api(`/api/persons/${encodeURIComponent(p.id)}/faces`);
  $("detail-name").textContent = `${data.name} — ${data.total} Gesichter`;
  $("person-detail").dataset.personId = p.id;
  const box = $("detail-faces");
  box.innerHTML = "";
  data.faces.forEach((f) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "face";
    const pct = f.score == null ? "" : `${Math.round(f.score * 100)}%`;
    el.innerHTML = `<img loading="lazy" src="${cropUrl(f.face_id)}?size=160" alt="" /><span>${pct}</span>`;
    el.addEventListener("click", () => {
      selectedFaces.has(f.face_id) ? selectedFaces.delete(f.face_id) : selectedFaces.add(f.face_id);
      el.classList.toggle("on", selectedFaces.has(f.face_id));
      updateFaceSelection();
    });
    box.appendChild(el);
  });
  updateFaceSelection();
}

function updateFaceSelection() {
  const n = selectedFaces.size;
  $("detail-actions").classList.toggle("hidden", n === 0);
  $("sel-count").textContent = n === 1 ? "1 Gesicht gewählt" : `${n} Gesichter gewählt`;
}

$("btn-unassign").addEventListener("click", async () => {
  const n = selectedFaces.size;
  if (!n) return;
  if (!await askConfirm({
    title: `${n} Gesicht${n === 1 ? "" : "er"} aus dieser Person entfernen`,
    lead: "Sie wandern zurück in „Wer ist das?“ und können dort neu zugeordnet werden.",
    ok: "Entfernen",
  })) return;
  try {
    const res = await api("/api/persons/faces/unassign", {
      method: "POST",
      body: JSON.stringify({ face_ids: [...selectedFaces] }),
    });
    notify(`${res.freed} entfernt, ${res.photos_updated} Fotos aktualisiert.`);
  } catch (e) {
    notify(`Konnte nicht entfernen: ${e.message || e}`, { kind: "error" });
    return;
  }
  reopenCurrentPerson();
});

$("btn-move").addEventListener("click", async () => {
  const n = selectedFaces.size;
  if (!n) return;
  const name = await askText({
    title: `${n} Gesicht${n === 1 ? "" : "er"} zuordnen`,
    lead: "Bestehender oder neuer Name.",
    placeholder: "Name",
    ok: "Zuordnen",
  });
  if (!name) return;
  try {
    const res = await api("/api/persons/faces/move", {
      method: "POST",
      body: JSON.stringify({ face_ids: [...selectedFaces], name }),
    });
    notify(`${res.moved} verschoben zu „${res.to.name}“, ${res.photos_updated} Fotos aktualisiert.`);
  } catch (e) {
    notify(`Konnte nicht verschieben: ${e.message || e}`, { kind: "error" });
    return;
  }
  reopenCurrentPerson();
});

async function reopenCurrentPerson() {
  const id = $("person-detail").dataset.personId;
  const people = await api("/api/persons");
  const p = people.find((x) => x.id === id);
  if (p) openPersonFaces(p);
  else { showPane("people-overview"); loadPeople(); }
}

async function renamePerson(p) {
  const name = await askText({
    title: `„${p.name}“ umbenennen`,
    lead: "Gilt für alle Gesichter und Fotos dieser Person.",
    placeholder: "Neuer Name",
    ok: "Umbenennen",
    value: p.name,
  });
  if (!name || name === p.name) return;
  try {
    const res = await api(`/api/persons/${encodeURIComponent(p.id)}/rename`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    notify(`Umbenannt: ${res.faces} Gesichter, ${res.photos} Fotos aktualisiert.`);
  } catch (e) {
    notify(`Konnte nicht umbenennen: ${e.message || e}`, { kind: "error" });
    return;
  }
  loadPeople();
}

/* Spitznamen sind zusätzliche Sucheinstiege -- der echte Name bleibt der eine,
   saubere Eintrag, „Karo“ findet trotzdem „Annika Wolf“. */
async function editAliases(p) {
  const current = (p.aliases || []).join(", ");
  // Leer bedeutet hier „alle entfernen" und ist damit eine gueltige Antwort.
  // `askText` gibt fuer leeren Text `null` zurueck, also wird das Abbrechen
  // hier nicht am Inhalt erkannt, sondern eigens gefragt.
  const input = await askText({
    title: `Spitznamen für „${p.name}“`,
    lead: "Mit Komma trennen. Damit findet die Suche die Person auch unter diesen Namen. "
        + "Leer lassen und übernehmen entfernt alle.",
    placeholder: "Karo, Kari",
    ok: "Speichern",
    value: current,
  });
  if (input === null && current === "") return;
  try {
    const res = await api(`/api/persons/${encodeURIComponent(p.id)}/aliases`, {
      method: "POST",
      body: JSON.stringify({ aliases: (input || "").split(",").map((s) => s.trim()).filter(Boolean) }),
    });
    peopleCache = [];  // Picker im Suchtab neu laden
    notify(res.aliases.length
      ? `Gespeichert: „${res.aliases.join("“, „")}“`
      : "Spitznamen entfernt.");
  } catch (e) {
    notify(`Konnte Spitznamen nicht speichern: ${e.message || e}`, { kind: "error" });
    return;
  }
  loadPeople();
}

async function unassignPerson(p) {
  if (!await askConfirm({
    title: `Zuordnung von „${p.name}“ auflösen`,
    lead: `${p.face_count} Gesichter wandern zurück in „Wer ist das?“. `
        + "Die Fotos bleiben, nur der Name wird entfernt.",
    ok: "Auflösen", danger: true,
  })) return;
  try {
    const res = await api(`/api/persons/${encodeURIComponent(p.id)}`, { method: "DELETE" });
    notify(`Gelöst: ${res.faces_freed} Gesichter zurück in die Queue, ${res.photos} Fotos bereinigt.`);
  } catch (e) {
    notify(`Konnte nicht auflösen: ${e.message || e}`, { kind: "error" });
    return;
  }
  loadPeople();
}

$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  // Eine neue Frage faengt auf Seite eins an.
  qbOffset = 0;
  await runSearch();
});

/* Dieselbe Frage, andere Antwort: die Karte zeigt, *wo* die Treffer liegen.

   Geholt werden dafuer alle Kennungen, nicht die erste Seite -- die Karte
   waere sonst eine Stichprobe. Bei Freitext geht das nicht: das ist eine
   Rangfolge, keine Menge, und der Server schneidet oben ab. Er sagt es
   (`ranked`), und das Band sagt es weiter. */
$("qb-to-atlas").addEventListener("click", async () => {
  const btn = $("qb-to-atlas");
  const frei = $("q-text").value.trim();
  btn.disabled = true;
  const alt = btn.textContent;
  btn.textContent = "holt Treffer …";
  let data;
  try {
    data = await api("/api/search/query", {
      method: "POST",
      body: JSON.stringify({
        query: qbTree,
        spaces: [...scopePick],
        caption_query: frei || null,
        ids_only: true,
      }),
    });
  } catch (err) {
    notify(`Konnte die Treffer nicht holen: ${err.message || err}`, { kind: "error" });
    btn.disabled = false; btn.textContent = alt;
    return;
  }
  btn.disabled = false;
  btn.textContent = alt;
  if (!data.ids?.length) { notify("Keine Treffer zum Zeigen."); return; }

  const satz = [data.conditions ? data.expression : "alle Fotos", data.scope,
                frei ? `ähnlich zu „${frei}“` : ""].filter(Boolean).join(", ");
  const { focusFromSearch } = await import("./atlas/index.js?v=55");
  focusFromSearch({
    ids: data.ids,
    label: satz,
    note: data.ranked ? `der ${data.ids.length} ähnlichsten` : "Treffer",
  });
  document.querySelector('[data-tab="atlas"]').click();
});
$("q-text")?.addEventListener("input", updateExpression);

/* Wie viele Treffer eine Seite fasst. Der Wert stand fruher als nackte 48
   im Rumpf der Anfrage, und daneben pruefte die Meldung auf dieselbe 48, um
   auf "erste Seite" zu schliessen -- zwei Stellen, eine Zahl. */
const QB_PAGE = 48;
let qbOffset = 0;

async function runSearch() {
  const box = $("search-results");
  const meta = $("search-meta");
  box.innerHTML = "<p class='muted'>Suche …</p>";
  if (meta) meta.textContent = "";
  let data;
  try {
    data = await api("/api/search/query", {
      method: "POST",
      body: JSON.stringify({
        query: qbTree,
        spaces: [...scopePick],
        caption_query: $("q-text").value.trim() || null,
        limit: QB_PAGE,
        offset: qbOffset,
      }),
    });
  } catch (err) {
    box.innerHTML = `<p class='muted'>Suche fehlgeschlagen (${escapeHtml(String(err.message || err).slice(0, 160))})</p>`;
    return;
  }
  const free = $("q-text").value.trim();
  // Ohne Bedingung heisst der Ausdruck "alle Fotos" -- mit dem Vorspann
  // "Fotos, die" davor wird daraus ein Stolpersatz.
  const satz = data.conditions ? `Fotos, die ${data.expression}` : "Alle Fotos";
  $("qb-expr").textContent =
    satz +
    (data.scope ? `, ${data.scope}` : "") +
    (free ? `, sortiert nach „${free}“` : "") +
    ` — ${data.total} Treffer` +
    (data.conditions ? ` · ${data.conditions} Bedingung${data.conditions === 1 ? "" : "en"}` : "");
  const gezeigt = data.returned ?? (data.results || []).length;
  if (meta) {
    // Die Zahl ist jetzt die ganze Menge, nicht die Seitenlaenge -- also
    // steht daneben, welcher Ausschnitt gerade zu sehen ist.
    meta.textContent = data.total
      ? (gezeigt < data.total
          ? `${data.total} Treffer · ${qbOffset + 1}–${qbOffset + gezeigt} zu sehen`
          : `${data.total} Treffer`)
      : "Keine Treffer.";
  }
  $("qb-to-atlas").classList.toggle("hidden", !data.total);
  renderResults(data.results || []);
  renderPager(["qb-pager", "qb-pager-2"], qbOffset, gezeigt, data.total,
              (next) => { qbOffset = next; runSearch(); }, QB_PAGE);
}

function renderResults(results, unknown = []) {
  const box = $("search-results");
  box.innerHTML = "";
  if (unknown.length) {
    const warn = document.createElement("p");
    warn.className = "muted warn";
    warn.textContent = `Unbekannte Person${unknown.length > 1 ? "en" : ""}: ${unknown.join(", ")} — noch niemand mit diesem Namen benannt.`;
    box.appendChild(warn);
  }
  if (!results.length) {
    box.insertAdjacentHTML(
      "beforeend",
      "<p class='muted'>Keine Treffer. Personen filtern hart; Freitext sortiert nur. Ohne Captions trifft „Bier“ oft nichts.</p>",
    );
    return;
  }
  results.forEach((r, i) => {
    const el = document.createElement("figure");
    el.className = "hit";
    const head = r.caption_display || [r.date, r.folder_name].filter(Boolean).join(" · ");
    const names = (r.person_names || []).join(", ");
    const notes = (r.annotations || []).map((a) => `<span class="note">${escapeHtml(a)}</span>`).join("");
    const tags = (r.scene_tags || []).slice(0, 6).map((t) => `<span class="note">${escapeHtml(t)}</span>`).join("");
    // Die erste Reihe eager: lazy laedt erst bei Sichtbarkeit, was je nach
    // Umgebung gar nicht ausloest und leere Kacheln hinterlaesst.
    const loading = i < 12 ? "eager" : "lazy";
    el.innerHTML = `
      <img loading="${loading}" src="/api/photos/${encodeURIComponent(r.id)}/thumb?size=320" alt="" />
      <figcaption>
        <div class="muted">${escapeHtml(head)}</div>
        ${names ? `<div class="names">${escapeHtml(names)}</div>` : ""}
        ${r.caption_de ? `<p>${escapeHtml(r.caption_de)}</p>` : `<p class="muted">Noch keine Caption</p>`}
        ${notes || tags ? `<div class="notes">${notes}${tags}</div>` : ""}
      </figcaption>`;
    el.querySelector("img").addEventListener("click", () => showLightbox(results, i));
    box.appendChild(el);
  });
}

loadQueue().catch((err) => {
  $("queue-meta").textContent = `API nicht erreichbar: ${err.message}`;
});


/* ---- Bereits benannte Personen im Unbekannt-Stapel ---------------------
   Knapp ein Drittel der "unbekannten" Gesichter gehoert zu Leuten, die
   laengst benannt sind. In der Cluster-Ansicht erscheinen sie als dutzende
   Kleingruppen derselben Person -- hier als eine Rueckfrage je Person. */

async function loadCandidates() {
  const sum = $("ukc-sum");
  const box = $("ukc-list");
  if (!sum || !box) return;
  sum.textContent = "wird geprüft …";
  box.innerHTML = "";
  let d;
  try {
    d = await api("/api/persons/candidates?limit=30&faces_per=18");
  } catch (err) {
    sum.textContent = "konnte nicht geladen werden";
    return;
  }
  if (!d.people) {
    sum.textContent = "keine Treffer";
    return;
  }
  sum.textContent = `${d.total_faces} Gesichter bei ${d.people} Personen`;
  box.innerHTML = d.batches.map((b, i) => `
    <div class="cand" data-i="${i}">
      <div class="cand-head">
        <strong>${escapeHtml(b.name)}</strong>
        <span class="muted">${b.count} Gesicht${b.count === 1 ? "" : "er"} ·
          Ähnlichkeit ${b.worst.toFixed(2)}–${b.best.toFixed(2)}${
            b.shown < b.count ? ` · ${b.shown} gezeigt` : ""}</span>
        <button type="button" class="cand-ok">Alle ${b.count} bestätigen</button>
      </div>
      <div class="faces">
        ${b.faces.map((f) => `<img loading="lazy" alt="" title="${f.score}"
            src="${cropUrl(f.face_id)}?size=120" />`).join("")}
      </div>
    </div>`).join("");

  box.querySelectorAll(".cand").forEach((el) => {
    const b = d.batches[Number(el.dataset.i)];
    el.querySelector(".cand-ok").addEventListener("click", async () => {
      // Bewusst mit Rueckfrage: hier werden bis zu 700 Gesichter auf einmal
      // zugeordnet, und ein Fehler verfaelscht danach jede Suche.
      if (!await askConfirm({
        title: `${b.count} Gesichter bestätigen`,
        lead: `Alle als „${escapeHtml(b.name)}“ übernehmen.`,
        ok: "Bestätigen",
      })) return;
      const btn = el.querySelector(".cand-ok");
      btn.disabled = true;
      btn.textContent = "…";
      try {
        await api("/api/persons/candidates/confirm", {
          method: "POST",
          body: JSON.stringify({ person_id: b.person_id, name: b.name,
                                 threshold: d.threshold }),
        });
        el.remove();
        peopleCache = [];
        loadUnknown(true);
        loadCandidates();
      } catch (err) {
        btn.disabled = false;
        btn.textContent = `Alle ${b.count} bestätigen`;
        notify(`Konnte nicht zugeordnet werden: ${err.message || err}`, { kind: "error" });
      }
    });
  });
}


/* Die Vorschlagslisten liegen in core/names.js. Der Startabruf steht hier
   und nicht dort: stuende er im Modul, liefen zwei API-Aufrufe bei jedem
   Import mit -- auch fuer Tabs, die keine Vorschlagsliste brauchen. */
refreshPersonNames();
refreshEventNames();

// Die Tasten und Knoepfe der Grossansicht einmal binden. Frueher geschah das
// beim Auswerten von app.js, weil die Listener dort standen; jetzt sagt es
// der Router ausdruecklich -- und zwar bevor der erste Tab aufgeht.
bindLightbox();
bindEvents();

/* Zuletzt, nicht zwischendrin.

   Stand frueher direkt hinter der Navigationsleiste. Das war zu frueh: bei
   `?tab=events` rief showTab den Serien-Tab auf, dessen Zustand erst weit
   unten mit `let` deklariert war -- temporale Totzone, und die Auswertung des
   ganzen Moduls brach ab. Alles danach wurde nie gebunden. Von der Jobs-Seite
   fuehrt genau so ein Link hierher (core/nav.js).

   Der Grund ist seit events/index.js weg: als eigenes Modul steht der Zustand
   vor dem ersten Aufruf. Die Zeile bleibt trotzdem hier unten, denn sie ist
   jetzt aus einem zweiten Grund richtig -- am Ende ist gebunden, was gebunden
   gehoert, bevor der erste Tab aufgeht. */
showTab(tabFromUrl());
