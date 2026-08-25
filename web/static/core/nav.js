/* Die Kopfzeile — einmal beschrieben, auf beiden Seiten dieselbe.

   Vorher stand sie zweimal im HTML, und auf der Jobs-Seite war sie auf zwei
   Einträge zusammengeschrumpft: von dort kam man nur zu „Wer ist das?"
   zurück. Wer auf der Jobs-Seite steht, will aber genauso zu den Serien.

   Die Tabs von `index.html` schalten innerhalb der Seite um; von einer
   anderen Seite aus wird daraus ein Sprung mit `?tab=`. Deshalb kennt diese
   Datei beide Fälle und `index.html` liest den Parameter beim Laden. */

export const TABS = [
  { id: "label", label: "Wer ist das?" },
  { id: "unknown", label: "Unbekannte" },
  { id: "people", label: "Personen" },
  { id: "events", label: "Serien" },
  { id: "search", label: "Suche" },
  { id: "atlas", label: "Atlas" },
];

export const JOBS_PAGE = "/jobs.html";

/**
 * @param {Element} host   das <nav>
 * @param {object} o
 * @param {string} [o.active]   aktive Tab-Kennung, oder "jobs"
 * @param {boolean} [o.inPlace] true auf index.html: Tabs schalten hier um
 */
export function renderNav(host, { active = "label", inPlace = false } = {}) {
  host.innerHTML = TABS.map((t) => {
    const on = inPlace && t.id === active ? " on" : "";
    return inPlace
      ? `<button class="tab${on}" data-tab="${t.id}">${t.label}</button>`
      : `<a class="tab" href="/?tab=${t.id}">${t.label}</a>`;
  }).join("") + (active === "jobs"
    ? `<span class="tab on">Jobs</span>`
    : `<a class="tab" href="${JOBS_PAGE}">Jobs</a>`);
}

/** Welcher Tab ist gemeint? `?tab=` gewinnt, sonst der erste. */
export function tabFromUrl() {
  const want = new URLSearchParams(location.search).get("tab");
  return TABS.some((t) => t.id === want) ? want : TABS[0].id;
}

/** Die Adresse mitführen, damit ein Tab teilbar und der Zurück-Knopf sinnvoll ist. */
export function rememberTab(id) {
  const url = new URL(location.href);
  if (id === TABS[0].id) url.searchParams.delete("tab");
  else url.searchParams.set("tab", id);
  history.replaceState(null, "", url);
}
