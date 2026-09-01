/* Textbausteine — dieselben Wörter in Galerie und Serienliste.

   Reine Funktionen über ihre Argumente: kein Element, kein `fetch`, kein
   Zustand. Genau deshalb liegen sie hier und nicht bei einer der beiden
   Ansichten — wer sie einer zuschlägt, macht die andere von ihr abhängig.

   Hier wird NICHT escaped. Die Aufrufer setzen die Rückgabe in
   Template-Literale und escapen dort; täte es diese Datei noch einmal,
   stünden doppelte Entities im Bild. Prüfstein für die Datei: kein `import`,
   kein `$(`, kein `document.`. */

//: Modulprivat. Nach dem Schnitt hat app.js keinen Leser mehr — ein Export
//: wäre ein toter Anschluss, der eine Kopplung behauptet, die es nicht gibt.
const MONTHS = ["Januar","Februar","März","April","Mai","Juni",
                "Juli","August","September","Oktober","November","Dezember"];

export const CHANNEL_LABEL = {
  camera: "eigene Aufnahmen",
  whatsapp: "empfangen",
  "whatsapp-sent": "verschickt",
  screenshot: "Screenshot",
  download: "heruntergeladen",
  document: "Dokument",
};

// Kanalfilter. Vorgabe "camera": das ist die Bibliothek im engeren Sinn.
// Empfangenes bleibt erreichbar, draengt sich aber nicht auf. Als
// Gliederungsebene taugt der Kanal nicht -- niemand sucht "alle Screenshots
// aus 2019" -- als Filter beantwortet er die Frage, die man wirklich hat.
/* Herkunftsfilter fuer eine Galerie.

   "Alle" steht vorn und ist die Voreinstellung. Vorher stand dort "Eigene
   Aufnahmen": wer auf eine Person klickte, sah nur ihre Kamerafotos, waehrend
   die Ueberschrift die volle Zahl nannte. Bei einem Archiv, das zu neun
   Zehnteln aus Handy-Ordnern besteht, fehlte damit fast alles -- und nichts
   sagte, dass gefiltert wird. Wer eine Person anklickt, will erst einmal
   alles von ihr sehen; einschraenken kann man danach. */
export const CHANNEL_FILTERS = [
  { key: "", label: "Alle" },
  { key: "camera", label: "Eigene Aufnahmen" },
  { key: "whatsapp", label: "Empfangen" },
  { key: "whatsapp-sent", label: "Verschickt" },
];

export function monthLabel(ym) {
  const [y, m] = ym.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

export function eventTitle(ev) {
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
/**
 * Es gab das zweimal: `eventWhen` für die Galerie und `evWhen` für die
 * Serienliste. Für jeden Wert, den das Backend liefern kann, gaben beide
 * dasselbe aus — sie unterschieden sich einzig darin, was ohne Uhrzeit
 * herauskommt.
 *
 * Der Standard ist deshalb der leere Text, und er muss es bleiben: die
 * Galerie ruft diese Funktion nur über `eventMeta`, und das schiebt jede
 * nichtleere Rückgabe in die Metazeile. Stünde hier "Uhrzeit unbekannt",
 * erschiene der Serientext in jeder Galeriezeile ohne Uhrzeit — ohne Fehler,
 * ohne roten Test, sichtbar nur im Bild. Wer den Text braucht, sagt es an
 * der Aufrufstelle.
 */
export function eventWhen(ev, { unknown = "" } = {}) {
  if (ev.day_level || !ev.start) return unknown;
  const t = (iso) => iso.slice(11, 16);
  const span = ev.span_minutes;
  const range = span > 0 ? `${t(ev.start)}–${t(ev.end)}` : t(ev.start);
  if (!span) return range;
  const dur = span >= 90 ? `${(span / 60).toFixed(1).replace(".", ",")} h` : `${span} min`;
  return `${range} · ${dur}`;
}

export function eventMeta(ev) {
  const bits = [];
  const when = eventWhen(ev);
  if (when) bits.push(when);
  bits.push(`${ev.photos.length} Foto${ev.photos.length === 1 ? "" : "s"}`);
  if (ev.channel && ev.channel !== "camera") {
    bits.push(CHANNEL_LABEL[ev.channel] || ev.channel);
  }
  return bits.join(" · ");
}

export function evDate(iso) {
  if (!iso) return "ohne Datum";
  const [y, m, d] = iso.split("-");
  return `${Number(d)}. ${MONTHS[Number(m) - 1]} ${y}`;
}

export function faceStatsLine(s) {
  if (!s) return "";
  const named = s.faces_named ?? s.faces_labeled ?? 0;
  const parts = [`${named} von ${s.faces_total} mit Namen`];
  if (s.faces_skipped) parts.push(`${s.faces_skipped} übersprungen`);
  if (s.faces_ignored) parts.push(`${s.faces_ignored} ignoriert`);
  if (s.faces_small) parts.push(`${s.faces_small} in Gruppen unter 10`);
  else if (s.faces_unlabeled && !(s.faces_in_queue > 0)) {
    parts.push(`${s.faces_unlabeled} unbenannt`);
  }
  return ` · ${parts.join(" · ")}`;
}
