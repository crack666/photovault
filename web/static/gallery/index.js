/* Die Galerie: Zeitstrahl, Kanalleiste, Bilderstrom -- und der Serienstreifen.

   Dieselbe Ansicht bedient drei Aufrufer: die Fotos einer Person, die
   Gesichter einer unbenannten Gruppe und die Filterleiste, die sich selbst
   neu zeichnet. Deshalb liegt sie hier und nicht bei einem von ihnen.

   Die Grossansicht wird direkt importiert. Solange sie in app.js lag, ging
   das nicht -- app.js exportiert nichts -- und der Klick haette einen
   Rueckweg gebraucht. Seit lightbox/ ein eigenes Modul ist, gibt es dafuer
   keinen Grund mehr. */

import { escapeHtml } from "../core/dom.js?v=64";
import {
  CHANNEL_FILTERS, CHANNEL_LABEL, eventMeta, eventTitle, monthLabel,
} from "../core/format.js?v=64";
import { showLightbox } from "../lightbox/index.js?v=64";

function peopleLine(ev) {
  const names = ev.person_names || [];
  if (!names.length) return "";
  const shown = names.slice(0, 4).map(escapeHtml).join(", ");
  const more = names.length > 4 ? ` +${names.length - 4}` : "";
  return `<p class="ev-people">${shown}${more}</p>`;
}

//: Ansichtszustand der Galerie, kein Format -- deshalb hier und nicht in
//: core/format.js. Dort waere es eine globale Schaltvariable, die jedes
//: kuenftige Modul umlegen koennte.
let channelFilter = "";

// Der Balken zeigt nicht nur wie viel, sondern woraus: ein Jahr aus 400
// eigenen Aufnahmen sieht anders aus als eines aus 400 Weiterleitungen -- und
// man sieht auf einen Blick, wo sich das Hinsehen lohnt.
function stackBar(y) {
  const chans = y.channels || {};
  const order = ["camera", "whatsapp", "whatsapp-sent", "screenshot", "download", "document"];
  return order.filter((c) => chans[c]).map((c) =>
    `<b class="ch-${c}" style="flex:${chans[c]}"></b>`).join("");
}

function yearTooltip(y) {
  const chans = y.channels || {};
  const parts = Object.keys(chans).map((c) => `${chans[c]} ${CHANNEL_LABEL[c] || c}`);
  return escapeHtml(`${y.count} Fotos` + (parts.length ? ` — ${parts.join(", ")}` : ""));
}

function filterByChannel(data, chan) {
  if (!chan) return data;
  const years = (data.years || []).map((y) => {
    const events = y.events.filter((e) => e.channel === chan);
    const months = (y.months || [])
      .map((m) => ({ ...m, events: m.events.filter((e) => e.channel === chan) }))
      .filter((m) => m.events.length)
      .map((m) => ({ ...m, count: m.events.reduce((n, e) => n + e.photos.length, 0) }));
    return {
      ...y, events, months,
      count: events.reduce((n, e) => n + e.photos.length, 0),
      channels: { [chan]: events.reduce((n, e) => n + e.photos.length, 0) },
    };
  }).filter((y) => y.events.length);
  return { ...data, years, total: years.reduce((n, y) => n + y.count, 0) };
}

function renderChannelBar(host, data, redraw) {
  const counts = {};
  (data.years || []).forEach((y) =>
    Object.entries(y.channels || {}).forEach(([c, n]) => { counts[c] = (counts[c] || 0) + n; }));
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  host.innerHTML = CHANNEL_FILTERS
    .filter((f) => !f.key || counts[f.key])
    .map((f) => {
      const n = f.key ? counts[f.key] : total;
      return `<button type="button" class="chan${f.key === channelFilter ? " on" : ""}"
                data-chan="${f.key}">${escapeHtml(f.label)} <em>${n}</em></button>`;
    }).join("");
  host.querySelectorAll(".chan").forEach((b) => b.addEventListener("click", () => {
    channelFilter = b.dataset.chan;
    redraw();
  }));
}

/**
 * @param {object} [opt]
 * @param {Element} [opt.meta]  Element fuer die Zeile "N Fotos, gefiltert nach …"
 * @param {(foto, img) => boolean} [opt.onPick]  Darf den Klick auf ein Foto
 *   abfangen. Gibt es `true` zurueck, gilt der Klick als verbraucht und die
 *   Grossansicht bleibt zu -- so haengt die Fotoauswahl der Personenansicht
 *   daran, ohne dass die Galerie sie kennen muss. Ohne Rueckgabe oeffnet das
 *   Foto wie sonst. Frueher stand hier ein `selectable`-Schalter, und die
 *   Galerie las `selectMode` und `photoSel` aus app.js mit.
 */
export function fillGallery(timelineEl, streamEl, full, { meta = null, onPick = null } = {}) {
  /* `full` ist der ungefilterte Bestand und bleibt es.

     Vorher hiess der Parameter `data` und wurde eine Zeile spaeter mit dem
     gefilterten Ergebnis ueberschrieben. Der Rueckruf der Filterleiste
     schliesst aber ueber *dieselbe Variable*, nicht ueber ihren damaligen
     Wert -- beim naechsten Klick bekam er also die bereits gefilterten
     Daten. Damit filterte jeder Klick auf dem Ergebnis des vorigen weiter:
     erst "Alle" 51, dann "Eigene Aufnahmen" 17, und danach zeigte auch
     "Alle" nur noch diese 17, bis man die Seite neu lud. */
  const bar = streamEl.previousElementSibling?.classList.contains("chanbar")
    ? streamEl.previousElementSibling
    : Object.assign(document.createElement("div"), { className: "chanbar" });
  if (!bar.parentNode) streamEl.parentNode.insertBefore(bar, streamEl);
  // Die Zahlen in der Leiste kommen ebenfalls aus dem vollen Bestand --
  // sonst schrumpfen sie bei jedem Umschalten mit.
  renderChannelBar(bar, full, () =>
    fillGallery(timelineEl, streamEl, full, { meta, onPick }));

  const data = filterByChannel(full, channelFilter);
  // Die Zahl in der Ueberschrift muss zu dem passen, was darunter steht.
  // Sonst sieht ein aktiver Filter aus wie fehlende Fotos.
  if (meta) {
    const name = (CHANNEL_FILTERS.find((f) => f.key === channelFilter) || {}).label;
    const span = data.span ? ` · ${data.span.from.slice(0, 4)}–${data.span.to.slice(0, 4)}` : "";
    meta.textContent = channelFilter
      ? `${data.total} von ${full.total} Fotos — nur ${name}${span} · ${data.years.length} Jahre`
      : `${data.total} Foto${data.total === 1 ? "" : "s"}${span} · ${data.years.length} Jahre`;
  }
  const years = data.years || [];
  const peak = Math.max(1, ...years.map((y) => y.count));
  timelineEl.innerHTML = years.map((y) => `
    <button type="button" class="tl" data-year="${escapeHtml(y.year)}"
            title="${yearTooltip(y)}">
      <i style="height:${Math.max(12, Math.round((y.count / peak) * 40))}px">${stackBar(y)}</i>
      <span>${escapeHtml(y.year)}</span>
      <em>${y.count}</em>
    </button>`).join("");
  timelineEl.querySelectorAll(".tl").forEach((b) => {
    b.addEventListener("click", () => {
      streamEl.querySelector(`#year-${CSS.escape(b.dataset.year)}`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // Eigene Liste, nicht der Zustand der Grossansicht: die bekommt sie beim
  // Oeffnen uebergeben. Genau wie bindShotStrip und renderResults es tun.
  const photos = [];
  streamEl.innerHTML = years.map((y) => `
    <section class="year" id="year-${escapeHtml(y.year)}">
      <h2 class="year-head"><span>${escapeHtml(y.year)}</span><em>${y.count} Fotos</em></h2>
      ${(y.months && y.months.length ? y.months : [{ month: "", events: y.events }])
        .map((mo) => `
        ${mo.month ? `<h3 class="month-head">${escapeHtml(monthLabel(mo.month))}
            <em>${mo.count}</em></h3>` : ""}
        ${mo.events.map((ev) => `
        <div class="event" data-channel="${escapeHtml(ev.channel || "camera")}">
          <h3>${escapeHtml(eventTitle(ev))}<em>${ev.photos.length}</em></h3>
          <p class="ev-when">${escapeHtml(eventMeta(ev))}</p>
          ${peopleLine(ev)}
          <div class="shots">
            ${ev.photos.map((ph) => {
              const i = photos.push(ph) - 1;
              return `<img loading="lazy" data-i="${i}"
                        src="/api/photos/${encodeURIComponent(ph.id)}/thumb?size=320"
                        alt="" title="${escapeHtml(ph.caption_de || "")}" />`;
            }).join("")}
          </div>
        </div>`).join("")}`).join("")}
    </section>`).join("");

  streamEl.querySelectorAll(".shots img").forEach((im) => {
    im.addEventListener("click", () => {
      const i = Number(im.dataset.i);
      if (onPick && onPick(photos[i], im) === true) return;
      showLightbox(photos, i);
    });
  });

  const obs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const year = e.target.id.replace("year-", "");
      timelineEl.querySelectorAll(".tl").forEach((b) => {
        b.classList.toggle("on", b.dataset.year === year);
      });
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  streamEl.querySelectorAll(".year").forEach((s) => obs.observe(s));
}

const STRIP = 8;

export function bindShotStrip(el, ids, { excludable = false, excluded = null, onToggle = null } = {}) {
  const list = ids || [];
  const out = excluded || new Set();
  let offset = 0;
  function paint() {
    const total = list.length;
    const slice = list.slice(offset, offset + STRIP);
    const end = Math.min(offset + STRIP, total);
    el.innerHTML = `
      ${total > STRIP ? `<button type="button" class="strip-nav strip-prev" ${offset === 0 ? "disabled" : ""} aria-label="Zurück">‹</button>` : ""}
      <div class="strip-track">
        ${slice.map((id, i) => excludable
          ? `<figure class="shot${out.has(id) ? " out" : ""}" data-i="${offset + i}">
               <img loading="lazy" src="/api/photos/${encodeURIComponent(id)}/thumb?size=200" alt="" />
               <button type="button" class="drop" title="Gehört nicht in diese Serie">✕</button>
             </figure>`
          : `<img loading="lazy" data-i="${offset + i}"
              src="/api/photos/${encodeURIComponent(id)}/thumb?size=200" alt="" />`
        ).join("")}
      </div>
      ${total > STRIP ? `<button type="button" class="strip-nav strip-next" ${end >= total ? "disabled" : ""} aria-label="Weiter">›</button>
        <span class="strip-pos">${offset + 1}–${end} / ${total}</span>` : ""}`;
    el.querySelectorAll(".strip-track img").forEach((im) => {
      const i = Number((im.closest("[data-i]") || im).dataset.i);
      im.addEventListener("click", () => showLightbox(list, i));
    });
    el.querySelectorAll(".shot .drop").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const fig = btn.closest(".shot");
        const id = list[Number(fig.dataset.i)];
        if (out.has(id)) out.delete(id);
        else out.add(id);
        fig.classList.toggle("out", out.has(id));
        if (onToggle) onToggle(id, out.has(id), out);
      });
    });
    const prev = el.querySelector(".strip-prev");
    const next = el.querySelector(".strip-next");
    if (prev) prev.addEventListener("click", () => {
      offset = Math.max(0, offset - STRIP);
      paint();
    });
    if (next) next.addEventListener("click", () => {
      offset = Math.min(Math.max(0, total - STRIP), offset + STRIP);
      paint();
    });
  }
  paint();
  return out;
}
