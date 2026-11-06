/* Tasten fuer "Wer ist das?" -- ohne DOM, damit es sich ohne Browser pruefen laesst.

   Benennen ist die meiste Handarbeit im Archiv (photovault#60): bei rund
   28.000 Gesichtern stand jede Karte bisher nur ueber die Maus offen. Die
   Belegung trifft genau die Massenarbeit -- Vorschlag annehmen, ablehnen,
   weiter -- und nichts darueber hinaus.

   Hier steht nur die Zuordnung Taste -> Aktion. Ob eine Aktion an der
   aktuellen Karte moeglich ist (gibt es einen Vorschlag?), entscheidet der
   Aufrufer in app.js; er kennt die Karte. */

/** Die Belegung, wie sie die Hilfezeile zeigt -- eine Quelle fuer beide. */
export const LABEL_KEYS = [
  ["J / Enter", "ersten Vorschlag annehmen (bei „Noch mehr …?“: Ja)"],
  ["1–9", "Vorschlag Nummer n annehmen"],
  ["N", "Nein, anderer Name: ins Namensfeld"],
  ["S", "Überspringen"],
  ["→ / ←", "nächste / vorige Gruppe, ohne zu entscheiden"],
  ["Esc", "aus dem Namensfeld heraus, damit die Tasten wieder greifen"],
  ["?", "diese Belegung zeigen oder verbergen"],
];

/**
 * Welche Aktion eine Taste auf der Karte ausloest.
 *
 * @param {{key: string, ctrlKey?: boolean, metaKey?: boolean, altKey?: boolean}} e
 * @param {{typing?: boolean, inNameField?: boolean, onButton?: boolean}} [ctx]
 *   typing: Fokus liegt in einem Eingabefeld (core/dom.js `isTyping`).
 *   inNameField: dieses Feld ist das Namensfeld der Karte.
 *   onButton: Fokus liegt auf einem Knopf oder Link.
 * @returns {null | "confirm" | "reject" | "skip" | "next" | "prev" | "help" | "leave-field" | {pick: number}}
 */
export function labelKeyAction(e, ctx = {}) {
  if (!e || e.ctrlKey || e.metaKey || e.altKey) return null;
  const key = e.key;
  if (ctx.typing) {
    // Beim Tippen gehoert jede Taste dem Feld -- "J" ist der Anfang von
    // "Jana". Nur Escape fuehrt aus dem Namensfeld heraus.
    return ctx.inNameField && key === "Escape" ? "leave-field" : null;
  }
  // Enter auf einem fokussierten Knopf drueckt diesen Knopf; ein zweites
  // "confirm" daneben ordnete die Gruppe womoeglich einer anderen Person zu.
  if (key === "Enter") return ctx.onButton ? null : "confirm";
  if (key === "j" || key === "J") return "confirm";
  if (key === "n" || key === "N") return "reject";
  if (key === "s" || key === "S") return "skip";
  if (key === "ArrowRight") return "next";
  if (key === "ArrowLeft") return "prev";
  if (key === "?") return "help";
  if (/^[1-9]$/.test(key)) return { pick: Number(key) - 1 };
  return null;
}
