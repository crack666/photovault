/* Der Papierkorb: zweite Stufe des Löschens.

   Die erste Stufe im Atlas fasst keine Datei an — sie setzt einen Vermerk.
   Hier steht, was vorgemerkt ist, und hier ist der einzige Ort, an dem etwas
   wirklich verschwindet.

   Das Wichtigste ist deshalb nicht der Löschknopf, sondern was daneben steht:
   wie viele der Vorgemerkten eine Beschreibung tragen und auf wie vielen ein
   benannter Mensch ist. Das ist die Arbeit, die mitverloren geht — und man
   soll es vorher wissen, nicht hinterher. */

import { $, escapeHtml, num } from "../core/dom.js?v=63";
import { api, thumbUrl } from "../core/api.js?v=63";
import { openModal } from "../core/modal.js?v=63";
import { showLightbox } from "../lightbox/index.js?v=63";

const PAGE = 60;

let state = null;
let picked = new Set();
let limit = PAGE;
//: Wie viele Kacheln im Raster stehen. Nachladen haengt nur die neuen an --
//: baute es alles neu, spraenge die Bildlaufleiste nach oben, und das ist
//: beim Nachladen noch schlimmer als beim Zurueckholen: man ist ja gerade
//: unten angekommen.
let rendered = 0;
let loading = false;
let booted = false;

export async function initTrash() {
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

/* Nachladen, sobald das Ende in Sicht kommt.

   Der Knopf "Mehr laden" war ein Klick, den niemand braucht: wer unten
   ankommt, will weiterlesen. Er bleibt trotzdem stehen -- als Anzeige, wie
   viel noch kommt, und als Ausweg, wenn der Beobachter nicht greift.

   Die Marken der zurueckgeholten Fotos muessen den Neuabruf ueberleben: der
   Server kennt sie nicht mehr als vorgemerkt, aber ihre Kachel soll
   ausgegraut stehenbleiben, bis man die Seite neu laedt. */
async function loadMore() {
  if (loading || state.returned >= state.total + countRestored()) return;
  loading = true;
  const zurueck = new Map(state.photos.filter((p) => p.restored).map((p) => [p.id, true]));
  const vorher = rendered;
  try {
    limit += PAGE;
    state = await api(`/api/trash?limit=${limit}`);
  } catch (e) {
    note(`Nachladen fehlgeschlagen: ${e.message}`);
    loading = false;
    paintMeta();
    return;
  }
  for (const p of state.photos) if (zurueck.has(p.id)) p.restored = true;
  const neu = state.photos.slice(vorher);
  if (neu.length) {
    $("trash-grid").insertAdjacentHTML("beforeend", neu.map(tileHtml).join(""));
    rendered = state.photos.length;
  }
  // Erst freigeben, dann zeichnen: die Zeile liest `loading`, und andersherum
  // blieb "lädt …" stehen, obwohl längst geladen war.
  loading = false;
  paintMeta();
}

function countRestored() {
  return state.photos.filter((p) => p.restored).length;
}

function paintMeta() {
  const { total, bytes, with_caption: withCap, with_person: withPerson } = state;

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

  // Zurueckgeholtes zaehlt nicht mehr zu `total`, steht aber noch im Raster --
  // ohne diesen Ausgleich meldete die Zeile "noch 3 weitere", wo keine sind.
  const offen = total + countRestored() - state.returned;
  $("trash-more").classList.toggle("hidden", offen <= 0);
  $("trash-more").textContent = loading
    ? "lädt …"
    : `Noch ${num(offen)} — beim Herunterscrollen von selbst`;
}

function tileHtml(p) {
  const raus = !!p.restored;
  return `
    <figure class="trash-item${picked.has(p.id) ? " on" : ""}${raus ? " restored" : ""}"
            data-id="${escapeHtml(p.id)}">
      <div class="trash-shot">
        <img src="${thumbUrl(p.id, 320)}" alt="" loading="lazy">
        ${raus ? `<span class="back-note" title="Nicht mehr zur Löschung vorgemerkt">gerettet</span>` : ""}
        <button type="button" class="trash-back" data-back="${escapeHtml(p.id)}"
                title="${raus ? "Wieder zur Löschung vormerken" : "Aus dem Papierkorb zurückholen"}">
          ${raus ? "↻ vormerken" : "↩ zurückholen"}
        </button>
      </div>
      <figcaption>
        <span class="when">${escapeHtml(p.date || "ohne Datum")}</span>
        ${p.person_names.length
          ? `<span class="who">${escapeHtml(p.person_names.join(", "))}</span>` : ""}
        <span class="what">${escapeHtml(p.caption_de || p.caption_display || "")}</span>
      </figcaption>
    </figure>`;
}

function paintGrid() {
  $("trash-grid").innerHTML = state.photos.map(tileHtml).join("");
  rendered = state.photos.length;
}

function paint() {
  paintMeta();
  paintGrid();
}

/* Zurückholen, ohne die Liste neu zu bauen.

   Ein `refresh()` holt die Seite neu und baut das Raster von vorn -- damit
   springt die Bildlaufleiste nach oben, und wer bei Foto 200 war, sucht
   wieder. Zurückgeholte Fotos verschwinden dabei ausserdem sofort, sodass
   man nicht mehr sieht, was man gerade gerettet hat.

   Also wird nur die betroffene Kachel angefasst: sie bleibt liegen, wird
   ausgegraut und sagt es. Der Knopf zeigt danach in die andere Richtung, ein
   Fehlgriff ist also ein Klick. Die Zähler oben werden aus den Feldern der
   Kachel nachgerechnet -- Grösse, Beschreibung, benannte Person stehen alle
   in der Liste. */
async function setTrashed(ids, trashed) {
  if (!ids.length) return;
  note(trashed ? "merkt vor …" : "holt zurück …");
  try {
    await api("/api/trash", {
      method: "POST", body: JSON.stringify({ photo_ids: ids, trashed }),
    });
  } catch (e) {
    note(`Fehlgeschlagen: ${e.message}`);
    return;
  }

  const wanted = new Set(ids);
  let n = 0;
  for (const p of state.photos) {
    if (!wanted.has(p.id) || !!p.restored === !trashed) continue;
    p.restored = !trashed;
    const schritt = trashed ? 1 : -1;
    state.total += schritt;
    state.bytes += schritt * (p.file_size || 0);
    if (p.caption_de) state.with_caption += schritt;
    if (p.person_names.length) state.with_person += schritt;
    // Zurückgeholtes kann nicht mehr gelöscht werden, also auch nicht gewählt.
    if (!trashed) picked.delete(p.id);
    const fig = $("trash-grid").querySelector(`[data-id="${CSS.escape(p.id)}"]`);
    if (fig) fig.outerHTML = tileHtml(p);
    n++;
  }
  paintMeta();
  // Kurz halten: die Zeile steht über dem Raster, und jede zusätzliche
  // Zeile schiebt es nach unten -- gemessen 39 px, genau dort wo man gerade
  // hingeklickt hat.
  note(trashed
    ? `${num(n)} wieder vorgemerkt.`
    : `${num(n)} zurückgeholt, bleibt ausgegraut stehen.`);
}

/* ---- Bedienung --------------------------------------------------------- */

export function bindTrash() {
  $("trash-grid").addEventListener("click", (e) => {
    // Der Knopf auf der Kachel zuerst: er liegt über dem Bild, und ein Klick
    // darauf soll nicht die Grossansicht öffnen. Vorher ging Zurückholen nur
    // aus der Detailansicht heraus -- man musste sich erst hineinklicken.
    const back = e.target.closest("[data-back]");
    if (back) {
      e.stopPropagation();
      const p = state.photos.find((x) => x.id === back.dataset.back);
      if (p) setTrashed([p.id], !!p.restored);
      return;
    }

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
    // Zurückgeholtes liegt nicht mehr im Papierkorb und kann deshalb auch
    // nicht zum Löschen gewählt werden.
    if (state.photos.find((p) => p.id === id)?.restored) return;
    if (picked.has(id)) picked.delete(id);
    else picked.add(id);
    paint();
  });

  $("trash-more").onclick = () => loadMore();

  /* Der Beobachter am unteren Rand. `rootMargin` laesst ihn schon vorher
     anspringen, damit das Nachladen fertig ist, bevor man das Ende sieht --
     sonst stockt es bei jedem Bildschirm. */
  const ende = $("trash-more");
  if (ende && "IntersectionObserver" in window) {
    new IntersectionObserver((eintraege) => {
      if (eintraege.some((x) => x.isIntersecting) && state && !ende.classList.contains("hidden")) {
        loadMore();
      }
    }, { rootMargin: "600px" }).observe(ende);
  }
  $("trash-all").onclick = () => {
    const drin = state.photos.filter((p) => !p.restored).map((p) => p.id);
    if (picked.size === drin.length) picked.clear();
    else picked = new Set(drin);
    paint();
  };

  $("trash-restore").onclick = () => setTrashed([...picked], false);

  $("trash-empty").onclick = async () => {
    // Zurückgeholtes gehört nicht dazu, auch nicht bei "alle".
    const ids = picked.size
      ? [...picked]
      : state.photos.filter((p) => !p.restored).map((p) => p.id);
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
