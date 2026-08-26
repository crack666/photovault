/* Der Papierkorb: zweite Stufe des Löschens.

   Die erste Stufe im Atlas fasst keine Datei an — sie setzt einen Vermerk.
   Hier steht, was vorgemerkt ist, und hier ist der einzige Ort, an dem etwas
   wirklich verschwindet.

   Das Wichtigste ist deshalb nicht der Löschknopf, sondern was daneben steht:
   wie viele der Vorgemerkten eine Beschreibung tragen und auf wie vielen ein
   benannter Mensch ist. Das ist die Arbeit, die mitverloren geht — und man
   soll es vorher wissen, nicht hinterher. */

import { $, escapeHtml, num } from "../core/dom.js?v=17";
import { api, thumbUrl } from "../core/api.js?v=17";
import { openModal } from "../core/modal.js?v=17";

const PAGE = 60;

let state = null;
let picked = new Set();
let limit = PAGE;
let showLightbox = () => {};
let booted = false;

export async function initTrash(deps = {}) {
  showLightbox = deps.showLightbox || showLightbox;
  booted = true;
  await refresh();
}

async function refresh() {
  const host = $("view-trash");
  if (!host) return;
  try {
    state = await api(`/api/trash?limit=${limit}`);
  } catch (e) {
    $("trash-meta").textContent = `Papierkorb nicht erreichbar: ${e.message}`;
    return;
  }
  picked = new Set([...picked].filter((id) => state.photos.some((p) => p.id === id)));
  paint();
}

function paint() {
  const { total, bytes, photos, with_caption: withCap, with_person: withPerson } = state;

  $("trash-meta").innerHTML = total
    ? `<b>${num(total)}</b> Fotos vorgemerkt · ${(bytes / 1048576).toFixed(1)} MB`
    : "Der Papierkorb ist leer.";

  // Was beim Löschen mitverloren geht. Steht *vor* dem Knopf, nicht dahinter.
  const cost = [];
  if (withCap) cost.push(`${num(withCap)} mit Beschreibung`);
  if (withPerson) cost.push(`${num(withPerson)} mit benannter Person`);
  $("trash-cost").textContent = cost.length
    ? `Davon ${cost.join(", ")} — diese Arbeit ist danach weg.`
    : "";
  $("trash-cost").classList.toggle("hidden", !cost.length);

  $("trash-actions").classList.toggle("hidden", !total);
  $("trash-pick").textContent = picked.size ? `${num(picked.size)} gewählt` : "";
  $("trash-restore").disabled = !picked.size;
  $("trash-empty").textContent = picked.size
    ? `${num(picked.size)} endgültig löschen`
    : `Alle ${num(total)} endgültig löschen`;

  $("trash-grid").innerHTML = photos.map((p) => `
    <figure class="trash-item${picked.has(p.id) ? " on" : ""}" data-id="${escapeHtml(p.id)}">
      <img src="${thumbUrl(p.id, 320)}" alt="" loading="lazy">
      <figcaption>
        <span class="when">${escapeHtml(p.date || "ohne Datum")}</span>
        ${p.person_names.length
          ? `<span class="who">${escapeHtml(p.person_names.join(", "))}</span>` : ""}
        <span class="what">${escapeHtml(p.caption_de || p.caption_display || "")}</span>
      </figcaption>
    </figure>`).join("");

  $("trash-more").classList.toggle("hidden", state.returned >= total);
  $("trash-more").textContent = `Mehr laden (${num(total - state.returned)} weitere)`;
}

/* ---- Bedienung --------------------------------------------------------- */

export function bindTrash() {
  $("trash-grid").addEventListener("click", (e) => {
    const fig = e.target.closest(".trash-item");
    if (!fig) return;
    const id = fig.dataset.id;
    // Klick auf das Bild öffnet, Klick auf den Rand wählt — sonst kann man
    // nicht nachsehen, was man da eigentlich wegwirft.
    if (e.target.tagName === "IMG" && !e.shiftKey) {
      const idx = state.photos.findIndex((p) => p.id === id);
      showLightbox(state.photos.map((p) => ({ id: p.id })), Math.max(0, idx));
      return;
    }
    if (picked.has(id)) picked.delete(id);
    else picked.add(id);
    paint();
  });

  $("trash-more").onclick = () => { limit += PAGE; refresh(); };
  $("trash-all").onclick = () => {
    if (picked.size === state.photos.length) picked.clear();
    else picked = new Set(state.photos.map((p) => p.id));
    paint();
  };

  $("trash-restore").onclick = async () => {
    await act("/api/trash", { photo_ids: [...picked], trashed: false },
              (r) => `${num(r.restored)} gerettet.`);
  };

  $("trash-empty").onclick = async () => {
    const ids = picked.size ? [...picked] : state.photos.map((p) => p.id);
    const all = !picked.size;
    const count = all ? state.total : ids.length;
    const dlg = openModal({
      title: `${num(count)} Fotos endgültig löschen`,
      lead: "Datei, Indexeintrag, Gesichter und Vorschaubild — <b>es gibt kein Zurück</b>. "
          + "Die gelöschten Pfade werden vorher in ein Protokoll unter <code>logs/</code> "
          + "geschrieben; rückgängig macht das nichts, aber danach ist nachvollziehbar, was weg ist.",
      body: `${state.with_caption || state.with_person ? `
        <p class="gate">Im Papierkorb liegen ${num(state.with_caption)} Fotos mit
        Beschreibung und ${num(state.with_person)} mit benannter Person.</p>` : ""}
        <label class="mv-hide">
          <input type="checkbox" id="trash-sure"> Ja, endgültig löschen
          <span>Ohne Haken passiert nichts.</span>
        </label>`,
      buttons: [{ id: "cancel", label: "Abbrechen" },
                { id: "go", label: "Löschen", kind: "danger" }],
    });
    const sure = $("trash-sure");
    if (await dlg.wait() !== "go") return;
    if (!sure?.checked) { note("Ohne Haken wurde nichts gelöscht."); return; }
    await act("/api/trash/empty",
              { photo_ids: all ? [] : ids, confirm: true },
              (r) => `${num(r.deleted)} gelöscht, ${num(r.files)} Dateien entfernt`
                   + `${r.failed?.length ? `, ${num(r.failed.length)} fehlgeschlagen` : ""}.`
                   + ` Protokoll: ${r.log}`);
  };
}

async function act(path, body, done) {
  note("läuft …");
  try {
    const res = await api(path, { method: "POST", body: JSON.stringify(body) });
    note(done(res));
  } catch (e) {
    note(`Fehlgeschlagen: ${e.message}`);
  }
  picked.clear();
  await refresh();
}

function note(text) {
  const el = $("trash-note");
  if (el) el.textContent = text;
}

export function trashBooted() {
  return booted;
}
