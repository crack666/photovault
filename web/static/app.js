import { api, cropUrl } from "./core/api.js";
import { $, escapeHtml } from "./core/dom.js";
import { watchMedia } from "./core/images.js";
import { faceStatsLine } from "./core/format.js";
import { notify } from "./core/modal.js";
import { refreshEventNames, refreshPersonNames } from "./core/names.js";
import { rememberTab, renderNav, tabFromUrl, photoFromUrl } from "./core/nav.js";
import { forgetPeopleList } from "./core/people.js";
import { bindEvents, loadEventTab } from "./events/index.js";
import { bindLightbox, showLightbox } from "./lightbox/index.js";
import { bindPeople, loadPeople, openPersonFromUrl } from "./people/index.js";
import { bindSearch, loadPersonPicker } from "./search/index.js";
import { bindUnknown, loadCandidates, loadUnknown } from "./unknown/index.js";

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

   Das `` an jedem Import ist kein Schmuck: ein `import "./model.js"` ohne
   Parameter liefert aus dem Browser-Cache beliebig lange die alte Fassung,
   auch wenn index.html schon die neue erwartet. Genau das ist passiert. Die
   Zahl gilt fuer alle Module gemeinsam und wird gemeinsam erhoeht.

   Die Grossansicht bekam er frueher hineingereicht, weil sie in app.js lag
   und app.js nichts exportiert. Seit sie ein eigenes Modul ist, importiert
   er sie selbst -- der deps-Umweg ist ersatzlos entfallen. */
async function openAtlas() {
  const { initAtlas } = await import("./atlas/index.js");
  await initAtlas();
}

let trashBound = false;
async function openTrash() {
  const mod = await import("./trash/index.js");
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
  // Steht dauerhaft sichtbar oben: ein Klick darauf ist die naheliegende
  // Geste fuer "zeig mir das ganze Foto".
  $("cover").dataset.face = faceId;
  $("cover").title = "Ganzes Foto ansehen";
  $("cover").src = `${cropUrl(faceId)}?size=640`;
  $("thumbs").querySelectorAll("figure").forEach((f) => {
    f.classList.toggle("on", f.dataset.id === faceId);
  });
}

/* Wie viele Kacheln auf einmal in den DOM wandern.

   Vorher standen alle auf einmal da -- bei einer grossen Gruppe 1.244 Stueck
   und ein 21.000 px hohes Dokument. `loading="lazy"` half dagegen nicht
   genug: gemessen am 27.10.2026 feuerte der Browser allein fuer den ersten
   Bildschirm 112 Anfragen, beim Scrollen hunderte am Stueck. Jede kalte davon
   laesst den Server das Original oeffnen (photovault#34).

   Nachgelegt wird beim Scrollen, **nicht** blaetterweise: wer 1.200 Gesichter
   mit dem Auge ueberfliegt, soll das in einem Zug tun und einmal bestaetigen.
   Die Auswahl haengt ohnehin an `cluster.face_ids`, nicht am DOM -- was
   gezeigt wird, aendert also nichts daran, was bestaetigt wird. */
/* Das ganze Foto zu einem Gesicht.

   Auf dieser Seite sieht man nur den Zuschnitt. Bei einem Gesicht, das halb
   hinter einer Wand steht oder im Hintergrund verschwimmt, laesst sich daran
   nicht entscheiden, ob es in die Gruppe gehoert -- dafuer braucht es das
   Bild drumherum. Die Foto-Kennung kommt erst auf Klick: eine Gruppe hat bis
   zu 1.244 Gesichter, und sie allen mitzugeben waere Last fuer einen Fall,
   der selten eintritt. */
async function zeigeGanzesFoto(faceId) {
  try {
    const d = await api(`/api/faces/${encodeURIComponent(faceId)}/photo`);
    showLightbox([{ id: d.photo_id, kind: d.kind, file_name: d.file_name }], 0);
  } catch (e) {
    notify(`Foto nicht ladbar: ${e.message}`);
  }
}

const KACHEL_STAPEL = 150;

let thumbWache = null;

/* Jedes Gesicht des Clusters ist anklickbar: groß ansehen, und wenn es nicht
   dazugehört, hier gleich ausschließen -- sonst zieht ein Fremder mit ein. */
function renderThumbs(cluster) {
  const box = $("thumbs");
  if (thumbWache) { thumbWache.disconnect(); thumbWache = null; }
  box.innerHTML = "";

  const kachel = (id) => {
    const fig = document.createElement("figure");
    fig.className = "thumb";
    fig.dataset.id = id;
    fig.classList.toggle("out", cluster.excluded.has(id));
    fig.innerHTML = `<img loading="lazy" src="${cropUrl(id)}?size=160" alt="" />
                     <button type="button" class="drop" title="Gehört nicht dazu">✕</button>
                     <button type="button" class="gross"
                             title="Ganzes Foto ansehen (oder Doppelklick)">⤢</button>`;
    fig.querySelector("img").addEventListener("click", () => showCover(cluster, id));
    // Einfacher Klick zeigt den Zuschnitt oben gross; das ganze Foto braucht
    // es erst, wenn der Ausschnitt die Frage nicht beantwortet -- ein Gesicht
    // halb hinter einer Wand etwa.
    fig.querySelector("img").addEventListener("dblclick", (e) => {
      e.preventDefault();
      zeigeGanzesFoto(id);
    });
    fig.querySelector(".gross").addEventListener("click", (e) => {
      e.stopPropagation();
      zeigeGanzesFoto(id);
    });
    fig.querySelector(".drop").addEventListener("click", (e) => {
      e.stopPropagation();
      cluster.excluded.has(id) ? cluster.excluded.delete(id) : cluster.excluded.add(id);
      fig.classList.toggle("out", cluster.excluded.has(id));
      updateClusterCount(cluster);
    });
    return fig;
  };

  const wache = document.createElement("div");
  wache.className = "thumb-wache";
  box.appendChild(wache);

  let gezeigt = 0;
  const nachlegen = () => {
    const bis = Math.min(gezeigt + KACHEL_STAPEL, cluster.face_ids.length);
    const stueck = document.createDocumentFragment();
    for (; gezeigt < bis; gezeigt++) stueck.appendChild(kachel(cluster.face_ids[gezeigt]));
    box.insertBefore(stueck, wache);
    if (gezeigt >= cluster.face_ids.length) {
      thumbWache.disconnect();
      thumbWache = null;
      wache.remove();
    }
  };

  // Vorlauf, damit beim Scrollen nichts leer bleibt -- aber nicht so viel,
  // dass wieder alles auf einmal kommt.
  thumbWache = new IntersectionObserver(
    (eintraege) => { if (eintraege.some((e) => e.isIntersecting)) nachlegen(); },
    { rootMargin: "800px" },
  );
  thumbWache.observe(wache);
  nachlegen();

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

loadQueue().catch((err) => {
  $("queue-meta").textContent = `API nicht erreichbar: ${err.message}`;
});


refreshPersonNames();
refreshEventNames();

// Die Tasten und Knoepfe der Grossansicht einmal binden. Frueher geschah das
// beim Auswerten von app.js, weil die Listener dort standen; jetzt sagt es
// der Router ausdruecklich -- und zwar bevor der erste Tab aufgeht.
bindLightbox();

$("cover").addEventListener("click", () => {
  const id = $("cover").dataset.face;
  if (id) zeigeGanzesFoto(id);
});
bindEvents();
bindUnknown();
bindSearch();
bindPeople();
watchMedia();

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
const startTab = tabFromUrl();
showTab(startTab);
if (startTab === "people") openPersonFromUrl();
const photo = photoFromUrl();
if (photo) showLightbox([{ id: photo }], 0);
