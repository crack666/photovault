/* Ein Dialog, der zur Anwendung gehört.

   `prompt()` und `confirm()` sind Systemdialoge: auf dem Handy blenden sie
   „Auf example.com wird Folgendes angezeigt" davor, brechen jede Formatierung
   auf eine Textzeile herunter, und in manchen eingebetteten Browsern sind sie
   ganz gesperrt. Für eine Entscheidung, die Dateien verschiebt, ist das zu
   wenig -- man muss sehen, was passiert, und es soll bedienbar sein.

   Absichtlich klein gehalten: ein Rahmen, ein Rumpf aus HTML, Knöpfe, ein
   Versprechen auf die Antwort. Was im Rumpf steht, weiß der Aufrufer. */

let host = null;
let escHandler = null;
let keyHandler = null;
let rueckgabe = null;

/* Was in einem Dialog die Tastatur annimmt.

   `footer button` allein genuegt nicht: der Rumpf traegt Eingabefelder, der
   Kopf das Kreuz zum Schliessen, und der Ordnerwaehler legt eine ganze
   Liste von Knoepfen hinein. Was nicht in dieser Liste steht, ist mit Tab
   nicht erreichbar -- und was `disabled` ist, soll es auch nicht sein
   (`setBusy` schaltet die Fussknoepfe waehrend eines Laufs ab). */
const NIMMT_FOKUS = [
  "a[href]", "button:not([disabled])", "input:not([disabled])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]",
].join(",");

function fokussierbare(root) {
  return [...root.querySelectorAll(NIMMT_FOKUS)]
    .filter((el) => el.tabIndex !== -1 && el.offsetParent !== null);
}

function ensureHost() {
  if (host) return host;
  host = document.createElement("div");
  host.className = "pv-modal-host hidden";
  document.body.appendChild(host);
  return host;
}

/**
 * @param {object} o
 * @param {string} o.title    Überschrift
 * @param {string} [o.lead]   Erklärender Satz darunter
 * @param {string} [o.body]   HTML für den Rumpf
 * @param {Array}  o.buttons  [{id, label, kind}] -- kind: "primary" | "danger" | undefined
 * @returns {{root: Element, body: Element, wait: function, close: function, setBody: function, setBusy: function}}
 */
export function openModal({ title, lead = "", body = "", buttons = [] }) {
  const h = ensureHost();
  h.innerHTML = `
    <div class="pv-modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(title)}">
      <header>
        <h2>${escapeAttr(title)}</h2>
        <button class="pv-modal-x" data-modal="cancel" aria-label="Schließen">✕</button>
      </header>
      ${lead ? `<p class="pv-modal-lead">${lead}</p>` : ""}
      <div class="pv-modal-body">${body}</div>
      <footer>${buttons.map((b) =>
        `<button data-modal="${b.id}" class="${b.kind || ""}">${escapeAttr(b.label)}</button>`
      ).join("")}</footer>
    </div>`;
  h.classList.remove("hidden");

  const root = h.querySelector(".pv-modal");
  const bodyEl = h.querySelector(".pv-modal-body");
  let settle = null;

  function close(answer = null) {
    h.classList.add("hidden");
    h.innerHTML = "";
    if (escHandler) document.removeEventListener("keydown", escHandler);
    if (keyHandler) document.removeEventListener("keydown", keyHandler, true);
    escHandler = null;
    keyHandler = null;
    // Den Fokus dorthin zurueck, wo er herkam. Ohne das landet er nach dem
    // Schliessen auf <body>, und der naechste Tab faengt oben in der
    // Navigationsleiste an -- wer den Dialog aus einer Liste weit unten
    // geoeffnet hat, sucht seine Stelle von Hand wieder.
    const zurueck = rueckgabe;
    rueckgabe = null;
    if (zurueck && zurueck.isConnected && typeof zurueck.focus === "function") {
      zurueck.focus();
    }
    if (settle) { const s = settle; settle = null; s(answer); }
  }

  // Klick auf den Hintergrund und Escape brechen ab -- bei einer Aktion, die
  // Dateien bewegt, muss der Ausweg immer offensichtlich sein.
  h.onclick = (e) => {
    if (e.target === h) return close(null);
    const b = e.target.closest("[data-modal]");
    if (!b) return;
    close(b.dataset.modal === "cancel" ? null : b.dataset.modal);
  };
  escHandler = (e) => { if (e.key === "Escape") close(null); };
  document.addEventListener("keydown", escHandler);

  // `aria-modal="true"` steht oben im Markup -- das ist ein Versprechen an
  // Vorlesewerkzeuge, dass ausserhalb dieses Dialogs nichts erreichbar ist.
  // Fuer die Tastatur galt es nicht: Tab lief aus dem Dialog heraus in die
  // Seite dahinter, wo man Knoepfe bedienen konnte, die der Dialog gerade
  // verdeckt. Ein Markup, das etwas behauptet, was die Bedienung nicht
  // einhaelt, ist schlimmer als eines, das nichts behauptet.
  //
  // In der Erfassungsphase (`true`), damit ein Aufrufer mit eigenem
  // Tab-Handler im Rumpf nicht vorher abbricht.
  keyHandler = (e) => {
    if (e.key !== "Tab") return;
    const kette = fokussierbare(root);
    if (!kette.length) return;
    const erste = kette[0];
    const letzte = kette[kette.length - 1];
    // Auch der Fall "Fokus liegt draussen": dann hereinholen, statt den
    // Sprung ins Nichts zuzulassen.
    if (!root.contains(document.activeElement)) {
      e.preventDefault();
      (e.shiftKey ? letzte : erste).focus();
      return;
    }
    if (e.shiftKey && document.activeElement === erste) {
      e.preventDefault();
      letzte.focus();
    } else if (!e.shiftKey && document.activeElement === letzte) {
      e.preventDefault();
      erste.focus();
    }
  };
  document.addEventListener("keydown", keyHandler, true);

  // Vor dem ersten `focus()` merken, wohin es zurueckgeht.
  rueckgabe = document.activeElement;
  const first = root.querySelector("input, select, textarea, footer button");
  if (first) first.focus();

  return {
    root,
    body: bodyEl,
    close,
    setBody(html) { bodyEl.innerHTML = html; },
    setBusy(on, label) {
      root.classList.toggle("busy", !!on);
      root.querySelectorAll("footer button").forEach((b) => { b.disabled = !!on; });
      if (label) root.querySelector("footer").dataset.note = label;
    },
    wait() {
      return new Promise((resolve) => {
        if (h.classList.contains("hidden")) return resolve(null);
        settle = resolve;
      });
    },
  };
}

function escapeAttr(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}


/** Einen Text erfragen -- im eigenen Dialog, nicht per Systemabfrage.

    `prompt()` sieht auf dem Handy aus wie eine Warnung der Website, ist in
    manchen eingebetteten Browsern ganz gesperrt, und Chrome unterdrückt es
    nach mehreren Aufrufen von selbst. Wo es gesperrt ist, bricht die Zusage
    ab und *nichts* passiert -- ein Knopf, der aussieht wie kaputt.

    Stand vorher zweimal im Quelltext, einmal im Atlas und einmal nirgends:
    die Personenansicht benutzte noch `prompt()`. Deshalb hier, wo beide
    hinlangen können. */
export async function askText({ title, lead, placeholder = "", ok = "Übernehmen", rows = 2,
                                value = "" }) {
  const dlg = openModal({
    title, lead,
    body: `<textarea class="pv-text" id="pv-text" rows="${rows}"
            placeholder="${placeholder}" spellcheck="false"></textarea>`,
    buttons: [
      { id: "cancel", label: "Abbrechen" },
      { id: "ok", label: ok, kind: "primary" },
    ],
  });
  const field = dlg.root.querySelector("#pv-text");
  field.value = value;
  field.focus();
  field.select();
  field.onkeydown = (e) => {
    // Eingabetaste bestätigt, Umschalt+Eingabe macht einen Absatz.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      dlg.root.querySelector('[data-modal="ok"]').click();
    }
  };
  const answer = await dlg.wait();
  const text = (answer === "ok" ? field.value : "").trim();
  return text || null;
}


/** Eine Ja/Nein-Frage stellen -- die Entsprechung zu `askText`.

    `confirm()` hat dieselben zwei Probleme wie `prompt()`: es sieht aus wie
    eine Warnung der Website, und wo es gesperrt ist, bricht die Zusage ab.
    Bei einer Frage heisst das: die Handlung passiert nie, und niemand sagt
    warum. Bei „Ordner umbenennen" oder „Gesichter dauerhaft ignorieren" ist
    das ein Knopf, der aussieht wie kaputt.

    `danger` faerbt den Bestaetigen-Knopf -- fuer alles, was nicht durch einen
    zweiten Klick zurueckzuholen ist. */
export async function askConfirm({ title, lead = "", ok = "Ja", danger = false }) {
  const dlg = openModal({
    title, lead,
    buttons: [
      { id: "cancel", label: "Abbrechen" },
      { id: "ok", label: ok, kind: danger ? "danger" : "primary" },
    ],
  });
  return (await dlg.wait()) === "ok";
}

/* ---- Kurznachricht ------------------------------------------------------
   Der Ersatz fuer `alert()`.

   `alert()` haelt die Seite an, verlangt einen Klick fuer eine Information,
   die man nur zur Kenntnis nimmt -- und wo es gesperrt ist, wirft es. Das ist
   schlimmer als es klingt: der Aufruf steht meist *nach* der erfolgreichen
   Handlung, und alles danach (Auswahl leeren, Liste neu laden) faellt dann
   aus. Die Handlung hat gewirkt, die Oberflaeche zeigt das Gegenteil.

   Also eine Zeile am unteren Rand, die von selbst geht. Fehler bleiben
   laenger stehen als Erfolge, weil man sie lesen will. */

let noteBox = null;
let noteTimer = null;

export function notify(text, { kind = "ok", ms } = {}) {
  if (!noteBox) {
    noteBox = document.createElement("div");
    noteBox.className = "pv-note hidden";
    noteBox.setAttribute("role", "status");
    noteBox.addEventListener("click", () => noteBox.classList.add("hidden"));
    document.body.appendChild(noteBox);
  }
  noteBox.textContent = text;
  noteBox.className = `pv-note ${kind === "error" ? "bad" : ""}`;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => noteBox.classList.add("hidden"),
                         ms ?? (kind === "error" ? 12000 : 5000));
}
