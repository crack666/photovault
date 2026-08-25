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
    escHandler = null;
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
