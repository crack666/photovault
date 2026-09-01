/* Blaettern: "23-42 von 317" mit zwei Pfeilen -- und nichts, wenn alles passt.

   Liegt in core/, weil vier Ansichten dieselbe Leiste zeigen: die zwei
   Serien-Reiter, die Albumliste und die Suche. Der Schritt kommt vom
   Aufrufer, weil die Seiten verschieden gross sind (Serien 20, Suche 48);
   frueher stand hier die Serien-Zahl als Vorgabe, was fuer die Suche schlicht
   falsch war.

   `ids` nimmt eine oder mehrere Element-Kennungen: die meisten Ansichten
   zeigen die Leiste zweimal, oben und unten. */

import { $ } from "./dom.js?v=64";

export function renderPager(ids, offset, returned, total, onPage, schritt = 20) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + returned, total);
  const fits = total <= 0 || (offset <= 0 && end >= total);
  const html = fits
    ? ""
    : `<button type="button" class="mini pager-prev" ${offset <= 0 ? "disabled" : ""} aria-label="Vorherige Seite">‹</button>
       <span class="muted">${start}–${end} von ${total}</span>
       <button type="button" class="mini pager-next" ${end >= total ? "disabled" : ""} aria-label="Nächste Seite">›</button>`;
  (Array.isArray(ids) ? ids : [ids]).forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.innerHTML = html;
    el.classList.toggle("hidden", !html);
    const prev = el.querySelector(".pager-prev");
    const next = el.querySelector(".pager-next");
    if (prev) prev.addEventListener("click", () => {
      if (offset <= 0) return;
      onPage(Math.max(0, offset - schritt));
    });
    if (next) next.addEventListener("click", () => {
      if (end >= total) return;
      onPage(offset + (returned || schritt));
    });
  });
}
