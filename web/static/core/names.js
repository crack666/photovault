/* ---- Vorschlagslisten --------------------------------------------------
   Ein Vertipper legt sonst still eine zweite Identitaet an: "Annika Wolf"
   und "Annika Glass" waeren zwei Personen, und niemand bemerkt es, bis die
   Fotos auf zwei Karten verteilt sind. Dasselbe bei Serien.

   Bewusst <datalist> und kein erzwungenes Auswaehlen: neue Namen muessen
   moeglich bleiben, das ist ja der Normalfall beim Benennen.

   Blatt ohne Modulzustand: kein Zwischenspeicher, keine Aufrufe beim Laden.
   Der Startabruf steht im Router, nicht hier -- stünde er in diesem Modul,
   liefen zwei API-Aufrufe bei jedem Import mit, und zwar noch bevor
   feststeht, welcher Tab überhaupt geöffnet wird. */

import { api } from "./api.js?v=58";
import { $, escapeHtml } from "./dom.js?v=58";

function fillDatalist(id, values) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = [...new Set(values.filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, "de")).map((v) => `<option value="${escapeHtml(v)}"></option>`).join("");
}

export async function refreshPersonNames() {
  try {
    const d = await api("/api/persons");
    const list = d.persons || d;
    fillDatalist("dl-persons", (Array.isArray(list) ? list : [])
      .map((p) => p.name)
      // Ablagen fuer Aussortiertes sind keine Namensvorschlaege.
      .filter((n) => n && n !== "Übersprungen" && n !== "Ignoriert"));
  } catch (err) { /* Vorschlaege sind Komfort, kein Muss */ }
}

export async function refreshEventNames() {
  try {
    const d = await api("/api/events/named?limit=500");
    fillDatalist("dl-events", (d.events || []).map((e) => e.name));
  } catch (err) { /* siehe oben */ }
}
