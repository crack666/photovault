/* Der Suchtab: Ausdrucks-Builder, Bereichs-Waehler, Trefferliste.

   Der Ausdrucksbaum hier ist derselbe, den das Backend nach Qdrant
   uebersetzt; die Klartextform holen wir uns von dort zurueck, damit Anzeige
   und tatsaechliche Abfrage nicht auseinanderlaufen koennen.

   Braucht von aussen nur Bausteine -- die Personenliste, die Blaetterleiste,
   die Grossansicht -- und nichts aus app.js. */

import { api, cropUrl } from "../core/api.js?v=58";
import { feature, gate } from "../core/capabilities.js?v=58";
import { $, escapeHtml, num } from "../core/dom.js?v=58";
import { notify } from "../core/modal.js?v=58";
import { renderPager } from "../core/pager.js?v=58";
import { loadPeopleList, peopleList } from "../core/people.js?v=58";
import { showLightbox } from "../lightbox/index.js?v=58";

/* ---- Ausdrucks-Builder ----
   Der Ausdrucksbaum hier ist derselbe, den das Backend nach Qdrant übersetzt. Die
   Klartextform holen wir uns von dort zurück, damit Anzeige und tatsächliche
   Abfrage nicht auseinanderlaufen können. */

const FIELDS = [
  { key: "person",     label: "zeigt Person",   kind: "person" },
  { key: "year",       label: "aus dem Jahr",   kind: "text", ph: "2015" },
  { key: "date_from",  label: "aufgenommen ab", kind: "text", ph: "2015-06-01" },
  { key: "date_to",    label: "aufgenommen bis",kind: "text", ph: "2015-08-31" },
  { key: "location",   label: "am Ort",         kind: "text", ph: "griechenland" },
  { key: "tag",        label: "mit Szene",      kind: "text", ph: "strand" },
  { key: "annotation", label: "mit Notiz",      kind: "text", ph: "Stripclub" },
  { key: "folder",     label: "im Album",       kind: "text", ph: "Abi 08" },
];

/* Bereichs-Wähler: der Bereich schränkt *jede* Suche ein, auch die
   Bedeutungssuche. Weil eine unsichtbare Einschränkung eine Falle wäre,
   stehen die Chips über dem Formular und der Umfang im Ergebnissatz. Leere
   Auswahl heißt „alle" -- nicht „keine", denn niemand sucht absichtlich im
   Nichts. Die Wahl bleibt über Sitzungen erhalten, sonst müsste man sie bei
   jedem Aufräumdurchgang neu treffen. */
const SCOPE_KEY = "pv-search-spaces";
let spacesCache = [];
let scopePick = new Set();

let peopleFilter = "";
let peopleLimit = 16;
let qbTree = { op: "and", children: [] };

export async function loadPersonPicker() {
  // Das Feld nahm bisher jede Eingabe an und antwortete erst beim Suchen mit
  // 503. Wer kein Ollama hat, tippt sonst einmal ins Leere und weiss nicht,
  // warum -- obwohl der Rest der Suche vollstaendig funktioniert.
  gate("freetext", $("q-text"), $("q-text-gate"));
  await loadPeopleList();
  await loadScope();
  renderBuilder();
  renderPeopleChips();
  await renderSearchExamples();
}

async function loadScope() {
  const bar = $("search-scope");
  if (!bar) return;
  if (!spacesCache.length) {
    try {
      spacesCache = (await api("/api/search/spaces")).spaces || [];
    } catch (e) {
      // Nicht wegfallen lassen: ohne Wähler sucht man ungewollt überall.
      $("scope-chips").innerHTML = "";
      $("scope-hint").textContent =
        `Bereiche nicht abrufbar (${String(e.message || e).slice(0, 90)}) — Suche läuft über alles.`;
      return;
    }
  }
  // Nur einen Bereich gibt es nichts zu wählen.
  bar.classList.toggle("hidden", spacesCache.length < 2);
  const known = new Set(spacesCache.map((s) => s.name));
  try {
    const saved = JSON.parse(localStorage.getItem(SCOPE_KEY) || "[]");
    scopePick = new Set(saved.filter((n) => known.has(n)));
  } catch { scopePick = new Set(); }
  renderScope();
}

function renderScope() {
  $("scope-chips").innerHTML = spacesCache.map((s) => `
    <button type="button" class="scope-chip${scopePick.has(s.name) ? " on" : ""}"
            data-space="${escapeHtml(s.name)}">
      ${escapeHtml(s.name)} <em>${num(s.count)}</em>
    </button>`).join("");
  $("scope-hint").textContent = scopePick.size
    ? `Suche nur in ${[...scopePick].join(", ")}.`
    : "Alle Bereiche — nichts eingeschränkt.";
}


function renderBuilder() {
  const root = $("qb-root");
  if (!root) return;
  root.innerHTML = "";
  root.appendChild(renderGroup(qbTree, [], true));
  if (!qbTree.children.length) {
    const p = document.createElement("p");
    p.className = "muted hint";
    p.textContent = "Keine extra Bedingung — Personen oben, Jahr oder Album hier.";
    root.appendChild(p);
  }
  updateExpression();
  renderPeopleChips();
}

function renderGroup(group, path, isRoot) {
  const box = document.createElement("div");
  box.className = isRoot ? "qb-group root" : "qb-group";
  group.children.forEach((child, i) => {
    if (i > 0) box.appendChild(renderJoiner(group, path));
    const row = child.children ? renderGroup(child, [...path, i], false) : renderCond(child, [...path, i]);
    box.appendChild(row);
  });
  if (!isRoot) {
    const foot = document.createElement("div");
    foot.className = "qb-subadd";
    foot.innerHTML = `<button type="button" class="mini" data-sub="cond">+ Bedingung</button>
                      <button type="button" class="mini undo" data-sub="del">Klammer auflösen</button>`;
    foot.querySelector('[data-sub="cond"]').addEventListener("click", () => {
      group.children.push({ field: "person", value: "", label: "" });
      renderBuilder();
    });
    foot.querySelector('[data-sub="del"]').addEventListener("click", () => {
      removeAt(path);
      renderBuilder();
    });
    box.appendChild(foot);
  }
  return box;
}

function renderJoiner(group) {
  const wrap = document.createElement("div");
  wrap.className = "qb-join";
  wrap.innerHTML = `
    <button type="button" class="joiner ${group.op === "and" ? "on" : ""}" data-op="and">UND</button>
    <button type="button" class="joiner ${group.op === "or" ? "on" : ""}" data-op="or">ODER</button>`;
  wrap.querySelectorAll(".joiner").forEach((b) => {
    b.addEventListener("click", () => { group.op = b.dataset.op; renderBuilder(); });
  });
  return wrap;
}

function renderCond(cond, path) {
  const row = document.createElement("div");
  row.className = "qb-cond";
  const spec = FIELDS.find((f) => f.key === cond.field) || FIELDS[0];

  const sel = document.createElement("select");
  sel.innerHTML = FIELDS.map((f) =>
    `<option value="${f.key}" ${f.key === cond.field ? "selected" : ""}>${f.label}</option>`).join("");
  sel.addEventListener("change", () => {
    cond.field = sel.value; cond.value = ""; cond.label = "";
    renderBuilder();
  });
  row.appendChild(sel);

  if (spec.kind === "person") {
    const pick = document.createElement("select");
    pick.className = "person-select";
    pick.innerHTML = `<option value="">— Person wählen —</option>` +
      peopleList().map((p) =>
        `<option value="${escapeHtml(p.id)}" ${p.id === cond.value ? "selected" : ""}>${escapeHtml(p.name)} (${p.face_count})</option>`).join("");
    pick.addEventListener("change", () => {
      cond.value = pick.value;
      cond.label = (peopleList().find((p) => p.id === pick.value) || {}).name || pick.value;
      updateExpression();
      showFaceFor(row, cond.value);
    });
    row.appendChild(pick);
    showFaceFor(row, cond.value);
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = spec.ph || "";
    input.value = cond.value || "";
    input.addEventListener("input", () => { cond.value = input.value; updateExpression(); });
    row.appendChild(input);
  }

  const del = document.createElement("button");
  del.type = "button";
  del.className = "mini undo qb-del";
  del.textContent = "✕";
  del.title = "Bedingung entfernen";
  del.addEventListener("click", () => { removeAt(path); renderBuilder(); });
  row.appendChild(del);
  return row;
}

/* Das Gesicht neben der Auswahl: zeigt sofort, ob die richtige Person gemeint ist. */
function showFaceFor(row, personId) {
  row.querySelector(".qb-face")?.remove();
  const p = peopleList().find((x) => x.id === personId);
  if (!p) return;
  const img = document.createElement("img");
  img.className = "qb-face";
  img.src = `${cropUrl(p.cover_face_id)}?size=160`;
  img.alt = p.name;
  img.title = `${p.name} — ${p.face_count} Gesichter`;
  row.insertBefore(img, row.querySelector(".qb-del"));
}

function nodeAt(path) {
  let node = qbTree;
  for (const i of path.slice(0, -1)) node = node.children[i];
  return node;
}

function removeAt(path) {
  const parent = nodeAt(path);
  parent.children.splice(path[path.length - 1], 1);
}

function selectedPeople() {
  return (qbTree.children || []).filter((c) => c.field === "person" && c.value);
}

function setPersonInQuery(person, on) {
  const kids = qbTree.children;
  const i = kids.findIndex((c) => c.field === "person" && c.value === person.id);
  if (on && i < 0) kids.push({ field: "person", value: person.id, label: person.name });
  if (!on && i >= 0) kids.splice(i, 1);
}

function renderPeopleChips() {
  const host = $("search-people");
  if (!host) return;
  if (!peopleList().length) {
    host.innerHTML = "<p class='muted hint'>Noch niemand benannt — zuerst unter „Wer ist das?“.</p>";
    return;
  }
  const chosen = new Set(selectedPeople().map((c) => c.value));
  const needle = peopleFilter.trim().toLowerCase();
  const hit = (p) => !needle || (p.name || "").toLowerCase().includes(needle)
    || (p.aliases || []).some((a) => String(a).toLowerCase().includes(needle));

  // Bei 114 benannten Personen waren 98 unerreichbar: die Liste endete nach
  // den 16 mit den meisten Gesichtern. Angehakte gehören immer dazu -- sonst
  // sieht man nicht mehr, wen man gewählt hat, sobald man tippt.
  const matching = peopleList().filter(hit);
  const visible = [
    ...peopleList().filter((p) => chosen.has(p.id)),
    ...matching.filter((p) => !chosen.has(p.id)).slice(0, peopleLimit),
  ];
  const rest = matching.filter((p) => !chosen.has(p.id)).length - peopleLimit;

  host.innerHTML = `
    <div class="person-find">
      <input type="search" id="person-find" placeholder="Namen suchen — ${peopleList().length} benannt"
             value="${escapeHtml(peopleFilter)}" autocomplete="off" />
      ${chosen.size ? `<button type="button" class="mini" id="person-none">Auswahl leeren</button>` : ""}
    </div>
    ${visible.map((p) => `
      <button type="button" class="search-person${chosen.has(p.id) ? " on" : ""}" data-id="${escapeHtml(p.id)}">
        <img src="${cropUrl(p.cover_face_id)}?size=80" alt="" />
        ${escapeHtml(p.name)}
      </button>`).join("")}
    ${rest > 0 ? `<button type="button" class="mini" id="person-more">+${rest} weitere</button>` : ""}
    ${!visible.length ? `<p class="muted hint">Niemand mit „${escapeHtml(peopleFilter)}“.</p>` : ""}`;

  const find = $("person-find");
  find.oninput = () => {
    peopleFilter = find.value;
    peopleLimit = 16;
    const at = find.selectionStart;
    renderPeopleChips();
    // Nach dem Neuzeichnen ist das Feld ein anderes -- Fokus und Schreibmarke
    // müssen zurück, sonst tippt man nach jedem Zeichen ins Nichts.
    const again = $("person-find");
    again.focus();
    again.setSelectionRange(at, at);
  };
  if ($("person-more")) {
    $("person-more").onclick = () => { peopleLimit += 32; renderPeopleChips(); };
  }
  if ($("person-none")) {
    $("person-none").onclick = () => {
      selectedPeople().forEach((c) => {
        const p = peopleList().find((x) => x.id === c.value);
        if (p) setPersonInQuery(p, false);
      });
      renderBuilder();
      renderPeopleChips();
    };
  }
  host.querySelectorAll(".search-person").forEach((b) => {
    b.addEventListener("click", () => {
      const p = peopleList().find((x) => x.id === b.dataset.id);
      if (!p) return;
      setPersonInQuery(p, !b.classList.contains("on"));
      renderBuilder();
      renderPeopleChips();
    });
  });
}

/* Beispiele duerfen nicht an der Sperre vorbeischreiben.

   Zwei der Kacheln setzen Freitext. Ohne Ollama ist das Feld gesperrt, die
   Kachel schrieb aber direkt hinein -- und die Suche antwortete mit 503. Die
   Sperre war gut begruendet und griff an genau einer von vier Stellen. */
async function renderSearchExamples() {
  const host = $("search-examples");
  if (!host) return;
  const freitext = (await feature("freetext")).ok;
  const named = peopleList().filter((p) => (p.face_count || 0) >= 8);
  const a = named[0], b = named[1];
  const first = (p) => (p.name || "").split(/\s+/)[0] || p.name;
  const items = [];
  if (a && b) {
    items.push({
      title: `${first(a)} und ${first(b)}`,
      hint: "beide auf einem Foto",
      run: () => {
        qbTree = { op: "and", children: [] };
        setPersonInQuery(a, true);
        setPersonInQuery(b, true);
        $("q-text").value = "";
      },
    });
    // Das Beispielwort war „Bier" -- fest verdrahtet, und die Person davor
    // kommt aus den Daten. Bei einem Kind an erster Stelle stand da dann
    // „<Kind> · Bier". Ein Wort, das zu jedem passt, oder keins.
    items.push({
      title: `${first(a)} · Geburtstag`,
      hint: "Person plus was im Bild ist",
      needs: "freetext",
      run: () => {
        qbTree = { op: "and", children: [] };
        setPersonInQuery(a, true);
        $("q-text").value = "Geburtstag";
      },
    });
  }
  items.push({
    title: "Feuerwerk in der Nacht",
    hint: "nur Freitext, sortiert",
    needs: "freetext",
    run: () => {
      qbTree = { op: "and", children: [] };
      $("q-text").value = "Feuerwerk in der Nacht";
    },
  });
  items.push({
    title: "Strand",
    hint: "Szene aus den Tags",
    run: () => {
      qbTree = { op: "and", children: [{ field: "tag", value: "strand", label: "" }] };
      $("q-text").value = "";
    },
  });
  items.push({
    title: "Jahr 2015",
    hint: "ein ganzes Jahr",
    run: () => {
      qbTree = { op: "and", children: [{ field: "year", value: "2015", label: "" }] };
      $("q-text").value = "";
    },
  });
  const zeigbar = items.filter((ex) => freitext || ex.needs !== "freetext");
  host.innerHTML = zeigbar.map((ex, i) =>
    `<button type="button" class="search-ex" data-i="${i}">
       <strong>${escapeHtml(ex.title)}</strong>
       <span>${escapeHtml(ex.hint)}</span>
     </button>`).join("");
  host.querySelectorAll(".search-ex").forEach((btn) => {
    btn.addEventListener("click", async () => {
      zeigbar[Number(btn.dataset.i)].run();
      renderBuilder();
      await runSearch();
    });
  });
}


/* Vorschau lokal, damit es beim Tippen mitläuft; die verbindliche Fassung
   liefert der Server mit dem Suchergebnis. */
function localExpression(node, top = true) {
  if (!node.children) {
    const spec = FIELDS.find((f) => f.key === node.field);
    const val = (node.label || node.value || "").trim();
    return spec && val ? `${spec.label.toLowerCase()} ${val}` : "";
  }
  const parts = node.children.map((c) => localExpression(c, false)).filter(Boolean);
  if (!parts.length) return "";
  if (parts.length === 1) return parts[0];
  const joined = parts.join(` ${node.op === "or" ? "ODER" : "UND"} `);
  return top ? joined : `(${joined})`;
}

function updateExpression() {
  const text = localExpression(qbTree);
  const free = ($("q-text") && $("q-text").value.trim()) || "";
  let line = text ? `Fotos, die ${text}` : "Noch keine Bedingung — alle Fotos";
  if (free) line += `, sortiert nach „${free}“`;
  $("qb-expr").textContent = line;
}


/* Dieselbe Frage, andere Antwort: die Karte zeigt, *wo* die Treffer liegen.

   Geholt werden dafuer alle Kennungen, nicht die erste Seite -- die Karte
   waere sonst eine Stichprobe. Bei Freitext geht das nicht: das ist eine
   Rangfolge, keine Menge, und der Server schneidet oben ab. Er sagt es
   (`ranked`), und das Band sagt es weiter. */

/* Wie viele Treffer eine Seite fasst. Der Wert stand fruher als nackte 48
   im Rumpf der Anfrage, und daneben pruefte die Meldung auf dieselbe 48, um
   auf "erste Seite" zu schliessen -- zwei Stellen, eine Zahl. */
const QB_PAGE = 48;
let qbOffset = 0;

async function runSearch() {
  const box = $("search-results");
  const meta = $("search-meta");
  box.innerHTML = "<p class='muted'>Suche …</p>";
  if (meta) meta.textContent = "";
  let data;
  try {
    data = await api("/api/search/query", {
      method: "POST",
      body: JSON.stringify({
        query: qbTree,
        spaces: [...scopePick],
        caption_query: $("q-text").value.trim() || null,
        limit: QB_PAGE,
        offset: qbOffset,
      }),
    });
  } catch (err) {
    box.innerHTML = `<p class='muted'>Suche fehlgeschlagen (${escapeHtml(String(err.message || err).slice(0, 160))})</p>`;
    return;
  }
  const free = $("q-text").value.trim();
  // Ohne Bedingung heisst der Ausdruck "alle Fotos" -- mit dem Vorspann
  // "Fotos, die" davor wird daraus ein Stolpersatz.
  const satz = data.conditions ? `Fotos, die ${data.expression}` : "Alle Fotos";
  $("qb-expr").textContent =
    satz +
    (data.scope ? `, ${data.scope}` : "") +
    (free ? `, sortiert nach „${free}“` : "") +
    ` — ${data.total} Treffer` +
    (data.conditions ? ` · ${data.conditions} Bedingung${data.conditions === 1 ? "" : "en"}` : "");
  const gezeigt = data.returned ?? (data.results || []).length;
  if (meta) {
    // Die Zahl ist jetzt die ganze Menge, nicht die Seitenlaenge -- also
    // steht daneben, welcher Ausschnitt gerade zu sehen ist.
    meta.textContent = data.total
      ? (gezeigt < data.total
          ? `${data.total} Treffer · ${qbOffset + 1}–${qbOffset + gezeigt} zu sehen`
          : `${data.total} Treffer`)
      : "Keine Treffer.";
  }
  $("qb-to-atlas").classList.toggle("hidden", !data.total);
  renderResults(data.results || []);
  renderPager(["qb-pager", "qb-pager-2"], qbOffset, gezeigt, data.total,
              (next) => { qbOffset = next; runSearch(); }, QB_PAGE);
}

function renderResults(results, unknown = []) {
  const box = $("search-results");
  box.innerHTML = "";
  if (unknown.length) {
    const warn = document.createElement("p");
    warn.className = "muted warn";
    warn.textContent = `Unbekannte Person${unknown.length > 1 ? "en" : ""}: ${unknown.join(", ")} — noch niemand mit diesem Namen benannt.`;
    box.appendChild(warn);
  }
  if (!results.length) {
    box.insertAdjacentHTML(
      "beforeend",
      "<p class='muted'>Keine Treffer. Personen filtern hart; Freitext sortiert nur. Ohne Captions trifft „Bier“ oft nichts.</p>",
    );
    return;
  }
  results.forEach((r, i) => {
    const el = document.createElement("figure");
    el.className = "hit";
    const head = r.caption_display || [r.date, r.folder_name].filter(Boolean).join(" · ");
    const names = (r.person_names || []).join(", ");
    const notes = (r.annotations || []).map((a) => `<span class="note">${escapeHtml(a)}</span>`).join("");
    const tags = (r.scene_tags || []).slice(0, 6).map((t) => `<span class="note">${escapeHtml(t)}</span>`).join("");
    // Die erste Reihe eager: lazy laedt erst bei Sichtbarkeit, was je nach
    // Umgebung gar nicht ausloest und leere Kacheln hinterlaesst.
    const loading = i < 12 ? "eager" : "lazy";
    el.innerHTML = `
      <img loading="${loading}" src="/api/photos/${encodeURIComponent(r.id)}/thumb?size=320" alt="" />
      <figcaption>
        <div class="muted">${escapeHtml(head)}</div>
        ${names ? `<div class="names">${escapeHtml(names)}</div>` : ""}
        ${r.caption_de ? `<p>${escapeHtml(r.caption_de)}</p>` : `<p class="muted">Noch keine Caption</p>`}
        ${notes || tags ? `<div class="notes">${notes}${tags}</div>` : ""}
      </figcaption>`;
    el.querySelector("img").addEventListener("click", () => showLightbox(results, i));
    box.appendChild(el);
  });
}

/* Die Bindungen des Reiters, einmal. */
let bound = false;

export function bindSearch() {
  if (bound) return;
  bound = true;

  $("scope-chips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".scope-chip");
    if (!chip) return;
    const name = chip.dataset.space;
    if (scopePick.has(name)) scopePick.delete(name);
    else scopePick.add(name);
    // Alle angehakt ist dasselbe wie keiner -- dann lieber keiner, damit der
    // Satz "nichts eingeschränkt" sagt statt alle drei Namen aufzuzählen.
    if (scopePick.size === spacesCache.length) scopePick.clear();
    try { localStorage.setItem(SCOPE_KEY, JSON.stringify([...scopePick])); } catch { /* privater Modus */ }
    renderScope();
  });
  document.querySelectorAll("[data-add]").forEach((b) => {
    b.addEventListener("click", () => {
      if (b.dataset.add === "cond") {
        qbTree.children.push({ field: "person", value: "", label: "" });
      } else {
        qbTree.children.push({ op: "or", children: [{ field: "year", value: "", label: "" }] });
      }
      renderBuilder();
    });
  });
  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    // Eine neue Frage faengt auf Seite eins an.
    qbOffset = 0;
    await runSearch();
  });
  $("qb-to-atlas").addEventListener("click", async () => {
    const btn = $("qb-to-atlas");
    const frei = $("q-text").value.trim();
    btn.disabled = true;
    const alt = btn.textContent;
    btn.textContent = "holt Treffer …";
    let data;
    try {
      data = await api("/api/search/query", {
        method: "POST",
        body: JSON.stringify({
          query: qbTree,
          spaces: [...scopePick],
          caption_query: frei || null,
          ids_only: true,
        }),
      });
    } catch (err) {
      notify(`Konnte die Treffer nicht holen: ${err.message || err}`, { kind: "error" });
      btn.disabled = false; btn.textContent = alt;
      return;
    }
    btn.disabled = false;
    btn.textContent = alt;
    if (!data.ids?.length) { notify("Keine Treffer zum Zeigen."); return; }

    const satz = [data.conditions ? data.expression : "alle Fotos", data.scope,
                  frei ? `ähnlich zu „${frei}“` : ""].filter(Boolean).join(", ");
    const { focusFromSearch } = await import("./atlas/index.js?v=58");
    focusFromSearch({
      ids: data.ids,
      label: satz,
      note: data.ranked ? `der ${data.ids.length} ähnlichsten` : "Treffer",
    });
    document.querySelector('[data-tab="atlas"]').click();
  });
  $("q-text")?.addEventListener("input", updateExpression);
}
