/* Die Grossansicht -- ein Foto gross, mit allem, was der Index darueber weiss.

   Sie hing bisher in app.js und war von dort aus die einzige Funktion, die
   aus fuenf Richtungen gebraucht wurde: Galerie, Serienstreifen, Suche,
   Karte und Papierkorb. Die letzten beiden bekamen sie per deps-Objekt
   hineingereicht -- ein Umweg, den es nur gab, weil sie in app.js lag und
   app.js nichts exportiert. Jetzt importiert sie jeder direkt.

   Modulzustand ist die gerade gezeigte Liste (`lbPhotos`) und die Stelle
   darin (`lbIndex`). Die Liste gehoert weiterhin dem Aufrufer: `asLbPhotos`
   laesst uebergebene Objekte als Referenz durch, damit eine hier gespeicherte
   Beschreibung auch in der Kachel des Aufrufers ankommt. Nicht kopieren. */

import { api } from "../core/api.js?v=55";
import { $, escapeHtml, isTyping } from "../core/dom.js?v=55";
import { bindFaceStrip, mountFaceStrip } from "../faces/strip.js?v=55";

//: Die gerade gezeigte Liste und die Stelle darin. Geschrieben nur von
//: showLightbox, gelesen von openLightbox und den Tasten.
let lbPhotos = [], lbIndex = 0;

const DATE_SOURCE_LABEL = {
  exif: "aus den Bilddaten", filename: "aus dem Dateinamen",
  folder_name: "aus dem Albumnamen", folder: "aus dem Album",
  folder_json: "aus der Album-Datei", file_time: "geschätzt aus der Dateizeit",
  accepted: "von dir gesetzt", offset: "Uhr korrigiert",
};
const DATE_ESTIMATED = new Set(["filename", "folder", "folder_name", "folder_json", "file_time", "album"]);

function lbInfoOpen() {
  return localStorage.getItem("pv-lb-info") !== "0";
}

function setLbInfoOpen(on) {
  localStorage.setItem("pv-lb-info", on ? "1" : "0");
  $("lightbox").classList.toggle("info-off", !on);
  $("lb-reveal").classList.toggle("hidden", on);
}

function asLbPhotos(photos) {
  return (photos || []).map((p) => (typeof p === "string" ? { id: p } : p));
}

export function showLightbox(photos, index) {
  const list = asLbPhotos(photos);
  if (!list.length) return;
  lbPhotos = list;
  openLightbox(index);
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

function gpsPair(gps) {
  if (!Array.isArray(gps) || gps.length < 2) return null;
  const lat = Number(gps[0]), lon = Number(gps[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon };
}

const FILE_WARN = {
  truncated: "Datei fehlerhaft — JPEG unvollständig (Transfer abgebrochen?)",
  unreadable: "Datei fehlerhaft — Bild nicht lesbar",
  missing: "Datei fehlt auf der Platte",
};

function fillFileWarn(code) {
  const el = $("lb-file-warn");
  if (!el) return;
  const text = FILE_WARN[code] || "";
  el.textContent = text;
  el.classList.toggle("hidden", !text);
}

function fillMap(gps) {
  const box = $("lb-map");
  if (!box) return;
  const pair = gpsPair(gps);
  const frame = $("lb-map-frame");
  if (!pair) {
    box.classList.add("hidden");
    if (frame) frame.src = "about:blank";
    return;
  }
  const { lat, lon } = pair;
  // OSM-Embed hat kein zoom=; der Ausschnitt ist die Zoomstufe.
  // ~800 m Breite ≈ Straße / Häuserblock, nicht der ganze Bezirk.
  const spanM = 400;
  const dLat = spanM / 111320;
  const dLon = spanM / (111320 * Math.cos((lat * Math.PI) / 180) || 1);
  const bbox = [lon - dLon, lat - dLat, lon + dLon, lat + dLat].join(",");
  box.classList.remove("hidden");
  if (frame) {
    frame.src = `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${lat},${lon}`;
  }
  const link = $("lb-map-link");
  if (link) link.href = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`;
  $("lb-map-coord").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

async function loadPhotoInfo(id, opts = {}) {
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
    ["Serie", d.event_name],
    ["Ereignis", d.caption_display],
    ["Personen", (d.person_names || []).join(", ")],
    ["Vermutlich", (d.person_suggestions || []).join(", ")],
    ["Gesichter erkannt", d.face_count],
    ["Notizen", (d.annotations || []).join(", ")],
    ["Szene", (d.scene_tags || []).join(", ")],
    ["Ort", d.location],
    ["GPS", (() => {
      const g = gpsPair(d.gps);
      return g ? `${g.lat.toFixed(5)}, ${g.lon.toFixed(5)}` : null;
    })()],
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
  /* "Behalten" und "Speichern" taten dasselbe: beide schickten den Feldinhalt
     mit lock: true. Der Nutzer suchte einen Unterschied, den es nicht gab.

     Jetzt hat der zweite Knopf eine eigene Aufgabe -- die Beschreibung des
     Modells uebernehmen, ohne sie anzufassen -- und ist nur da, wenn es die
     ueberhaupt zu uebernehmen gibt: eine da, noch nicht gesperrt, nichts
     getippt. */
  $("lb-caption").dataset.stored = d.caption_de || "";
  $("lb-cap-state").textContent = d.caption_locked
    ? "von Hand — bleibt bei neuen Läufen erhalten"
    : (d.caption_de ? "vom Modell erzeugt" : "noch keine Beschreibung");
  updateKeepButton(Boolean(d.caption_locked));
  fillMap(d.gps);
  fillFileWarn(d.file_warning);
  const accept = $("lb-date-accept");
  if (accept) {
    accept.classList.remove("hidden");
    const estimated = d.date && DATE_ESTIMATED.has(d.date_source);
    $("lb-accept-date").classList.toggle("hidden", !estimated);
    $("lb-date-hint").textContent = estimated
      ? "Aufnahmedatum ist geschätzt — oft trotzdem passend, oder hier korrigieren."
      : "Wenn Ordner oder Nachbarn ein anderes Datum nahelegen — hier setzen.";
    $("lb-date-input").value = d.date || "";
    $("lb-date-state").textContent = "";
  }
  $("lb-info").dataset.dateSource = d.date_source || "";

  // Der Knopf gehoert zu *diesem* Foto -- sonst steht beim naechsten noch
  // "Zurückholen" da, und ein Klick darauf holt etwas zurück, das nie weg war.
  const trash = $("lb-trash");
  if (trash) {
    trash.dataset.back = d.trashed_at ? "1" : "";
    trash.textContent = d.trashed_at ? "Zurückholen" : "In den Papierkorb";
    trash.classList.toggle("danger", !d.trashed_at);
    $("lb-trash-state").textContent = d.trashed_at ? "liegt im Papierkorb." : "";
  }

  // Der Gesichtsstreifen laedt sich nach einer Zuordnung selbst neu und
  // ruft dann hier zurueck -- ohne `skipFaces` wuerden sich die beiden
  // gegenseitig immer wieder neu laden.
  if (!opts.skipFaces) {
    mountFaceStrip($("lb-faces"), id, {
      onChange: () => loadPhotoInfo(id, { skipFaces: true }),
    });
  }
}

function updateKeepButton(locked) {
  const feld = $("lb-caption");
  const gespeichert = feld.dataset.stored || "";
  const unveraendert = feld.value === gespeichert;
  $("lb-keep-caption").classList.toggle(
    "hidden", locked || !gespeichert.trim() || !unveraendert);
}

async function trashFromLightbox(id, back) {
  const state = $("lb-trash-state");
  const btn = $("lb-trash");
  state.textContent = back ? "hole zurück …" : "merkt vor …";
  btn.disabled = true;
  try {
    await api("/api/trash", {
      method: "POST",
      body: JSON.stringify({ photo_ids: [id], trashed: !back }),
    });
  } catch (e) {
    state.textContent = `Fehlgeschlagen: ${String(e.message || e).slice(0, 120)}`;
    btn.disabled = false;
    return;
  }
  btn.disabled = false;
  btn.dataset.back = back ? "" : "1";
  btn.textContent = back ? "In den Papierkorb" : "Zurückholen";
  btn.classList.toggle("danger", back);
  state.textContent = back
    ? "wieder da."
    : "Im Papierkorb. Aus Suche, Serien und Alben sofort weg — von der Karte "
      + "erst beim nächsten Rechnen.";
}

function closeLightbox() {
  $("lightbox").classList.add("hidden");
  fillMap(null);
  fillFileWarn(null);
}

function stepLightbox(d) { if (!$("lightbox").classList.contains("hidden")) openLightbox(lbIndex + d); }

/* Die Listener einmal binden -- nach dem Vorbild von trash/index.js.

   Der Riegel ist hier nicht optional: initTrash wird von app.js hinter einem
   eigenen trashBound aufgerufen, diese Funktion hat keinen solchen Aufrufer.
   Ohne ihn haengt ein zweiter Aufruf jeden Handler ein zweites Mal ein, und
   ein Klick auf "Speichern" schickte zwei Anfragen.

   Zwei Bindungen gehen an document (Tasten, Hintergrundklick) statt an ein
   Element der Grossansicht -- sie pruefen selbst, ob sie gemeint sind. */
let bound = false;

export function bindLightbox() {
  if (bound) return;
  bound = true;

  bindFaceStrip($("lb-faces"));
  $("lb-toggle").addEventListener("click", () => setLbInfoOpen(false));
  $("lb-reveal").addEventListener("click", () => setLbInfoOpen(true));
  $("lb-caption").addEventListener("input", () => updateKeepButton(false));
  $("lb-save-caption").addEventListener("click", async () => {
    const id = $("lb-info").dataset.photoId;
    if (!id) return;
    $("lb-cap-state").textContent = "speichert …";
    try {
      const res = await api(`/api/photos/${encodeURIComponent(id)}/caption`, {
        method: "POST",
        body: JSON.stringify({ caption_de: $("lb-caption").value, lock: true }),
      });
      const ph = lbPhotos[lbIndex];
      if (ph) ph.caption_de = res.caption_de;
      await loadPhotoInfo(id);
      $("lb-cap-state").textContent = res.locked
        ? "gespeichert — bleibt bei neuen Läufen erhalten"
        : "gespeichert";
    } catch (err) {
      $("lb-cap-state").textContent = `Fehler: ${String(err.message || err).slice(0, 120)}`;
    }
  });
  $("lb-accept-date").addEventListener("click", async () => {
    const id = $("lb-info").dataset.photoId;
    if (!id) return;
    $("lb-date-state").textContent = "schreibt …";
    try {
      const res = await api(`/api/photos/${encodeURIComponent(id)}/accept-date`, { method: "POST" });
      await loadPhotoInfo(id);
      $("lb-date-state").textContent = res.written
        ? "übernommen — steht jetzt in den Bilddaten"
        : "übernommen im Index" + (res.exif_reason ? ` (Datei: ${res.exif_reason})` : "");
    } catch (err) {
      $("lb-date-state").textContent = `Fehler: ${String(err.message || err).slice(0, 120)}`;
    }
  });
  $("lb-set-date").addEventListener("click", async () => {
    const id = $("lb-info").dataset.photoId;
    const day = $("lb-date-input").value;
    if (!id || !day) return;
    $("lb-date-state").textContent = "schreibt …";
    try {
      const res = await api(`/api/photos/${encodeURIComponent(id)}/date`, {
        method: "POST",
        body: JSON.stringify({ date: day }),
      });
      await loadPhotoInfo(id);
      $("lb-date-state").textContent = res.written
        ? "gesetzt — steht in Index und Datei"
        : "gesetzt im Index" + (res.exif_reason ? ` (Datei: ${res.exif_reason})` : "");
    } catch (err) {
      $("lb-date-state").textContent = `Fehler: ${String(err.message || err).slice(0, 120)}`;
    }
  });
  $("lb-trash").addEventListener("click", () => {
    const id = $("lb-info").dataset.photoId;
    if (!id) return;
    trashFromLightbox(id, $("lb-trash").dataset.back === "1");
  });
  $("lb-keep-caption").addEventListener("click", async () => {
    const id = $("lb-info").dataset.photoId;
    // Ausdruecklich der gespeicherte Text, nicht der im Feld: uebernommen wird,
    // was das Modell geschrieben hat.
    const text = $("lb-caption").dataset.stored || "";
    if (!id || !text.trim()) return;
    $("lb-cap-state").textContent = "übernimmt …";
    try {
      await api(`/api/photos/${encodeURIComponent(id)}/caption`, {
        method: "POST",
        body: JSON.stringify({ caption_de: text, lock: true }),
      });
      await loadPhotoInfo(id);
    } catch (err) {
      $("lb-cap-state").textContent = `Fehler: ${String(err.message || err).slice(0, 120)}`;
    }
  });
  document.querySelector(".lb-close").addEventListener("click", closeLightbox);
  document.querySelector(".lb-prev").addEventListener("click", () => stepLightbox(-1));
  document.querySelector(".lb-next").addEventListener("click", () => stepLightbox(1));
  $("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeLightbox(); });
  document.addEventListener("keydown", (e) => {
    if ($("lightbox").classList.contains("hidden")) return;
    // Beim Tippen kein Blättern und kein Umschalten -- geprueft wurde vorher
    // nur TEXTAREA, und das Namensfeld ist ein INPUT.
    if (isTyping(e)) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft") stepLightbox(-1);
    if (e.key === "ArrowRight") stepLightbox(1);
    if (e.key === "i") setLbInfoOpen(!lbInfoOpen());
  });
}
