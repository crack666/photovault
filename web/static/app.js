const $ = (id) => document.getElementById(id);

const state = { clusters: [], index: 0, remaining: 0 };

function cropUrl(faceId) {
  return `/api/faces/${encodeURIComponent(faceId)}/crop`;
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $(`view-${name}`).classList.remove("hidden");
  if (name === "people") loadPeople();
  if (name === "search") loadPersonPicker();
  if (name === "unknown") { loadUnknown(true); loadCandidates(); }
  if (name === "events") loadEvents();
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
    const kleine = (d.groups_total || 0) - (d.groups_usable || 0);
    $("uk-meta").textContent =
      `${d.groups_usable ?? d.clusters.length} Gruppen ab ${d.min_size ?? 3} Gesichtern` +
      (d.remaining ? ` · ${d.clusters.length} gezeigt, +${d.remaining} weitere` : "") +
      (kleine > 0 ? ` · ${kleine} Einzelne und Paare ausgeblendet` : "") +
      (d.stats ? ` · ${d.stats.faces_labeled} von ${d.stats.faces_total} Gesichtern benannt` : "");
    $("uk-hint").textContent = "Eine Karte ist eine vermutete Person. Rein klicken für alle Fotos, dann benennen.";
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
    `${d.total} unbenannt · ${d.stats.faces_labeled} von ${d.stats.faces_total} Gesichtern benannt`;
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
  fillGallery($("ukd-timeline"), $("ukd-stream"), data, { selectable: false });

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
  if (!confirm(`${n} Gesicht${n === 1 ? "" : "er"} dieser Gruppe dauerhaft ignorieren?`)) return;
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
  if (!n || !confirm(`${n} Gesicht${n === 1 ? "" : "er"} dauerhaft ignorieren?\n\nSie verschwinden aus „Wer ist das?“ und tauchen nicht wieder auf.`)) return;
  const res = await api("/api/persons/ignore", {
    method: "POST", body: JSON.stringify({ face_ids: [...ukSel] }),
  });
  alert(`${res.ignored} ignoriert.`);
  loadUnknown(true);
});

$("uk-name").addEventListener("click", async () => {
  const n = ukSel.size;
  if (!n) return;
  const name = prompt(`${n} Gesicht${n === 1 ? "" : "er"} welcher Person zuordnen?`);
  if (!name || !name.trim()) return;
  const res = await api("/api/persons/faces/move", {
    method: "POST", body: JSON.stringify({ face_ids: [...ukSel], name: name.trim() }),
  });
  alert(`${res.moved} zugeordnet zu „${res.to.name}“.`);
  loadUnknown(true);
});

/* ---- Ausdrucks-Builder ----
   Der Baum hier ist derselbe, den das Backend nach Qdrant übersetzt. Die
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

let peopleCache = [];
let qbTree = { op: "and", children: [{ field: "person", value: "", label: "" }] };

async function loadPersonPicker() {
  if (!peopleCache.length) {
    try { peopleCache = await api("/api/persons"); } catch { peopleCache = []; }
  }
  renderBuilder();
}

function renderBuilder() {
  $("qb-root").innerHTML = "";
  $("qb-root").appendChild(renderGroup(qbTree, [], true));
  updateExpression();
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
  if (!qbTree.children.length) qbTree.children.push({ field: "person", value: "", label: "" });
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
  $("qb-expr").textContent = text ? `Fotos, die ${text}` : "Noch keine Bedingung — findet alle Fotos.";
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
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
  const s = state.stats;
  if (!s) return "";
  const pct = s.faces_total ? Math.round((s.faces_labeled / s.faces_total) * 100) : 0;
  return ` · ${s.faces_labeled} von ${s.faces_total} Gesichtern benannt (${pct} %)`;
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

const MONTHS = ["Januar","Februar","März","April","Mai","Juni",
                "Juli","August","September","Oktober","November","Dezember"];

const CHANNEL_LABEL = {
  camera: "eigene Aufnahmen",
  whatsapp: "empfangen",
  "whatsapp-sent": "verschickt",
  screenshot: "Screenshot",
  download: "heruntergeladen",
  document: "Dokument",
};

function monthLabel(ym) {
  const [y, m] = ym.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

function eventTitle(ev) {
  const folder = (ev.folders && ev.folders.length ? ev.folders.join(" · ") : ev.folder_name)
    || "Ohne Album";
  if (!ev.date) return folder;
  const [y, m, d] = ev.date.split("-");
  const when = m && d ? `${Number(d)}. ${MONTHS[Number(m) - 1]} ${y}` : y;
  return `${folder} · ${when}`;
}

// Uhrzeitspanne einer Serie. "14 Minuten, 122 Fotos" ist eine Fotoserie,
// "10 Stunden, 151 Fotos" ein durchgemachter Abend -- der Unterschied ist die
// nuetzlichste Information, die der Zeitstempel hergibt.
function eventWhen(ev) {
  if (ev.day_level || !ev.start) return "";
  const t = (iso) => iso.slice(11, 16);
  const span = ev.span_minutes;
  const range = span > 0 ? `${t(ev.start)}–${t(ev.end)}` : t(ev.start);
  if (!span) return range;
  const dur = span >= 90 ? `${(span / 60).toFixed(1).replace(".", ",")} h` : `${span} min`;
  return `${range} · ${dur}`;
}

function eventMeta(ev) {
  const bits = [];
  const when = eventWhen(ev);
  if (when) bits.push(when);
  bits.push(`${ev.photos.length} Foto${ev.photos.length === 1 ? "" : "s"}`);
  if (ev.channel && ev.channel !== "camera") {
    bits.push(CHANNEL_LABEL[ev.channel] || ev.channel);
  }
  return bits.join(" · ");
}

function peopleLine(ev) {
  const names = ev.person_names || [];
  if (!names.length) return "";
  const shown = names.slice(0, 4).map(escapeHtml).join(", ");
  const more = names.length > 4 ? ` +${names.length - 4}` : "";
  return `<p class="ev-people">${shown}${more}</p>`;
}

// Kanalfilter. Vorgabe "camera": das ist die Bibliothek im engeren Sinn.
// Empfangenes bleibt erreichbar, draengt sich aber nicht auf. Als
// Gliederungsebene taugt der Kanal nicht -- niemand sucht "alle Screenshots
// aus 2019" -- als Filter beantwortet er die Frage, die man wirklich hat.
const CHANNEL_FILTERS = [
  { key: "camera", label: "Eigene Aufnahmen" },
  { key: "whatsapp", label: "Empfangen" },
  { key: "whatsapp-sent", label: "Verschickt" },
  { key: "", label: "Alle" },
];
let channelFilter = "camera";
let lastGallery = null;

let lbPhotos = [], lbIndex = 0;

async function openPersonPhotos(p) {
  showPane("person-photos");
  $("pp-name").textContent = p.name;
  $("pp-face").src = `${cropUrl(p.cover_face_id)}?size=160`;
  $("pp-meta").textContent = "Lade Fotos …";
  $("pp-stream").innerHTML = "";
  $("pp-timeline").innerHTML = "";
  $("btn-check-faces").onclick = () => openPersonFaces(p);

  const data = await api(`/api/persons/${encodeURIComponent(p.id)}/photos`);
  const span = data.span ? ` · ${data.span.from.slice(0, 4)}–${data.span.to.slice(0, 4)}` : "";
  $("pp-meta").textContent = `${data.total} Foto${data.total === 1 ? "" : "s"}${span} · ${data.years.length} Jahre`;
  fillGallery($("pp-timeline"), $("pp-stream"), data, { selectable: true });
}

// Der Balken zeigt nicht nur wie viel, sondern woraus: ein Jahr aus 400
// eigenen Aufnahmen sieht anders aus als eines aus 400 Weiterleitungen -- und
// man sieht auf einen Blick, wo sich das Hinsehen lohnt.
function stackBar(y) {
  const chans = y.channels || {};
  const order = ["camera", "whatsapp", "whatsapp-sent", "screenshot", "download", "document"];
  return order.filter((c) => chans[c]).map((c) =>
    `<b class="ch-${c}" style="flex:${chans[c]}"></b>`).join("");
}

function yearTooltip(y) {
  const chans = y.channels || {};
  const parts = Object.keys(chans).map((c) => `${chans[c]} ${CHANNEL_LABEL[c] || c}`);
  return escapeHtml(`${y.count} Fotos` + (parts.length ? ` — ${parts.join(", ")}` : ""));
}

function filterByChannel(data, chan) {
  if (!chan) return data;
  const years = (data.years || []).map((y) => {
    const events = y.events.filter((e) => e.channel === chan);
    const months = (y.months || [])
      .map((m) => ({ ...m, events: m.events.filter((e) => e.channel === chan) }))
      .filter((m) => m.events.length)
      .map((m) => ({ ...m, count: m.events.reduce((n, e) => n + e.photos.length, 0) }));
    return {
      ...y, events, months,
      count: events.reduce((n, e) => n + e.photos.length, 0),
      channels: { [chan]: events.reduce((n, e) => n + e.photos.length, 0) },
    };
  }).filter((y) => y.events.length);
  return { ...data, years, total: years.reduce((n, y) => n + y.count, 0) };
}

function renderChannelBar(host, data, redraw) {
  const counts = {};
  (data.years || []).forEach((y) =>
    Object.entries(y.channels || {}).forEach(([c, n]) => { counts[c] = (counts[c] || 0) + n; }));
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  host.innerHTML = CHANNEL_FILTERS
    .filter((f) => !f.key || counts[f.key])
    .map((f) => {
      const n = f.key ? counts[f.key] : total;
      return `<button type="button" class="chan${f.key === channelFilter ? " on" : ""}"
                data-chan="${f.key}">${escapeHtml(f.label)} <em>${n}</em></button>`;
    }).join("");
  host.querySelectorAll(".chan").forEach((b) => b.addEventListener("click", () => {
    channelFilter = b.dataset.chan;
    redraw();
  }));
}

function fillGallery(timelineEl, streamEl, data, { selectable } = {}) {
  lastGallery = { timelineEl, streamEl, data, selectable };
  const bar = streamEl.previousElementSibling?.classList.contains("chanbar")
    ? streamEl.previousElementSibling
    : Object.assign(document.createElement("div"), { className: "chanbar" });
  if (!bar.parentNode) streamEl.parentNode.insertBefore(bar, streamEl);
  renderChannelBar(bar, data, () =>
    fillGallery(timelineEl, streamEl, data, { selectable }));

  data = filterByChannel(data, channelFilter);
  const years = data.years || [];
  const peak = Math.max(1, ...years.map((y) => y.count));
  timelineEl.innerHTML = years.map((y) => `
    <button type="button" class="tl" data-year="${escapeHtml(y.year)}"
            title="${yearTooltip(y)}">
      <i style="height:${Math.max(12, Math.round((y.count / peak) * 40))}px">${stackBar(y)}</i>
      <span>${escapeHtml(y.year)}</span>
      <em>${y.count}</em>
    </button>`).join("");
  timelineEl.querySelectorAll(".tl").forEach((b) => {
    b.addEventListener("click", () => {
      streamEl.querySelector(`#year-${CSS.escape(b.dataset.year)}`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  lbPhotos = [];
  streamEl.innerHTML = years.map((y) => `
    <section class="year" id="year-${escapeHtml(y.year)}">
      <h2 class="year-head"><span>${escapeHtml(y.year)}</span><em>${y.count} Fotos</em></h2>
      ${(y.months && y.months.length ? y.months : [{ month: "", events: y.events }])
        .map((mo) => `
        ${mo.month ? `<h3 class="month-head">${escapeHtml(monthLabel(mo.month))}
            <em>${mo.count}</em></h3>` : ""}
        ${mo.events.map((ev) => `
        <div class="event" data-channel="${escapeHtml(ev.channel || "camera")}">
          <h3>${escapeHtml(eventTitle(ev))}<em>${ev.photos.length}</em></h3>
          <p class="ev-when">${escapeHtml(eventMeta(ev))}</p>
          ${peopleLine(ev)}
          <div class="shots">
            ${ev.photos.map((ph) => {
              const i = lbPhotos.push(ph) - 1;
              return `<img loading="lazy" data-i="${i}"
                        src="/api/photos/${encodeURIComponent(ph.id)}/thumb?size=320"
                        alt="" title="${escapeHtml(ph.caption_de || "")}" />`;
            }).join("")}
          </div>
        </div>`).join("")}`).join("")}
    </section>`).join("");

  streamEl.querySelectorAll(".shots img").forEach((im) => {
    im.addEventListener("click", () => {
      if (selectable && selectMode) {
        const id = lbPhotos[Number(im.dataset.i)].id;
        photoSel.has(id) ? photoSel.delete(id) : photoSel.add(id);
        im.classList.toggle("picked", photoSel.has(id));
        updatePhotoSel();
        return;
      }
      openLightbox(Number(im.dataset.i));
    });
  });

  const obs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const year = e.target.id.replace("year-", "");
      timelineEl.querySelectorAll(".tl").forEach((b) => {
        b.classList.toggle("on", b.dataset.year === year);
      });
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  streamEl.querySelectorAll(".year").forEach((s) => obs.observe(s));
}

const DATE_SOURCE_LABEL = {
  exif: "aus den Bilddaten", filename: "aus dem Dateinamen",
  folder_name: "aus dem Albumnamen", folder: "aus dem Album",
  folder_json: "aus der Album-Datei", file_time: "geschätzt aus der Dateizeit",
};

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

async function annotateSelection(mode) {
  const n = photoSel.size;
  if (!n) return;
  const verb = mode === "remove" ? "entfernen von" : "vergeben an";
  const note = prompt(`Notiz ${verb} ${n} Foto${n === 1 ? "" : "s"}:\n(mehrere mit Komma trennen)`);
  if (!note || !note.trim()) return;
  const res = await api("/api/photos/annotate", {
    method: "POST",
    body: JSON.stringify({
      photo_ids: [...photoSel],
      annotations: note.split(",").map((s) => s.trim()).filter(Boolean),
      mode,
      reembed: true,
    }),
  });
  alert(`${res.changed} Fotos geändert, ${res.reembedded} neu eingebettet.`);
  clearPhotoSel();
  updatePhotoSel();
}

$("pp-tag").addEventListener("click", () => annotateSelection("add"));
$("pp-untag").addEventListener("click", () => annotateSelection("remove"));

$("pp-cap").addEventListener("click", async () => {
  const n = photoSel.size;
  if (!n) return;
  const text = prompt(`Beschreibung für ${n} Foto${n === 1 ? "" : "s"}:\n(überschreibt vorhandene und wird gegen neue Vision-Läufe gesperrt)`);
  if (!text || !text.trim()) return;
  const res = await api("/api/photos/caption/bulk", {
    method: "POST",
    body: JSON.stringify({ photo_ids: [...photoSel], caption_de: text.trim(), lock: true }),
  });
  alert(`${res.updated} Fotos beschriftet, ${res.reembedded} neu eingebettet.`);
  clearPhotoSel();
  updatePhotoSel();
});

function lbInfoOpen() {
  return localStorage.getItem("pv-lb-info") !== "0";
}

function setLbInfoOpen(on) {
  localStorage.setItem("pv-lb-info", on ? "1" : "0");
  $("lightbox").classList.toggle("info-off", !on);
  $("lb-reveal").classList.toggle("hidden", on);
}

function openLightbox(i) {
  lbIndex = Math.max(0, Math.min(i, lbPhotos.length - 1));
  const ph = lbPhotos[lbIndex];
  if (!ph) return;
  $("lb-img").src = `/api/photos/${encodeURIComponent(ph.id)}/thumb?size=1280`;
  const parts = [ph.caption_display, ph.caption_de].filter(Boolean);
  $("lb-cap").innerHTML = `${parts.map(escapeHtml).join("<br>")}
    <span class="muted"> — ${lbIndex + 1} von ${lbPhotos.length}</span>`;
  setLbInfoOpen(lbInfoOpen());
  $("lightbox").classList.remove("hidden");
  loadPhotoInfo(ph.id);
}

async function loadPhotoInfo(id) {
  const dl = $("lb-meta");
  dl.innerHTML = "<dt class='muted'>lädt …</dt><dd></dd>";
  let d;
  try { d = await api(`/api/photos/${encodeURIComponent(id)}`); }
  catch (e) { dl.innerHTML = `<dt class='muted'>Fehler</dt><dd>${escapeHtml(e.message)}</dd>`; return; }
  $("lb-info").dataset.photoId = id;

  const fmtSize = (b) => b ? `${(b / 1048576).toFixed(1)} MB` : null;
  const dateLine = d.date
    ? `${d.date}${d.date_source ? ` · ${DATE_SOURCE_LABEL[d.date_source] || d.date_source}` : ""}`
      + (d.date_confidence != null && d.date_confidence < 0.9 ? ` (unsicher)` : "")
    : null;

  // Jede Zeile ist zugleich ein mögliches Suchkriterium.
  const rows = [
    ["Aufnahmedatum", dateLine],
    ["Album", d.folder_name],
    ["Ereignis", d.caption_display],
    ["Personen", (d.person_names || []).join(", ")],
    ["Vermutlich", (d.person_suggestions || []).join(", ")],
    ["Gesichter erkannt", d.face_count],
    ["Notizen", (d.annotations || []).join(", ")],
    ["Szene", (d.scene_tags || []).join(", ")],
    ["Ort", d.location],
    ["Kamera", d.camera],
    ["Datei", d.file_name],
    ["Ordner", d.file_path ? d.file_path.replace(/\/[^/]+$/, "") : null],
    ["Nummer im Album", d.sequence_in_folder],
    ["Größe", fmtSize(d.file_size)],
    ["Datei geändert", d.file_mtime ? d.file_mtime.slice(0, 10) : null],
    ["Indiziert", d.ingested_at ? d.ingested_at.slice(0, 10) : null],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");

  dl.innerHTML = rows.map(([k, v]) =>
    `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd>`).join("");

  $("lb-caption").value = d.caption_de || "";
  $("lb-cap-state").textContent = d.caption_locked
    ? "von Hand — bleibt bei neuen Läufen erhalten"
    : (d.caption_de ? "vom Modell erzeugt" : "noch keine Beschreibung");
}

$("lb-toggle").addEventListener("click", () => setLbInfoOpen(false));
$("lb-reveal").addEventListener("click", () => setLbInfoOpen(true));

$("lb-save-caption").addEventListener("click", async () => {
  const id = $("lb-info").dataset.photoId;
  if (!id) return;
  $("lb-cap-state").textContent = "speichert …";
  const res = await api(`/api/photos/${encodeURIComponent(id)}/caption`, {
    method: "POST",
    body: JSON.stringify({ caption_de: $("lb-caption").value, lock: true }),
  });
  $("lb-cap-state").textContent = res.locked
    ? "gespeichert — bleibt bei neuen Läufen erhalten"
    : "gespeichert";
  const ph = lbPhotos[lbIndex];
  if (ph) ph.caption_de = res.caption_de;
});

function closeLightbox() { $("lightbox").classList.add("hidden"); }
function stepLightbox(d) { if (!$("lightbox").classList.contains("hidden")) openLightbox(lbIndex + d); }

document.querySelector(".lb-close").addEventListener("click", closeLightbox);
document.querySelector(".lb-prev").addEventListener("click", () => stepLightbox(-1));
document.querySelector(".lb-next").addEventListener("click", () => stepLightbox(1));
$("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeLightbox(); });
document.addEventListener("keydown", (e) => {
  if ($("lightbox").classList.contains("hidden")) return;
  if (e.target.tagName === "TEXTAREA") return;  // beim Tippen nicht weiterblättern
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") stepLightbox(-1);
  if (e.key === "ArrowRight") stepLightbox(1);
  if (e.key === "i") setLbInfoOpen(!lbInfoOpen());
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
  if (!n || !confirm(`${n} Gesicht${n === 1 ? "" : "er"} aus dieser Person entfernen?\n\nSie wandern zurück in „Wer ist das?“.`)) return;
  const res = await api("/api/persons/faces/unassign", {
    method: "POST",
    body: JSON.stringify({ face_ids: [...selectedFaces] }),
  });
  alert(`${res.freed} entfernt, ${res.photos_updated} Fotos aktualisiert.`);
  reopenCurrentPerson();
});

$("btn-move").addEventListener("click", async () => {
  const n = selectedFaces.size;
  if (!n) return;
  const name = prompt(`${n} Gesicht${n === 1 ? "" : "er"} welcher Person zuordnen?\n(bestehender oder neuer Name)`);
  if (!name || !name.trim()) return;
  const res = await api("/api/persons/faces/move", {
    method: "POST",
    body: JSON.stringify({ face_ids: [...selectedFaces], name: name.trim() }),
  });
  alert(`${res.moved} verschoben zu „${res.to.name}“, ${res.photos_updated} Fotos aktualisiert.`);
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
  const name = prompt(`Neuer Name für „${p.name}“`, p.name);
  if (!name || name.trim() === p.name) return;
  const res = await api(`/api/persons/${encodeURIComponent(p.id)}/rename`, {
    method: "POST",
    body: JSON.stringify({ name: name.trim() }),
  });
  alert(`Umbenannt: ${res.faces} Gesichter, ${res.photos} Fotos aktualisiert.`);
  loadPeople();
}

/* Spitznamen sind zusätzliche Sucheinstiege -- der echte Name bleibt der eine,
   saubere Eintrag, „Karo“ findet trotzdem „Annika Wolf“. */
async function editAliases(p) {
  const current = (p.aliases || []).join(", ");
  const input = prompt(
    `Spitznamen für „${p.name}“\n(mit Komma trennen, leer lassen zum Entfernen)\n\n` +
    `Damit findet die Suche die Person auch unter diesen Namen.`,
    current
  );
  if (input === null) return;
  const res = await api(`/api/persons/${encodeURIComponent(p.id)}/aliases`, {
    method: "POST",
    body: JSON.stringify({ aliases: input.split(",").map((s) => s.trim()).filter(Boolean) }),
  });
  peopleCache = [];  // Picker im Suchtab neu laden
  alert(res.aliases.length
    ? `Gespeichert: „${res.aliases.join("“, „")}“`
    : "Spitznamen entfernt.");
  loadPeople();
}

async function unassignPerson(p) {
  if (!confirm(
    `Zuordnung von „${p.name}“ auflösen?\n\n` +
    `${p.face_count} Gesichter wandern zurück in „Wer ist das?“. ` +
    `Die Fotos bleiben, nur der Name wird entfernt.`
  )) return;
  const res = await api(`/api/persons/${encodeURIComponent(p.id)}`, { method: "DELETE" });
  alert(`Gelöst: ${res.faces_freed} Gesichter zurück in die Queue, ${res.photos} Fotos bereinigt.`);
  loadPeople();
}

$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = await api("/api/search/query", {
    method: "POST",
    body: JSON.stringify({
      query: qbTree,
      caption_query: $("q-text").value.trim() || null,
      limit: 48,
    }),
  });
  // Die Fassung vom Server ist die verbindliche -- sie stammt aus demselben
  // Baum, aus dem der Filter gebaut wurde.
  $("qb-expr").textContent =
    `Fotos, die ${data.expression} — ${data.total} Treffer` +
    (data.conditions ? ` · ${data.conditions} Bedingung${data.conditions === 1 ? "" : "en"}` : "");
  renderResults(data.results || []);
});

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
    box.insertAdjacentHTML("beforeend", "<p class='muted'>Keine Treffer.</p>");
    return;
  }
  results.forEach((r, i) => {
    const el = document.createElement("figure");
    el.className = "hit";
    const head = r.caption_display || [r.date, r.folder_name].filter(Boolean).join(" · ");
    const names = (r.person_names || []).join(", ");
    const notes = (r.annotations || []).map((a) => `<span class="note">${escapeHtml(a)}</span>`).join("");
    // Die erste Reihe eager: lazy laedt erst bei Sichtbarkeit, was je nach
    // Umgebung gar nicht ausloest und leere Kacheln hinterlaesst.
    const loading = i < 12 ? "eager" : "lazy";
    el.innerHTML = `
      <img loading="${loading}" src="/api/photos/${encodeURIComponent(r.id)}/thumb?size=320" alt="" />
      <figcaption>
        <div class="muted">${escapeHtml(head)}</div>
        ${names ? `<div class="names">${escapeHtml(names)}</div>` : ""}
        ${r.caption_de ? `<p>${escapeHtml(r.caption_de)}</p>` : ""}
        ${notes ? `<div class="notes">${notes}</div>` : ""}
      </figcaption>`;
    el.querySelector("img").addEventListener("click", () => {
      lbPhotos = results;
      openLightbox(i);
    });
    box.appendChild(el);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

loadQueue().catch((err) => {
  $("queue-meta").textContent = `API nicht erreichbar: ${err.message}`;
});


/* ---- Unbenannte Serien -------------------------------------------------
   Gegenstück zu "Wer ist das?": das System bildet Gruppen, der Mensch
   erkennt sie. Absteigend nach Größe, weil dort der Ertrag je Entscheidung
   am höchsten ist — eine Serie mit 150 Fotos ordnet mehr als dreißig
   Zweiergrüppchen. */

let evChannel = "camera";

function evWhen(ev) {
  if (ev.day_level || !ev.start) return "Uhrzeit unbekannt";
  const t = (iso) => iso.slice(11, 16);
  const span = ev.span_minutes;
  const dur = span >= 90 ? `${(span / 60).toFixed(1).replace(".", ",")} h`
            : span > 0 ? `${span} min` : "";
  return `${t(ev.start)}${span > 0 ? `–${t(ev.end)}` : ""}${dur ? ` · ${dur}` : ""}`;
}

function evDate(iso) {
  if (!iso) return "ohne Datum";
  const [y, m, d] = iso.split("-");
  return `${Number(d)}. ${MONTHS[Number(m) - 1]} ${y}`;
}

async function loadEvents() {
  const host = $("ev-list");
  host.innerHTML = "<p class='muted'>Lade …</p>";
  $("ev-chan").innerHTML = CHANNEL_FILTERS
    .map((f) => `<button type="button" class="chan${f.key === evChannel ? " on" : ""}"
                   data-chan="${f.key}">${escapeHtml(f.label)}</button>`).join("");
  $("ev-chan").querySelectorAll(".chan").forEach((b) =>
    b.addEventListener("click", () => { evChannel = b.dataset.chan; loadEvents(); }));

  let d;
  try {
    // Alle laden statt zu blaettern: die Vorschaubilder haengen an
    // loading="lazy", es kommt also nur an, was man auch ansieht -- und beim
    // Durcharbeiten will man nicht alle 40 Serien nachladen muessen.
    d = await api(`/api/events/unnamed?limit=400&channel=${encodeURIComponent(evChannel)}`);
  } catch (err) {
    host.innerHTML = `<p class='muted'>Serien konnten nicht geladen werden (${escapeHtml(String(err.message || err).slice(0, 140))})</p>`;
    return;
  }
  const gezeigt = d.events.length;
  $("ev-meta").textContent =
    `${d.unnamed} von ${d.total_events} Serien ohne Namen · ${d.photos_unnamed} Fotos` +
    (gezeigt < d.unnamed ? ` · ${gezeigt} geladen` : "");

  if (!d.events.length) {
    host.innerHTML = "<p class='muted'>Alles benannt.</p>";
    return;
  }
  host.innerHTML = d.events.map((ev, i) => `
    <div class="serie" data-i="${i}">
      <div class="serie-head">
        <div>
          <strong>${escapeHtml(ev.folders.join(" · ") || "Ohne Album")}</strong>
          <span class="muted">${escapeHtml(evDate(ev.date))} · ${escapeHtml(evWhen(ev))} · ${ev.size} Fotos</span>
          ${ev.person_names.length
            ? `<span class="ev-people">${escapeHtml(ev.person_names.slice(0, 5).join(", "))}${ev.person_names.length > 5 ? ` +${ev.person_names.length - 5}` : ""}</span>`
            : ""}
        </div>
        <form class="serie-form">
          <input type="text" list="dl-events" autocomplete="off"
                 value="${escapeHtml(ev.suggested_name || "")}"
                 placeholder="z. B. Max 30. Geburtstag" />
          <button type="submit">${ev.suggested_name ? "Bestätigen" : "Benennen"}</button>
        </form>
      </div>
      <div class="shots">
        ${ev.cover.map((id) => `<img loading="lazy" alt=""
            src="/api/photos/${encodeURIComponent(id)}/thumb?size=200" />`).join("")}
      </div>
    </div>`).join("");

  host.querySelectorAll(".serie").forEach((el) => {
    const ev = d.events[Number(el.dataset.i)];
    el.querySelector(".serie-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = el.querySelector("input");
      const name = input.value.trim();
      if (!name) return;
      input.disabled = true;
      try {
        await api("/api/events/name", {
          method: "POST",
          body: JSON.stringify({
            name, channel: ev.channel, start: ev.start, end: ev.end,
            photo_count: ev.size,
          }),
        });
        el.remove();
        refreshEventNames();
      } catch (err) {
        input.disabled = false;
        alert(`Konnte nicht gespeichert werden: ${err.message || err}`);
      }
    });
  });
}


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
      if (!confirm(`${b.count} Gesichter als „${b.name}" bestätigen?`)) return;
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
        alert(`Konnte nicht zugeordnet werden: ${err.message || err}`);
      }
    });
  });
}


/* ---- Vorschlagslisten --------------------------------------------------
   Ein Vertipper legt sonst still eine zweite Identitaet an: "Annika Wolf"
   und "Annika Glass" waeren zwei Personen, und niemand bemerkt es, bis die
   Fotos auf zwei Karten verteilt sind. Dasselbe bei Serien.

   Bewusst <datalist> und kein erzwungenes Auswaehlen: neue Namen muessen
   moeglich bleiben, das ist ja der Normalfall beim Benennen. */

function fillDatalist(id, values) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = [...new Set(values.filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, "de")).map((v) => `<option value="${escapeHtml(v)}"></option>`).join("");
}

async function refreshPersonNames() {
  try {
    const d = await api("/api/persons");
    const list = d.persons || d;
    fillDatalist("dl-persons", (Array.isArray(list) ? list : [])
      .map((p) => p.name)
      // Ablagen fuer Aussortiertes sind keine Namensvorschlaege.
      .filter((n) => n && n !== "Übersprungen" && n !== "Ignoriert"));
  } catch (err) { /* Vorschlaege sind Komfort, kein Muss */ }
}

async function refreshEventNames() {
  try {
    const d = await api("/api/events/named?limit=500");
    fillDatalist("dl-events", (d.events || []).map((e) => e.name));
  } catch (err) { /* siehe oben */ }
}

refreshPersonNames();
refreshEventNames();
