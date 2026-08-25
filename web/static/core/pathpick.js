/* Einen Pfad auswählen, ohne ihn abzutippen.

   Ein Freitextfeld für `/mnt/photo/Sonstiges/Screenshots` ist auf dem Handy
   eine Zumutung und lädt zu Tippfehlern in Verzeichnisnamen ein, die dann
   still angelegt werden.

   Deshalb Brotkrumen: der Pfad steht als Kette anklickbarer Stücke da. Ein
   Klick auf ein Stück *kürzt* darauf und öffnet ein Textfeld mit genau diesem
   Anfang -- man tippt also nur den Rest, nicht den Weg dorthin. Der letzte
   Krumen ist der Ordnername und immer direkt änderbar, denn das ist die
   Stelle, die man fast immer meint. */

export function createPathPick(el, initial, onChange = () => {}) {
  let value = normalize(initial);
  let editing = null; // null = Brotkrumen, sonst der Textanfang

  function normalize(p) {
    const t = String(p || "").trim().replace(/\/{2,}/g, "/").replace(/\/+$/, "");
    return t.startsWith("/") ? t : `/${t}`;
  }

  function segments() {
    return value.split("/").filter(Boolean);
  }

  function paint() {
    if (editing !== null) {
      el.innerHTML = `
        <div class="pp-edit">
          <input type="text" class="pp-input" value="${attr(editing)}"
                 spellcheck="false" autocapitalize="off" autocomplete="off">
          <button type="button" class="pp-ok" title="Übernehmen">✓</button>
        </div>
        <p class="pp-hint">Rest eintippen, dann ✓ oder Eingabetaste.</p>`;
      const input = el.querySelector(".pp-input");
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      const commit = () => {
        value = normalize(input.value);
        editing = null;
        paint();
        onChange(value);
      };
      input.onkeydown = (e) => {
        if (e.key === "Enter") { e.preventDefault(); commit(); }
        if (e.key === "Escape") { e.preventDefault(); editing = null; paint(); }
      };
      el.querySelector(".pp-ok").onclick = commit;
      return;
    }

    const segs = segments();
    el.innerHTML = `
      <div class="pp-crumbs">
        <span class="pp-root">/</span>
        ${segs.map((s, i) => `
          <button type="button" class="pp-crumb${i === segs.length - 1 ? " last" : ""}"
                  data-i="${i}" title="${i === segs.length - 1
                    ? "Ordnernamen ändern" : `bis hierher kürzen und weitertippen`}">${attr(s)}</button>
          ${i < segs.length - 1 ? '<span class="pp-sep">/</span>' : ""}`).join("")}
        <button type="button" class="pp-crumb pp-add" data-add="1" title="Unterordner anhängen">+</button>
      </div>`;

    el.querySelector(".pp-crumbs").onclick = (e) => {
      if (e.target.dataset.add) {
        editing = `${value}/`;
        paint();
        return;
      }
      const b = e.target.closest("[data-i]");
      if (!b) return;
      const i = Number(b.dataset.i);
      // Auf den Krumen kürzen. Beim letzten bleibt der Name stehen, damit man
      // ihn ändern statt neu schreiben kann; bei jedem anderen endet der
      // Anfang mit einem Schrägstrich und man tippt weiter.
      const keep = segs.slice(0, i + 1);
      editing = i === segs.length - 1 ? `/${keep.join("/")}` : `/${keep.join("/")}/`;
      paint();
    };
  }

  paint();
  return {
    get value() { return value; },
    set(next) { value = normalize(next); editing = null; paint(); onChange(value); },
  };
}

function attr(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
