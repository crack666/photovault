/* Unbekannte Gesichter -- der Stapel, den man abarbeitet.

   Zwei Ansichten in einem Reiter: die Uebersicht ueber alle Gruppen und die
   Detailansicht einer einzelnen. Dazu die Kandidaten -- Gesichter, die
   jemandem aehneln, der schon einen Namen hat.

   Der Block braucht nichts aus app.js, nur Bausteine: die Galerie fuer die
   Fotos einer Gruppe, die Personenliste, die Vorschlagsliste. */

import { api, cropUrl } from "../core/api.js?v=55";
import { $, escapeHtml } from "../core/dom.js?v=55";
import { faceStatsLine } from "../core/format.js?v=55";
import { askConfirm, askText, notify } from "../core/modal.js?v=55";
import { refreshPersonNames } from "../core/names.js?v=55";
import { forgetPeopleList } from "../core/people.js?v=55";
import { fillGallery } from "../gallery/index.js?v=55";

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

export async function loadUnknown(reset) {
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
  forgetPeopleList();
  refreshPersonNames();
  showUkPane("uk-overview");
  loadUnknown(true);
}


function updateUkSel() {
  $("uk-sel").textContent = ukSel.size ? `${ukSel.size} gewählt` : "nichts gewählt";
}




/* ---- Bereits benannte Personen im Unbekannt-Stapel ---------------------
   Knapp ein Drittel der "unbekannten" Gesichter gehoert zu Leuten, die
   laengst benannt sind. In der Cluster-Ansicht erscheinen sie als dutzende
   Kleingruppen derselben Person -- hier als eine Rueckfrage je Person. */

export async function loadCandidates() {
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
        forgetPeopleList();
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

/* Neun Bindungen, einmal -- wie in lightbox/, events/ und trash/. */
let bound = false;

export function bindUnknown() {
  if (bound) return;
  bound = true;

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
}
