/* Was diese Installation kann — einmal gefragt, überall benutzt.

   Vorher prüfte jede Stelle für sich, und die meisten prüften gar nicht: das
   Freitextfeld nahm eine Eingabe an und antwortete erst beim Suchen mit 503,
   der Haken „Textvektoren neu rechnen" stand auch ohne Ollama da, und der
   Atlas riet zu einem Befehl, der ohne Zusatzpaket scheitert.

   Ein Versprechen, kein Wert: alle Aufrufer teilen dieselbe Abfrage, auch
   wenn drei Ansichten gleichzeitig fragen. */

let pending = null;

export function capabilities() {
  if (!pending) {
    pending = fetch("/api/capabilities")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      // Antwortet die API nicht, wird nichts gesperrt. Etwas anzubieten, das
      // dann scheitert, ist besser als etwas zu verstecken, das ginge.
      .catch(() => ({ features: {}, ollama: { reachable: true }, accelerator: {} }));
  }
  return pending;
}

/** Frisch nachfragen — nach einem Lauf, der etwas erzeugt hat. */
export function forgetCapabilities() {
  pending = null;
}

/** Ein Merkmal, mit brauchbarem Ausfall, wenn die Frage nicht ankam. */
export async function feature(id) {
  const state = await capabilities();
  return state.features?.[id] || { ok: true, why: "", lost: "", label: id };
}

/**
 * Ein Bedienelement sperren, wenn sein Merkmal fehlt — und den Grund
 * daneben schreiben, statt es stumm auszugrauen.
 *
 * @param {string} id        Merkmal aus `FEATURES`
 * @param {Element} control  was gesperrt wird
 * @param {Element} [note]   wohin der Grund kommt
 */
export async function gate(id, control, note) {
  const f = await feature(id);
  if (f.ok) {
    if (control) control.disabled = false;
    if (note) { note.textContent = ""; note.classList.add("hidden"); }
    return true;
  }
  if (control) control.disabled = true;
  if (note) {
    note.textContent = `${f.label} nicht verfügbar: ${f.why}${f.lost ? ` ${f.lost}` : ""} `;
    /* Und ein Weg zurück, ohne die Seite neu zu laden.

       Die Antwort auf „was kann diese Installation" wird einmal je Sitzung
       geholt und danach geteilt — richtig so, sonst fragen drei Ansichten
       dasselbe dreimal. Der Preis war: wer Ollama startet, *nachdem* die
       Seite offen war, blieb bis zum Neuladen gesperrt. Genau dafür gab es
       `forgetCapabilities`, und niemand rief es.

       Der Knopf steht neben dem Grund, weil man ihn genau dort sucht: man
       liest, was fehlt, behebt es, und will es sofort noch einmal wissen. */
    const erneut = document.createElement("button");
    erneut.type = "button";
    erneut.className = "mini";
    erneut.textContent = "erneut prüfen";
    erneut.addEventListener("click", async () => {
      erneut.disabled = true;
      erneut.textContent = "prüft …";
      forgetCapabilities();
      await gate(id, control, note);
    });
    note.appendChild(erneut);
    note.classList.remove("hidden");
  }
  return false;
}
