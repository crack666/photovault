/* Der Gesichtsstreifen unter einem geöffneten Foto.

   „Gesichter ohne Namen“ arbeitet den Stapel von vorn ab und stellt die Frage
   verkehrt: hier ist ein Ausschnitt, wer ist das? Wenn man dagegen das ganze
   Foto vor sich hat, weiß man es oft sofort -- nur gab es keinen Weg, es zu
   sagen. Dieser Streifen ist dieser Weg.

   Getippt werden soll dabei so selten wie möglich. Zu jedem unbenannten
   Gesicht liefert der Server die ähnlichsten *benannten* Personen mit; in der
   Regel steht der richtige Name schon als Knopf da und ein Klick genügt.
   Das Textfeld ist der Ausweg, nicht der Weg. */

import { escapeHtml, num } from "../core/dom.js?v=12";
import { api, cropUrl } from "../core/api.js?v=12";

/* Ab hier ist ein Vorschlag so stark, dass er hervorgehoben wird -- die
   Prozentangabe hilft nur beim Zweifeln. */
const STRONG = 0.55;

let people = null;      // {id, name, face_count}[] -- einmal je Sitzung
let openFace = null;    // face_id, dessen Namensfeld gerade offen ist
let current = null;     // {photoId, faces, named, note}
let onChange = () => {};
let follow = null;      // {faceId, personId, name, msg} -- Nachfrage nach dem Benennen
let dropped = new Set(); // in der Nachfrage abgewählte Gesichter

export async function mountFaceStrip(host, photoId, opts = {}) {
  if (opts.onChange) onChange = opts.onChange;
  openFace = null;
  current = null;
  follow = null;
  dropped = new Set();
  if (!host) return;
  host.classList.remove("hidden");
  host.innerHTML = `<div class="fs-head muted">Gesichter …</div>`;
  let data;
  try {
    data = await api(`/api/faces?photo=${encodeURIComponent(photoId)}`);
  } catch (e) {
    host.innerHTML = `<div class="fs-err">Gesichter nicht abrufbar: ${
      escapeHtml(String(e.message || e))}</div>`;
    return;
  }
  if (!data.faces.length) { host.classList.add("hidden"); host.innerHTML = ""; return; }
  current = { photoId, ...data };
  paint(host);
}

function paint(host) {
  const { faces, named, note } = current;
  const open = faces.length - named;
  host.innerHTML = `
    <div class="fs-head">
      <b>${num(faces.length)} Gesicht${faces.length === 1 ? "" : "er"}</b>
      ${open ? `<span class="fs-open">${num(open)} ohne Namen</span>`
             : `<span class="muted">alle benannt</span>`}
      <span class="fs-state"></span>
    </div>
    ${note ? `<div class="fs-err">${escapeHtml(note)}</div>` : ""}
    <div class="fs-row">${faces.map(tile).join("")}</div>
    <div class="fs-panel"></div>`;
  if (follow) drawFollow(host);
  else if (openFace) drawPanel(host);
}

function tile(f) {
  const label = f.state === "named"
    ? escapeHtml(f.person_name || f.person_id)
    : (f.state_label ? escapeHtml(f.state_label) : "wer ist das?");
  const hint = f.suggestions && f.suggestions.length
    ? ` — Vorschlag: ${escapeHtml(f.suggestions[0].name)}` : "";
  return `
    <button class="fs-face ${f.state}${openFace === f.face_id ? " on" : ""}"
            data-face="${escapeHtml(f.face_id)}"
            title="${label}${hint} · ${f.size_px} px">
      <img src="${cropUrl(f.face_id)}?size=160" alt="" loading="lazy">
      <span>${label}</span>
    </button>`;
}

/* ---- Das Namensfeld ---------------------------------------------------- */

function drawPanel(host) {
  const panel = host.querySelector(".fs-panel");
  const f = current.faces.find((r) => r.face_id === openFace);
  if (!panel) return;
  if (!f) { panel.innerHTML = ""; return; }

  if (f.state === "named") {
    panel.innerHTML = `
      <div class="fs-form">
        <p>Zugeordnet zu <b>${escapeHtml(f.person_name || f.person_id)}</b>.</p>
        <div class="fs-acts">
          <button data-act="rename">Doch jemand anderes …</button>
          <button data-act="free">Zuordnung lösen</button>
        </div>
      </div>`;
    return;
  }

  // Vorschläge zuerst, Liste danach: die Reihenfolge ist die
  // Wahrscheinlichkeit, nicht das Alphabet.
  const sugg = (f.suggestions || []).map((s) => `
    <button class="fs-sugg${s.score >= STRONG ? " strong" : ""}"
            data-act="assign" data-person="${escapeHtml(s.id)}">
      <img src="${cropUrl(s.example_face_id)}?size=160" alt="">
      <span>${escapeHtml(s.name)}</span>
      <em>${Math.round(s.score * 100)} %</em>
    </button>`).join("");

  panel.innerHTML = `
    <div class="fs-form">
      ${sugg
        ? `<p class="fs-lead">Sieht aus wie:</p><div class="fs-suggs">${sugg}</div>`
        : `<p class="fs-lead muted">Keine benannte Person ähnelt diesem Gesicht${
             people && !people.length ? " — es ist noch niemand benannt" : ""}.</p>`}
      <label class="fs-pick">Aus der Liste:
        <select data-role="pick"><option value="">lädt …</option></select>
      </label>
      <label class="fs-new">Neuer Name:
        <input type="text" data-role="name" placeholder="Vorname genügt" autocomplete="off">
        <button data-act="create">Anlegen</button>
      </label>
      <div class="fs-acts">
        <button data-act="ignore" class="quiet">Ist niemand — dauerhaft ignorieren</button>
      </div>
    </div>`;
  fillPeople(panel);
}

async function fillPeople(panel) {
  const sel = panel.querySelector('[data-role="pick"]');
  if (!sel) return;
  if (!people) {
    try { people = await api("/api/persons"); }
    catch (e) {
      sel.innerHTML = `<option value="">nicht abrufbar: ${
        escapeHtml(String(e.message || e).slice(0, 60))}</option>`;
      return;
    }
  }
  sel.innerHTML = `<option value="">— wählen —</option>` + people
    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)} (${num(p.face_count)})</option>`)
    .join("");
}

/* ---- Bedienung --------------------------------------------------------- */

export function bindFaceStrip(host) {
  if (!host) return;

  host.addEventListener("click", async (e) => {
    const fu = e.target.closest("button[data-act^=\"follow\"]");
    if (fu) { await onFollow(host, fu); return; }

    const face = e.target.closest(".fs-face");
    if (face) {
      openFace = openFace === face.dataset.face ? null : face.dataset.face;
      paint(host);
      return;
    }
    const btn = e.target.closest("button[data-act]");
    if (!btn || !openFace) return;
    const ids = [openFace];
    const act = btn.dataset.act;
    if (act === "rename") { renameMode(host); return; }
    if (act === "assign") {
      await run(host, `/api/persons/${encodeURIComponent(btn.dataset.person)}/assign`,
                { face_ids: ids }, (r) => `Als ${r.name} gespeichert.`,
                (r) => ({ id: r.person, name: r.name }));
    } else if (act === "create") {
      const name = (host.querySelector('[data-role="name"]') || {}).value || "";
      if (!name.trim()) { state(host, "Bitte einen Namen eintragen."); return; }
      await run(host, "/api/persons", { name: name.trim(), face_ids: ids },
                (r) => `${r.name} angelegt.`, (r) => ({ id: r.id, name: r.name }));
    } else if (act === "free") {
      await run(host, "/api/persons/faces/unassign", { face_ids: ids },
                (r) => `Gelöst, ${num(r.photos_updated)} Foto aktualisiert.`);
    } else if (act === "ignore") {
      await run(host, "/api/persons/ignore", { face_ids: ids }, () => "Ignoriert.");
    }
  });

  host.addEventListener("change", async (e) => {
    if (e.target.dataset.role !== "pick" || !e.target.value || !openFace) return;
    await run(host, `/api/persons/${encodeURIComponent(e.target.value)}/assign`,
              { face_ids: [openFace] }, (r) => `Als ${r.name} gespeichert.`,
              (r) => ({ id: r.person, name: r.name }));
  });

  // Enter im Namensfeld heißt „anlegen“ -- sonst tippt man den Namen und
  // sucht dann den Knopf.
  host.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.target.dataset.role !== "name") return;
    e.preventDefault();
    const go = host.querySelector('[data-act="create"]');
    if (go) go.click();
  });
}

/** Bei bestehender Zuordnung erst nach dem Klick das Namensfeld zeigen. */
function renameMode(host) {
  const f = current.faces.find((r) => r.face_id === openFace);
  if (f) {
    f.state = "open";
    f.state_label = `bisher: ${f.person_name || f.person_id}`;
  }
  paint(host);
}

async function run(host, path, body, done, named) {
  state(host, "speichert …");
  let res;
  try {
    res = await api(path, { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    state(host, `Fehlgeschlagen: ${String(e.message || e).slice(0, 160)}`);
    return;
  }
  const msg = done(res);
  const photoId = current.photoId;
  const faceId = openFace;
  // Neu laden statt lokal nachziehen: eine Zuordnung verändert auch die
  // Vorschläge der übrigen Gesichter auf demselben Foto.
  people = null;
  await mountFaceStrip(host, photoId);
  onChange();
  const who = named ? named(res) : null;
  if (who && who.id) {
    follow = { faceId, personId: who.id, name: who.name || who.id, msg };
    dropped = new Set();
    await drawFollow(host);
  }
  state(host, msg);
}

function state(host, text) {
  const el = host.querySelector(".fs-state");
  if (el) el.textContent = text;
}

/* ---- Die Nachfrage ----------------------------------------------------
   Direkt nach dem Benennen: dieselbe Person liegt meist noch auf zwei bis
   fünf weiteren Gesichtern, die niemand benannt hat. Der richtige Moment zu
   fragen ist genau jetzt -- und mit Bildern, nicht mit einer Zahl. Bei
   Geschwistern und Kindern liegt die Ähnlichkeit hoch; das entscheidet ein
   Mensch, keine Schwelle. Abgewählt bleibt abgewählt. */

async function drawFollow(host) {
  const panel = host.querySelector(".fs-panel");
  if (!panel || !follow) return;
  if (!follow.faces) {
    panel.innerHTML = `<div class="fs-form muted">suche ähnliche Gesichter …</div>`;
    try {
      const r = await api(`/api/faces/${encodeURIComponent(follow.faceId)}/lookalikes`);
      follow.faces = r.faces;
      follow.total = r.count;
      follow.threshold = r.threshold;
    } catch (e) {
      // Kein stiller Abbruch: ohne Hinweis sieht das aus wie „es gibt keine“.
      panel.innerHTML = `<div class="fs-err">Ähnliche Gesichter nicht abrufbar: ${
        escapeHtml(String(e.message || e))}</div>`;
      follow = null;
      return;
    }
  }
  if (!follow.faces.length) { follow = null; panel.innerHTML = ""; return; }

  const keep = follow.faces.filter((f) => !dropped.has(f.face_id));
  panel.innerHTML = `
    <div class="fs-form fs-follow">
      <p class="fs-lead">Diese ${follow.faces.length === 1 ? "Aufnahme sieht" : "Aufnahmen sehen"}
        genauso aus — auch <b>${escapeHtml(follow.name)}</b>?
        ${follow.total > follow.faces.length
          ? `<span class="muted">(${num(follow.faces.length)} von ${num(follow.total)} gezeigt)</span>` : ""}</p>
      <div class="fs-suggs">${follow.faces.map((f) => `
        <button class="fs-sugg${dropped.has(f.face_id) ? " out" : " strong"}"
                data-act="follow-toggle" data-face2="${escapeHtml(f.face_id)}"
                title="Ähnlichkeit ${Math.round(f.score * 100)} % · ${f.size_px} px">
          <img src="${cropUrl(f.face_id)}?size=160" alt="">
          <em>${Math.round(f.score * 100)} %</em>
        </button>`).join("")}</div>
      <p class="muted fs-fine">Antippen entfernt eine Aufnahme aus der Auswahl.
        Eine falsche Zuordnung lässt sich später über „Zuordnung lösen“ wieder aufheben.</p>
      <div class="fs-acts">
        <button data-act="follow-go" ${keep.length ? "" : "disabled"}>
          ${keep.length ? `${num(keep.length)} zuordnen` : "nichts ausgewählt"}</button>
        <button data-act="follow-no" class="quiet">Nur dieses eine, danke</button>
      </div>
    </div>`;
}

async function onFollow(host, btn) {
  if (!follow) return;
  const act = btn.dataset.act;

  if (act === "follow-toggle") {
    const id = btn.dataset.face2;
    if (dropped.has(id)) dropped.delete(id);
    else dropped.add(id);
    await drawFollow(host);
    return;
  }

  if (act === "follow-no") {
    follow = null;
    paint(host);
    return;
  }

  // follow-go: die noch ausgewählten derselben Person zuordnen.
  const ids = follow.faces.map((f) => f.face_id).filter((id) => !dropped.has(id));
  if (!ids.length) return;
  const { personId, name } = follow;
  const photoId = current.photoId;
  state(host, `ordne ${num(ids.length)} zu …`);
  let res;
  try {
    res = await api(`/api/persons/${encodeURIComponent(personId)}/assign`,
                    { method: "POST", body: JSON.stringify({ face_ids: ids }) });
  } catch (e) {
    state(host, `Fehlgeschlagen: ${String(e.message || e).slice(0, 160)}`);
    return;
  }
  people = null;
  await mountFaceStrip(host, photoId);
  onChange();
  state(host, `${num(res.assigned)} weitere Gesichter sind jetzt ${name}.`);
}
