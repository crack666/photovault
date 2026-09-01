/* Personen: die Liste, die Fotos einer Person, ihre Gesichter.

   Drei Ansichten hintereinander -- Uebersicht, Fotostrom, Gesichtspruefung --
   die sich einen Umschalter teilen (showPane). Dazu die Fotoauswahl, mit der
   man mehrere Bilder auf einmal notiert oder beschriftet.

   Die Fotoauswahl haengt an der Galerie nur ueber deren onPick-Rueckweg: sie
   sagt "der Klick ist verbraucht", und die Galerie laesst die Grossansicht
   dann zu. Die Galerie kennt die Auswahl nicht. */

import { api, cropUrl } from "../core/api.js?v=65";
import { $, escapeHtml } from "../core/dom.js?v=65";
import { askConfirm, askText, notify } from "../core/modal.js?v=65";
import { forgetPeopleList, setPeopleList } from "../core/people.js?v=65";
import { fillGallery } from "../gallery/index.js?v=65";

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

export async function loadPeople() {
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


function clearPhotoSel() {
  photoSel.clear();
  $("pp-stream").querySelectorAll(".picked").forEach((e) => e.classList.remove("picked"));
}

function updatePhotoSel() {
  $("pp-sel").textContent = photoSel.size
    ? `${photoSel.size} Foto${photoSel.size === 1 ? "" : "s"} gewählt`
    : "Fotos anklicken zum Auswählen";
}


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



function showPane(id) {
  ["people-overview", "person-photos", "person-detail"].forEach((p) => {
    $(p).classList.toggle("hidden", p !== id);
  });
}


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

/* Die Bindungen des Reiters, einmal. */
let bound = false;

export function bindPeople() {
  if (bound) return;
  bound = true;

  $("btn-select-mode").addEventListener("click", () => {
    selectMode = !selectMode;
    $("btn-select-mode").classList.toggle("on", selectMode);
    $("btn-select-mode").textContent = selectMode ? "Auswahl beenden" : "Fotos auswählen";
    $("pp-stream").classList.toggle("selecting", selectMode);
    $("pp-actions").classList.toggle("hidden", !selectMode);
    if (!selectMode) clearPhotoSel();
    updatePhotoSel();
  });
  $("pp-clear").addEventListener("click", () => { clearPhotoSel(); updatePhotoSel(); });
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
  document.querySelectorAll(".back-people").forEach((b) => {
    b.addEventListener("click", () => { showPane("people-overview"); loadPeople(); });
  });
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
}
