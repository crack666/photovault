/* Kleinkram, den jede Ansicht braucht.
   Eigene Datei, damit ein neuer Tab nicht wieder in app.js landet. */

export const $ = (id) => document.getElementById(id);

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** Zahl mit deutschem Tausenderpunkt -- 17370 liest sich sonst wie eine Kennung. */
export function num(n) {
  return Number(n).toLocaleString("de-DE");
}

/** Aus einer Liste die ersten n nennen, den Rest zaehlen. */
export function summarize(items, n = 3) {
  const list = items.filter(Boolean);
  if (list.length <= n) return list.join(", ");
  return `${list.slice(0, n).join(", ")} +${list.length - n}`;
}
