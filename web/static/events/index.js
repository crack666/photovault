/* Unbenannte Serien, benannte Serien, Vorschlaege, ganze Ordner.

   Vier Reiter derselben Frage: welche Aufnahmen gehoeren zusammen, und wie
   heisst das? Der Block ist geschlossen -- saemtlicher Zustand (welcher
   Reiter, welcher Kanal, welche Seite, ab welcher Groesse) wird nur hier
   gelesen und geschrieben. Nach aussen braucht er Textbausteine, den
   Serienstreifen der Galerie und die Blaetterleiste; nichts davon zeigt
   zurueck.

   Der Startfehler von frueher loest sich damit von selbst: `evTab` und seine
   Nachbarn wurden mit `let` weit unten in app.js deklariert, und der Router
   griff oben darauf zu -- temporale Totzone, die Auswertung brach ab. Als
   eigenes Modul ist der Zustand vor dem ersten Aufruf da. */

import { api } from "../core/api.js?v=58";
import { $, escapeHtml } from "../core/dom.js?v=58";
import { CHANNEL_FILTERS, evDate, eventWhen } from "../core/format.js?v=58";
import { askConfirm, notify } from "../core/modal.js?v=58";
import { refreshEventNames } from "../core/names.js?v=58";
import { renderPager } from "../core/pager.js?v=58";
import { bindShotStrip } from "../gallery/index.js?v=58";

/* ---- Unbenannte Serien -------------------------------------------------
   Gegenstück zu "Wer ist das?": das System bildet Gruppen, der Mensch
   erkennt sie. Absteigend nach Größe, weil dort der Ertrag je Entscheidung
   am höchsten ist — eine Serie mit 150 Fotos ordnet mehr als dreißig
   Zweiergrüppchen. */

//: Was in der Serienliste steht, wenn eine Serie keine Uhrzeit hat. In der
//: Galerie steht dort nichts -- deshalb der Text hier und nicht als Standard
//: in eventWhen.
const WHEN_UNKNOWN = { unknown: "Uhrzeit unbekannt" };

let evChannel = "camera";
let evTab = "unnamed";
const EV_PAGE = 20;
const SIZE_FILTERS = [
  { min: 20, label: "ab 20" },
  { min: 10, label: "ab 10" },
  { min: 5, label: "ab 5" },
  { min: 2, label: "ab 2" },
];
let evMinSize = 5;
let evOffset = 0;
let sgOffset = 0;
let sgMeta = { offset: 0, returned: 0, total: 0 };

export function loadEventTab() {
  ["unnamed", "named", "suggest", "albums"].forEach((id) => {
    $(`ev-${id}`).classList.toggle("hidden", evTab !== id);
  });
  $("ev-sub").querySelectorAll(".chan").forEach((b) => {
    b.classList.toggle("on", b.dataset.evtab === evTab);
  });
  if (evTab === "unnamed") loadEvents();
  else if (evTab === "named") loadNamed();
  else if (evTab === "suggest") loadSuggestions();
  else loadAlbums();
}

/* Die eine Bindung des Blocks -- einmal, wie in lightbox/ und trash/.

   Sie haengt an der Unterleiste der Reiter, nicht an deren Inhalt: die
   Leiste steht fest im HTML, der Inhalt wird bei jedem Wechsel neu
   gezeichnet. Ohne Riegel haengt ein zweiter Aufruf jeden Reiter ein
   zweites Mal ein. */
let bound = false;

export function bindEvents() {
  if (bound) return;
  bound = true;
  $("ev-sub").querySelectorAll(".chan").forEach((b) => {
    b.addEventListener("click", () => {
      evTab = b.dataset.evtab;
      evOffset = 0;
      sgOffset = 0;
      loadEventTab();
    });
  });
}

function vorschlaegeLabel(n) {
  const k = Number(n) || 0;
  return k === 1 ? "1 Vorschlag" : `${k} Vorschläge`;
}

function bindSizeFilters(hostId, current, onPick) {
  const host = $(hostId);
  if (!host) return;
  host.innerHTML = SIZE_FILTERS.map((f) =>
    `<button type="button" class="chan${f.min === current ? " on" : ""}" data-min="${f.min}">${f.label} Fotos</button>`
  ).join("");
  host.querySelectorAll(".chan").forEach((b) =>
    b.addEventListener("click", () => onPick(Number(b.dataset.min))));
}

async function loadEvents() {
  const host = $("ev-list");
  host.innerHTML = "<p class='muted'>Lade …</p>";
  $("ev-chan").innerHTML = CHANNEL_FILTERS
    .map((f) => `<button type="button" class="chan${f.key === evChannel ? " on" : ""}"
                   data-chan="${f.key}">${escapeHtml(f.label)}</button>`).join("");
  $("ev-chan").querySelectorAll(".chan").forEach((b) =>
    b.addEventListener("click", () => { evChannel = b.dataset.chan; evOffset = 0; loadEvents(); }));
  bindSizeFilters("ev-size", evMinSize, (min) => {
    evMinSize = min;
    evOffset = 0;
    loadEvents();
  });

  let d;
  try {
    d = await api(
      `/api/events/unnamed?limit=${EV_PAGE}&offset=${evOffset}` +
      `&min_size=${evMinSize}&channel=${encodeURIComponent(evChannel)}`
    );
  } catch (err) {
    host.innerHTML = `<p class='muted'>Serien konnten nicht geladen werden (${escapeHtml(String(err.message || err).slice(0, 140))})</p>`;
    return;
  }
  if (!d.events.length && evOffset > 0 && d.unnamed > 0) {
    evOffset = Math.max(0, Math.floor((d.unnamed - 1) / EV_PAGE) * EV_PAGE);
    return loadEvents();
  }
  const from = d.unnamed ? (d.offset || 0) + 1 : 0;
  const to = (d.offset || 0) + (d.returned || d.events.length);
  $("ev-meta").textContent =
    `${d.unnamed} Serie${d.unnamed === 1 ? "" : "n"} ohne Namen · ${d.photos_unnamed} Fotos` +
    (d.unnamed > (d.returned || 0) ? ` · ${from}–${to}` : "") +
    (d.unnamed_small
      ? ` · ${d.unnamed_small} mit unter ${d.min_size} Fotos ausgeblendet`
      : "");
  renderPager(["ev-pager", "ev-pager-2"], d.offset || 0, d.returned || d.events.length, d.unnamed, (off) => {
    evOffset = off;
    loadEvents();
  });

  if (!d.events.length) {
    host.innerHTML = d.unnamed_small
      ? `<p class='muted'>Keine Serie ab ${d.min_size} Fotos. ${d.unnamed_small} kleinere über „ab 2“.</p>`
      : "<p class='muted'>Alles benannt.</p>";
    return;
  }
  host.innerHTML = d.events.map((ev, i) => `
    <div class="serie" data-i="${i}">
      <div class="serie-head">
        <div>
          <strong>${escapeHtml(ev.folders.join(" · ") || "Ohne Album")}</strong>
          <span class="muted">${escapeHtml(evDate(ev.date))} · ${escapeHtml(eventWhen(ev, WHEN_UNKNOWN))} · ${ev.size} Fotos</span>
          ${ev.person_names.length
            ? `<span class="ev-people">${escapeHtml(ev.person_names.slice(0, 5).join(", "))}${ev.person_names.length > 5 ? ` +${ev.person_names.length - 5}` : ""}</span>`
            : ""}
        </div>
        <form class="serie-form">
          <input type="text" list="dl-events" autocomplete="off"
                 value="${escapeHtml(ev.suggested_name || "")}"
                 placeholder="z. B. Max 30. Geburtstag" />
          <button type="submit">${ev.suggested_name ? "Bestätigen" : "Benennen"}</button>
        </form>
      </div>
      <div class="shot-strip"></div>
    </div>`).join("");

  host.querySelectorAll(".serie").forEach((el) => {
    const ev = d.events[Number(el.dataset.i)];
    bindShotStrip(el.querySelector(".shot-strip"), ev.photo_ids || ev.cover || []);
    el.querySelector(".serie-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = el.querySelector("input");
      const name = input.value.trim();
      if (!name) return;
      input.disabled = true;
      try {
        await api("/api/events/name", {
          method: "POST",
          body: JSON.stringify({
            name, channel: ev.channel, start: ev.start, end: ev.end,
            photo_count: ev.size, photo_ids: ev.photo_ids || [],
          }),
        });
        refreshEventNames();
        evTab = "named";
        nmChannel = ev.channel || "";
        loadEventTab();
      } catch (err) {
        input.disabled = false;
        notify(`Konnte nicht gespeichert werden: ${err.message || err}`, { kind: "error" });
      }
    });
  });
}

let nmChannel = "";

async function loadNamed() {
  const host = $("nm-list");
  host.innerHTML = "<p class='muted'>Lade …</p>";
  $("nm-chan").innerHTML = CHANNEL_FILTERS
    .map((f) => `<button type="button" class="chan${f.key === nmChannel ? " on" : ""}"
                   data-chan="${f.key}">${escapeHtml(f.label)}</button>`).join("");
  $("nm-chan").querySelectorAll(".chan").forEach((b) =>
    b.addEventListener("click", () => { nmChannel = b.dataset.chan; loadNamed(); }));
  let d;
  try {
    d = await api(`/api/events/named?detail=1&limit=200&channel=${encodeURIComponent(nmChannel)}`);
  } catch (err) {
    host.innerHTML = `<p class='muted'>Benannte Serien nicht ladbar (${escapeHtml(String(err.message || err).slice(0, 140))})</p>`;
    return;
  }
  $("nm-meta").textContent = `${d.total} benannte Serie${d.total === 1 ? "" : "n"}`;
  if (!d.events.length) {
    host.innerHTML = "<p class='muted'>Noch keine Serie benannt.</p>";
    return;
  }
  host.innerHTML = d.events.map((ev, i) => `
    <div class="serie" data-i="${i}">
      <div class="serie-head">
        <div>
          <strong>${escapeHtml(ev.name)}</strong>
          <span class="muted">${escapeHtml(evDate(ev.date))} · ${escapeHtml(eventWhen(ev, WHEN_UNKNOWN))} · ${ev.size} Fotos
            · ${escapeHtml((ev.folders || []).join(" · ") || "ohne Ordner")}</span>
          ${ev.person_names.length
            ? `<span class="ev-people">${escapeHtml(ev.person_names.slice(0, 5).join(", "))}${ev.person_names.length > 5 ? ` +${ev.person_names.length - 5}` : ""}</span>`
            : ""}
        </div>
        <div class="nm-actions">
          ${ev.needs_shelve
            ? `<button type="button" class="mini nm-shelve">Fotos dorthin legen</button>`
            : `<span class="muted">liegt schon im eigenen Ordner</span>`}
          <button type="button" class="mini ghost nm-forget">Namen löschen</button>
        </div>
      </div>
      <form class="serie-form nm-rename">
        <input type="text" list="dl-events" value="${escapeHtml(ev.name)}"
               aria-label="Serienname" />
        <button type="submit">Umbenennen</button>
      </form>
      ${ev.needs_shelve && ev.dest
        ? `<p class="muted hint nm-dest">Optional nach <code>${escapeHtml(ev.dest)}</code> — nur die Fotos ohne ✕, der Dump bleibt.</p>`
        : `<p class="muted hint">✕ nimmt das Foto aus der Serie; nach dem Neuladen bleibt es draußen.</p>`}
      <div class="shot-strip"></div>
    </div>`).join("");
  host.querySelectorAll(".serie").forEach((el) => {
    const ev = d.events[Number(el.dataset.i)];
    ev.excluded = new Set();
    bindShotStrip(el.querySelector(".shot-strip"), ev.photo_ids || ev.cover || [], {
      excludable: true,
      excluded: ev.excluded,
      onToggle: (id, isOut) => {
        api("/api/events/members", {
          method: "POST",
          body: JSON.stringify({
            photo_ids: [id],
            name: isOut ? null : ev.name,
          }),
        }).catch((err) => notify(`Konnte Serie nicht anpassen: ${err.message || err}`, { kind: "error" }));
        const kept = (ev.photo_ids || []).filter((x) => !ev.excluded.has(x)).length;
        const dest = el.querySelector(".nm-dest");
        if (dest && ev.dest) {
          dest.innerHTML = `Optional nach <code>${escapeHtml(ev.dest)}</code> — ${kept} Foto${kept === 1 ? "" : "s"} ohne ✕, der Dump bleibt.`;
        }
      },
    });
    const btn = el.querySelector(".nm-shelve");
    if (btn) btn.addEventListener("click", () => shelveSeries(ev, btn));
    el.querySelector(".nm-forget").addEventListener("click", () => forgetSeries(ev, el));
    el.querySelector(".nm-rename").addEventListener("submit", (e) => {
      e.preventDefault();
      renameSeries(ev, el);
    });
  });
}

async function forgetSeries(ev, el) {
  if (!await askConfirm({
    title: `Namen „${ev.name}“ löschen`,
    lead: "Die Dateien bleiben wo sie sind — nur der Name der Serie verschwindet.",
    ok: "Namen löschen", danger: true,
  })) return;
  try {
    await api("/api/events/forget", {
      method: "POST",
      body: JSON.stringify({
        name: ev.name, channel: ev.channel, start: ev.start, end: ev.end,
        photo_ids: ev.photo_ids || [],
      }),
    });
    el.remove();
    refreshEventNames();
  } catch (err) {
    notify(`Konnte Namen nicht löschen: ${err.message || err}`, { kind: "error" });
  }
}

async function renameSeries(ev, el) {
  const name = el.querySelector(".nm-rename input")?.value.trim();
  if (!name || name === ev.name) return;
  try {
    await api("/api/events/name", {
      method: "POST",
      body: JSON.stringify({
        name, channel: ev.channel, start: ev.start, end: ev.end,
        photo_count: ev.size, photo_ids: ev.photo_ids || [],
      }),
    });
    ev.name = name;
    el.querySelector(".serie-head strong").textContent = name;
    refreshEventNames();
  } catch (err) {
    notify(`Konnte nicht umbenennen: ${err.message || err}`, { kind: "error" });
  }
}

async function shelveSeries(ev, btn) {
  const kept = (ev.photo_ids || []).filter((id) => !(ev.excluded && ev.excluded.has(id)));
  const n = kept.length;
  if (!n) {
    notify("Keine Fotos übrig — zuerst mit ✕ nichts mehr übrig gelassen.", { kind: "error" });
    return;
  }
  const dest = ev.dest || "einen neuen Ordner";
  if (!await askConfirm({
    title: `${n} Foto${n === 1 ? "" : "s"} ablegen`,
    lead: `Nach <b>${escapeHtml(dest)}</b> verschieben — nur diese Dateien, ohne die mit ✕. `
        + `${escapeHtml((ev.folders || []).join(", ") || "Der Dump")} bleibt sonst unangetastet.`,
    ok: "Verschieben",
  })) return;
  btn.disabled = true;
  btn.textContent = "verschiebt …";
  try {
    const res = await api("/api/events/shelve", {
      method: "POST",
      body: JSON.stringify({ name: ev.name, photo_ids: kept, dry_run: false }),
    });
    if (res.failed && res.failed.length) {
      notify(`Abgelegt, aber ${res.failed.length} Fehler.`, { kind: "error" });
    }
    loadNamed();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Fotos dorthin legen";
    notify(`Konnte nicht ablegen: ${err.message || err}`, { kind: "error" });
  }
}


async function loadSuggestions() {
  const host = $("sg-list");
  host.innerHTML = "<p class='muted'>Lade …</p>";
  let d;
  try {
    d = await api(`/api/events/suggestions?limit=${EV_PAGE}&offset=${sgOffset}`);
  } catch (err) {
    host.innerHTML = `<p class='muted'>Vorschläge nicht ladbar (${escapeHtml(String(err.message || err).slice(0, 140))})</p>`;
    return;
  }
  if (!d.suggestions.length && sgOffset > 0 && d.total > 0) {
    sgOffset = Math.max(0, Math.floor((d.total - 1) / EV_PAGE) * EV_PAGE);
    return loadSuggestions();
  }
  sgMeta = {
    offset: d.offset || 0,
    returned: d.returned || d.suggestions.length,
    total: d.total || 0,
  };
  const from = sgMeta.total ? sgMeta.offset + 1 : 0;
  const to = sgMeta.offset + sgMeta.returned;
  $("sg-meta").textContent = sgMeta.total
    ? `${vorschlaegeLabel(sgMeta.total)} · ${from}–${to}`
    : "Nichts Offenes.";
  renderPager(["sg-pager", "sg-pager-2"], sgMeta.offset, sgMeta.returned, sgMeta.total, (off) => {
    sgOffset = off;
    loadSuggestions();
  });
  if (!d.suggestions.length) {
    host.innerHTML = "<p class='muted'>Keine Vorschläge. Serien zuerst benennen hilft.</p>";
    return;
  }
  host.innerHTML = d.suggestions.map((s, i) => renderSuggestion(s, i)).join("");
  host.querySelectorAll(".serie").forEach((el) => {
    const s = d.suggestions[Number(el.dataset.i)];
    el.querySelectorAll(".shot-strip").forEach((strip) => {
      const ids = strip.dataset.ids ? strip.dataset.ids.split(",") : [];
      bindShotStrip(strip, ids.filter(Boolean));
    });
    const accept = el.querySelector(".sg-ok");
    const reject = el.querySelector(".sg-no");
    if (accept) accept.addEventListener("click", () => acceptSuggestion(s, el));
    if (reject) reject.addEventListener("click", () => rejectSuggestion(s, el));
    bindSuggestionDest(el, s);
  });
}

function suggestionGone(el) {
  el.remove();
  const left = $("sg-list").querySelectorAll(".serie").length;
  sgMeta.total = Math.max(0, (sgMeta.total || 1) - 1);
  sgMeta.returned = left;
  const from = sgMeta.total ? sgMeta.offset + 1 : 0;
  const to = sgMeta.offset + sgMeta.returned;
  $("sg-meta").textContent = sgMeta.total
    ? `${vorschlaegeLabel(sgMeta.total)} · ${from}–${to}`
    : "Nichts Offenes.";
  renderPager(["sg-pager", "sg-pager-2"], sgMeta.offset, sgMeta.returned, sgMeta.total, (off) => {
    sgOffset = off;
    loadSuggestions();
  });
  if (!left && sgMeta.total > 0) loadSuggestions();
  else if (!left) {
    $("sg-list").innerHTML = "<p class='muted'>Keine Vorschläge. Serien zuerst benennen hilft.</p>";
  }
}

function suggestionSources(s) {
  const evs = s.kind === "unify_folders" ? [s.event] : [s.a, s.b];
  const out = [];
  for (const ev of evs) {
    if (!ev) continue;
    if (ev.sources && ev.sources.length) {
      for (const src of ev.sources) {
        const path = src.path || src.folder || "";
        if (path && !out.some((x) => x.path === path)) {
          out.push({
            path,
            folder: src.folder || path,
            size: src.size || (src.photo_ids || []).length,
            photo_ids: src.photo_ids || [],
          });
        } else if (path) {
          const existing = out.find((x) => x.path === path);
          for (const id of src.photo_ids || []) {
            if (id && existing.photo_ids.indexOf(id) < 0) existing.photo_ids.push(id);
          }
          existing.size = existing.photo_ids.length;
        }
      }
      continue;
    }
    const paths = ev.album_paths || [];
    if (paths.length) {
      for (const p of paths) {
        if (p && !out.some((x) => x.path === p)) {
          out.push({ path: p, folder: p.split(/[/\\]/).pop() || p, size: 0, photo_ids: [] });
        }
      }
    } else {
      for (const f of ev.folders || []) {
        if (f && !out.some((x) => x.path === f)) {
          out.push({ path: f, folder: f, size: 0, photo_ids: [] });
        }
      }
    }
  }
  return out;
}

function selectedMergeIds(s, dropped) {
  const sources = suggestionSources(s);
  const hasIds = sources.some((src) => (src.photo_ids || []).length);
  if (hasIds) {
    const ids = [];
    for (const src of sources) {
      if (dropped && dropped.has(sourcePath(src))) continue;
      for (const id of src.photo_ids || []) if (id && !ids.includes(id)) ids.push(id);
    }
    return ids;
  }
  if (s.kind === "unify_folders") return s.event.photo_ids || [];
  return [...(s.a && s.a.photo_ids || []), ...(s.b && s.b.photo_ids || [])];
}

function joinDest(parent, name) {
  if (!parent) return "";
  const n = (name || "").trim();
  if (!n) return "";
  const slash = parent.includes("\\") && !parent.includes("/") ? "\\" : "/";
  return parent.replace(/[\\/]+$/, "") + slash + n;
}

function pathsEqual(a, b) {
  const n = (p) => String(p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  return n(a) === n(b) && n(a) !== "";
}

function sourcePath(src) {
  if (!src) return "";
  if (typeof src === "string") return src;
  const path = src.path || src.folder || "";
  return typeof path === "string" ? path : "";
}

function paintFromTo(box, sources, destParent, name, dropped) {
  if (!box) return;
  const dest = joinDest(destParent, name);
  const skip = dropped || new Set();
  if (!sources.length && !destParent) {
    box.innerHTML = `<p class="muted hint">Zusammenlegen setzt nur den Namen. Ein Zielordner ist noch unklar — kein Ordner Fotos/Alben gefunden.</p>`;
    return;
  }
  const rows = sources.map((src, i) => {
    const path = sourcePath(src);
    const folder = (src && src.folder) || path.split(/[/\\]/).pop() || path;
    const n = (src && src.size) || 0;
    const on = !skip.has(path);
    const stays = dest && pathsEqual(path, dest);
    let tail = "";
    if (!on) tail = `<span class="muted">bleibt in diesem Ordner</span>`;
    else if (stays) tail = `<span class="muted">bleibt — liegt schon dort</span>`;
    else if (dest) tail = `<span class="sg-arrow">→</span><code class="sg-dest">${escapeHtml(dest)}</code>`;
    else if (destParent) tail = `<span class="sg-arrow">→</span><span class="muted">Namen eintragen für das Ziel</span>`;
    else tail = `<span class="sg-arrow">→</span><span class="muted">Ziel unbekannt</span>`;
    return `<li class="sg-move${on ? "" : " off"}">
      <label>
        <input type="checkbox" data-i="${i}" ${on ? "checked" : ""} />
        <code class="sg-path">${escapeHtml(path)}</code>
        <span class="muted">${n ? ` · ${n} Fotos` : ""} · ${escapeHtml(folder)}</span>
        ${tail}
      </label>
    </li>`;
  }).join("");
  box.innerHTML = `
    <p class="muted hint">Haken weg: dieser Ordner bleibt wo er ist. Zusammenlegen vergibt den Namen und legt die angehakten Ordner nach dem Ziel (Move, gleichnamige Dateien werden zu -2).</p>
    <ul class="sg-moves">${rows}</ul>`;
}

function bindSuggestionDest(el, s) {
  const box = el.querySelector(".sg-fromto");
  const input = el.querySelector("input");
  if (!box || (s.kind !== "neighbor" && s.kind !== "unify_folders")) return;
  el._sgSources = suggestionSources(s);
  el._sgDropped = el._sgDropped || new Set();
  const parent = s.dest_parent || "";
  const refresh = () => paintFromTo(
    box, el._sgSources, parent, input ? input.value : s.suggested_name, el._sgDropped,
  );
  if (input) input.addEventListener("input", refresh);
  if (!box.dataset.bound) {
    box.dataset.bound = "1";
    box.addEventListener("change", (e) => {
      const cb = e.target.closest("input[type=checkbox]");
      if (!cb || !box.contains(cb)) return;
      const src = el._sgSources[Number(cb.dataset.i)];
      if (!src) return;
      const p = sourcePath(src);
      if (cb.checked) el._sgDropped.delete(p);
      else el._sgDropped.add(p);
      refresh();
    });
  }
  refresh();
}

function renderSourceLane(ev) {
  if (!ev) return "";
  const label = (ev.folders || []).join(" · ") || "Ohne Album";
  const paths = ev.album_paths && ev.album_paths.length ? ev.album_paths : [];
  const pathHtml = paths.length
    ? paths.map((p) => `<code class="sg-path">${escapeHtml(p)}</code>`).join("")
    : `<span class="muted">${escapeHtml(label)}</span>`;
  return `<div class="sg-lane">
    <div class="sg-src">
      <span class="sg-src-label">${escapeHtml(label)} · ${ev.size || 0} Fotos — Quelle</span>
      ${pathHtml}
    </div>
    <div class="shot-strip" data-ids="${escapeHtml((ev.photo_ids || ev.cover || []).join(","))}"></div>
  </div>`;
}

function renderSuggestion(s, i) {
  if (s.kind === "neighbor") {
    const gap = s.gap_minutes >= 90
      ? `${(s.gap_minutes / 60).toFixed(1).replace(".", ",")} h dazwischen`
      : `${s.gap_minutes} min dazwischen`;
    const people = (s.shared_people || []).length
      ? `gemeinsam: ${s.shared_people.slice(0, 4).join(", ")}`
      : "";
    const n = s.photo_count || ((s.a && s.a.size || 0) + (s.b && s.b.size || 0));
    return `<div class="serie" data-i="${i}">
      <div class="serie-head">
        <div>
          <strong>Zwei Serien, ${escapeHtml(gap)} · ${n} Fotos</strong>
          ${people ? `<span class="ev-people">${escapeHtml(people)}</span>` : ""}
        </div>
        <form class="serie-form sg-form">
          <input type="text" list="dl-events" value="${escapeHtml(s.suggested_name || "")}"
                 placeholder="Name für beide" />
          <button type="button" class="sg-ok">Zusammenlegen</button>
          <button type="button" class="sg-no ghost">Nicht zusammen</button>
        </form>
      </div>
      ${renderSourceLane(s.a)}
      ${renderSourceLane(s.b)}
      <div class="sg-fromto"></div>
    </div>`;
  }
  if (s.kind === "unify_folders") {
    return `<div class="serie" data-i="${i}">
      <div class="serie-head">
        <div>
          <strong>Eine Serie, ${s.folders.length} Ordner</strong>
          <span class="muted">${escapeHtml(s.folders.join(" · "))} · ${s.event.size} Fotos</span>
        </div>
        <form class="serie-form">
          <input type="text" list="dl-events" value="${escapeHtml(s.suggested_name || "")}"
                 placeholder="Zielname" />
          <button type="button" class="sg-ok">Namen setzen</button>
          <button type="button" class="sg-no ghost">Ignorieren</button>
        </form>
      </div>
      ${renderSourceLane(s.event)}
      <div class="sg-fromto"></div>
    </div>`;
  }
  const a = s.a || {}, b = s.b || {};
  return `<div class="serie" data-i="${i}">
    <div class="serie-head">
      <div>
        <strong>Gleiche Uhrzeit, verschiedene Quellen</strong>
        <span class="muted">${escapeHtml(a.channel || "")} · ${escapeHtml(a.folder_name || "")}
          und ${escapeHtml(b.channel || "")} · ${escapeHtml(b.folder_name || "")}
          · ${s.delta_seconds || 0} s</span>
      </div>
      <form class="serie-form">
        <input type="text" list="dl-events" placeholder="Name (optional)" />
        <button type="button" class="sg-ok">Ist dieselbe Gelegenheit</button>
        <button type="button" class="sg-no ghost">Zufall</button>
      </form>
    </div>
    <div class="shot-strip" data-ids="${escapeHtml([a.id, b.id].filter(Boolean).join(","))}"></div>
  </div>`;
}

async function confirmMove(name, destParent, ids) {
  if (!destParent || !ids.length) return true;
  const dest = joinDest(destParent, name);
  return askConfirm({
    title: `${ids.length} Foto${ids.length === 1 ? "" : "s"} verschieben`,
    lead: `Nach <b>${escapeHtml(dest)}</b>. Abgewählte Ordner bleiben, `
        + "gleichnamige Dateien bekommen -2.",
    ok: "Verschieben",
  });
}

async function shelveChecked(name, destParent, ids) {
  if (!destParent || !ids.length) return null;
  return api("/api/events/shelve", {
    method: "POST",
    body: JSON.stringify({ name, photo_ids: ids, dest_parent: destParent, dry_run: false }),
  });
}

async function acceptSuggestion(s, el) {
  const name = el.querySelector("input")?.value.trim();
  const btn = el.querySelector(".sg-ok");
  if (s.kind === "neighbor") {
    if (!name) return;
    const ids = selectedMergeIds(s, el._sgDropped);
    if (!ids.length) {
      notify("Keine Ordner übrig — mindestens einen Haken setzen.", { kind: "error" });
      return;
    }
    if (!await confirmMove(name, s.dest_parent, ids)) return;
    btn.disabled = true;
    try {
      await api("/api/events/merge", {
        method: "POST",
        body: JSON.stringify({
          name,
          channel: s.a.channel,
          a_start: s.a.start, a_end: s.a.end,
          b_start: s.b.start, b_end: s.b.end,
          photo_ids: ids,
        }),
      });
      const moved = await shelveChecked(name, s.dest_parent, ids);
      if (moved && moved.failed && moved.failed.length) {
        notify(`Zusammengelegt, aber ${moved.failed.length} Dateien nicht verschoben.`, { kind: "error" });
      }
      suggestionGone(el);
      refreshEventNames();
    } catch (err) {
      btn.disabled = false;
      notify(err.message || err);
    }
    return;
  }
  if (s.kind === "unify_folders") {
    if (!name) return;
    const ids = selectedMergeIds(s, el._sgDropped);
    if (!ids.length) {
      notify("Keine Ordner übrig — mindestens einen Haken setzen.", { kind: "error" });
      return;
    }
    if (!await confirmMove(name, s.dest_parent, ids)) return;
    btn.disabled = true;
    try {
      await api("/api/events/name", {
        method: "POST",
        body: JSON.stringify({
          name, channel: s.event.channel, start: s.event.start, end: s.event.end,
          photo_count: ids.length, photo_ids: ids,
        }),
      });
      const moved = await shelveChecked(name, s.dest_parent, ids);
      if (moved && moved.failed && moved.failed.length) {
        notify(`Name gesetzt, aber ${moved.failed.length} Dateien nicht verschoben.`, { kind: "error" });
      }
      suggestionGone(el);
      refreshEventNames();
    } catch (err) {
      btn.disabled = false;
      notify(err.message || err);
    }
    return;
  }
  btn.disabled = true;
  try {
    if (name) {
      await api("/api/events/merge", {
        method: "POST",
        body: JSON.stringify({
          name,
          channel: s.a.channel || "camera",
          a_start: s.a.taken_at, a_end: s.a.taken_at,
          b_start: s.b.taken_at, b_end: s.b.taken_at,
          photo_ids: [s.a.id, s.b.id].filter(Boolean),
        }),
      });
    }
    suggestionGone(el);
  } catch (err) {
    btn.disabled = false;
    notify(err.message || err);
  }
}

async function rejectSuggestion(s, el) {
  const span = (ev, fallbackChan) => ({
    channel: ev.channel || fallbackChan,
    start: ev.start || ev.taken_at,
    end: ev.end || ev.taken_at,
  });
  const a = s.kind === "unify_folders" ? s.event : s.a;
  const b = s.kind === "unify_folders" ? s.event : s.b;
  try {
    await api("/api/events/reject", {
      method: "POST",
      body: JSON.stringify({
        a_channel: a.channel || "camera",
        a_start: a.start || a.taken_at,
        a_end: a.end || a.taken_at,
        b_channel: b.channel || "camera",
        b_start: b.start || b.taken_at,
        b_end: b.end || b.taken_at,
      }),
    });
    suggestionGone(el);
  } catch (err) {
    notify(err.message || err);
  }
}

async function loadAlbums() {
  const host = $("al-list");
  host.innerHTML = "<p class='muted'>Lade …</p>";
  let d;
  try {
    d = await api("/api/albums");
  } catch (err) {
    host.innerHTML = `<p class='muted'>Alben nicht ladbar (${escapeHtml(String(err.message || err).slice(0, 140))})</p>`;
    return;
  }
  $("al-meta").textContent = `${d.total} Ordner`;
  host.innerHTML = d.albums.map((a, i) => `
    <div class="serie" data-i="${i}">
      <div class="serie-head">
        <div>
          <strong>${escapeHtml(a.folder_name)}</strong>
          <span class="muted">${a.photo_count} Foto${a.photo_count === 1 ? "" : "s"}
            · ${escapeHtml(a.path)}</span>
          ${a.event_names.length
            ? `<span class="ev-people">${a.named_count} in Serien: ${escapeHtml(a.event_names.join(", "))}</span>`
            : ""}
          ${a.generic && a.event_names.length
            ? `<span class="muted">Dump — Serien unter „Benannt“ in eigene Ordner legen, nicht den ganzen Ordner umbenennen.</span>`
            : ""}
        </div>
        ${a.rename_whole ? `
        <form class="serie-form al-form">
          <input type="text" value="${escapeHtml(a.folder_name)}"
                 placeholder="Games Convention 2007" />
          <button type="submit">Umbenennen</button>
        </form>` : ""}
      </div>
    </div>`).join("");
  host.querySelectorAll(".serie").forEach((el) => {
    const a = d.albums[Number(el.dataset.i)];
    const form = el.querySelector(".al-form");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = el.querySelector("input");
      const newName = input.value.trim();
      if (!newName || newName === a.folder_name) return;
      if (!await askConfirm({
        title: `Ordner umbenennen`,
        lead: `„${escapeHtml(a.folder_name)}“ wird zu „${escapeHtml(newName)}“. `
            + `Nur verschieben, nicht kopieren — ${a.photo_count} Fotos im Index.`,
        ok: "Umbenennen",
      })) return;
      input.disabled = true;
      try {
        const dry = await api("/api/albums/rename", {
          method: "POST",
          body: JSON.stringify({ path: a.path, new_name: newName, dry_run: true }),
        });
        const res = await api("/api/albums/rename", {
          method: "POST",
          body: JSON.stringify({ path: a.path, new_name: newName, dry_run: false }),
        });
        if (res.failed && res.failed.length) {
          notify(`Umbenannt, aber ${res.failed.length} Index-Fehler.`, { kind: "error" });
        }
        el.querySelector("strong").textContent = newName;
        el.querySelector(".muted").textContent =
          `${dry.photos} Fotos · ${res.to || ""}`;
        input.disabled = false;
      } catch (err) {
        input.disabled = false;
        notify(`Konnte nicht umbenannt werden: ${err.message || err}`, { kind: "error" });
      }
    });
  });
}
