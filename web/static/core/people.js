/* Die Liste der benannten Personen — einmal geholt, von mehreren gelesen.

   Drei Ansichten brauchen sie: der Personenreiter zeigt sie, der Suchtab
   baut daraus seine Auswahl, und die Unbekannten schlagen darin nach. Weil
   sie in app.js lag, hiess "die Liste ist veraltet" bisher `peopleCache = []`
   an vier verstreuten Stellen — und wer eine davon vergass, sah bis zum
   Neuladen alte Namen.

   Deshalb hier, mit genau einem Besitzer und drei Wegen: holen, ersetzen,
   vergessen. Kein Modul greift mehr in die Liste hinein.

   Bewusst kein Zeitablauf: die Liste veraltet nicht von selbst, sondern
   genau dann, wenn jemand einen Namen ändert. Das weiss der Aufrufer, nicht
   eine Uhr. */

import { api } from "./api.js?v=66";

let cache = [];

/** Die Liste, wie sie ist — ohne zu holen. Für Render-Funktionen. */
export function peopleList() {
  return cache;
}

/**
 * Die Liste, notfalls geholt. Sortiert nach Gesichtszahl, weil jede Auswahl
 * die häufigen Personen oben haben will.
 *
 * Scheitert der Abruf, bleibt die Liste leer statt zu werfen: eine
 * Namensauswahl ohne Vorschläge ist unbequem, ein Abbruch der ganzen Ansicht
 * wäre schlimmer.
 */
export async function loadPeopleList() {
  if (!cache.length) {
    try { cache = await api("/api/persons"); } catch { cache = []; }
  }
  cache.sort((a, b) => (b.face_count || 0) - (a.face_count || 0));
  return cache;
}

/** Frisch geholte Liste übernehmen — der Personenreiter holt sie ohnehin. */
export function setPeopleList(list) {
  cache = Array.isArray(list) ? list : [];
}

/** Nach einer Namensänderung: beim nächsten Zugriff neu holen. */
export function forgetPeopleList() {
  cache = [];
}
