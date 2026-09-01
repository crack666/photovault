import { api, cropUrl } from "./core/api.js?v=55";
import { $, escapeHtml } from "./core/dom.js?v=55";
import { faceStatsLine } from "./core/format.js?v=55";
import { askConfirm, askText, notify } from "./core/modal.js?v=55";
import { refreshEventNames, refreshPersonNames } from "./core/names.js?v=55";
import { rememberTab, renderNav, tabFromUrl } from "./core/nav.js?v=55";
import { forgetPeopleList, setPeopleList } from "./core/people.js?v=55";
import { bindEvents, loadEventTab } from "./events/index.js?v=55";
import { fillGallery } from "./gallery/index.js?v=55";
import { bindLightbox } from "./lightbox/index.js?v=55";
import { bindSearch, loadPersonPicker } from "./search/index.js?v=55";
import { bindUnknown, loadCandidates, loadUnknown } from "./unknown/index.js?v=55";

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
  /* Hier entsteht ein Name, den es vorher nicht gab -- also muessen ihn beide
     Listen erfahren. Genau das fehlte: assignUnknown machte denselben POST und
     erneuerte beides, dieser Weg -- der Hauptweg der Warteschlange -- keines
     von beiden. Ein gerade vergebener Name stand in der Vorschlagsliste erst
     nach dem Neuladen der Seite. */
  forgetPeopleList();
  refreshPersonNames();
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
  forgetPeopleList();
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
  setPeopleList(people);
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
    forgetPeopleList();  // Picker im Suchtab neu laden
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

loadQueue().catch((err) => {
  $("queue-meta").textContent = `API nicht erreichbar: ${err.message}`;
});


refreshPersonNames();
refreshEventNames();

// Die Tasten und Knoepfe der Grossansicht einmal binden. Frueher geschah das
// beim Auswerten von app.js, weil die Listener dort standen; jetzt sagt es
// der Router ausdruecklich -- und zwar bevor der erste Tab aufgeht.
bindLightbox();
bindEvents();
bindUnknown();
bindSearch();

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
