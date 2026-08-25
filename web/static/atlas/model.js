/* Die Daten der Karte: laden, Anordnungen rechnen, einfaerben, filtern.
   Alles ohne DOM -- das macht die Anordnungen einzeln nachvollziehbar. */

const SRC = "/static/atlas/atlas.json";

// Muss zu tools/atlas_build.py passen.
export const FLAG = {
  PERSON: 1 << 0,
  CAPTION: 1 << 1,
  EXIF_DATE: 1 << 2,
  EVENT: 1 << 3,
  GPS: 1 << 4,
  NO_CLOCK: 1 << 5,
  FACES_UNNAMED: 1 << 6,
  IN_STACK: 1 << 7,
  STACK_HEAD: 1 << 8,
};

const DAY_MS = 86400000;

export async function loadAtlas() {
  const res = await fetch(SRC, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(
      "Noch keine Karte gerechnet. Einmal ausführen:\n" +
      "    python -m tools.atlas_build",
    );
  }
  const raw = await res.json();
  const n = raw.n;

  const model = {
    n,
    builtAt: raw.built_at,
    space: raw.space,
    ids: raw.ids,
    channels: raw.channels,
    persons: raw.persons || [],
    pe: raw.pe || [],
    clusters: raw.clusters,
    events: raw.events || [],
    ev: Int32Array.from(raw.ev || []),
    x: Float32Array.from(raw.x),
    y: Float32Array.from(raw.y),
    t: Int32Array.from(raw.t),
    cl: Int16Array.from(raw.cl),
    ch: Uint8Array.from(raw.ch),
    st: Int32Array.from(raw.st),
    fl: Uint16Array.from(raw.fl),
    fc: Uint8Array.from(raw.fc),
  };
  // Qdrant antwortet mit Punkt-IDs, die Karte rechnet mit Indizes.
  model.indexOfId = new Map(raw.ids.map((id, i) => [id, i]));
  model.year = Int16Array.from(model.t, (d) => (d < 0 ? -1 : new Date(d * DAY_MS).getUTCFullYear()));
  model.layouts = { bedeutung: byMeaning(model), zeit: byTime(model) };
  model.clusterLabel = raw.clusters.map(labelOf);
  return model;
}

function labelOf(c) {
  if (!c.terms.length) return "ohne Namen";
  // Erstes Wort gross -- „abistreich, juni, schuelern" liest sich sonst wie Fliesstext.
  const head = c.terms[0];
  return head.charAt(0).toUpperCase() + head.slice(1) + (c.terms.length > 1 ? ` · ${c.terms[1]}` : "");
}

/* ---- Anordnung 1: Bedeutung ------------------------------------------
   Direkt die UMAP-Koordinaten. Naehe heisst hier „sieht aehnlich aus". */

function byMeaning(m) {
  return { x: m.x, y: m.y };
}

/* ---- Anordnung 2: Zeit x Bedeutung -----------------------------------
   Waagerecht die Zeit, senkrecht dieselbe Bedeutungsachse wie oben. Ein
   Thema wird dadurch zu einem waagerechten Band und man sieht, ueber welche
   Jahre es sich zieht.

   Die Zeitachse ist **nicht** linear. An diesem Bestand liegen 67 % der
   Fotos in den letzten vier Jahren -- linear bekaemen zwanzig Jahre
   Familiengeschichte ein Drittel der Breite und die WhatsApp-Jahre zwei
   Drittel. Jedes Jahr bekommt deshalb Platz nach `Anzahl^0.4`: dichte Jahre
   bleiben breiter, aber ein duennes Jahr verschwindet nicht. */

const YEAR_POWER = 0.4;

function byTime(m) {
  const counts = new Map();
  for (let i = 0; i < m.n; i++) {
    const y = m.year[i];
    if (y > 0) counts.set(y, (counts.get(y) || 0) + 1);
  }
  const years = [...counts.keys()].sort((a, b) => a - b);
  const weights = years.map((y) => Math.pow(counts.get(y), YEAR_POWER));
  const total = weights.reduce((a, b) => a + b, 0) || 1;

  // Ganz links ein eigenes Feld fuer Fotos ohne Datum -- sie gehoeren auf
  // keine Jahreszahl, und sie unterzuschieben waere eine Behauptung.
  const undatedWidth = 0.04;
  const span = 1 - undatedWidth;

  const start = new Map();
  let acc = 0;
  years.forEach((y, i) => {
    start.set(y, undatedWidth + (acc / total) * span);
    acc += weights[i];
  });
  const width = new Map();
  years.forEach((y, i) => width.set(y, (weights[i] / total) * span));

  const x = new Float32Array(m.n);
  for (let i = 0; i < m.n; i++) {
    const y = m.year[i];
    if (y < 0) {
      x[i] = 0.005 + ((i * 2654435761) % 1000) / 1000 * (undatedWidth - 0.015);
      continue;
    }
    const jan1 = Date.UTC(y, 0, 1) / DAY_MS;
    const frac = Math.min(0.999, Math.max(0, (m.t[i] - jan1) / 366));
    x[i] = start.get(y) + frac * width.get(y);
  }
  return { x, y: m.y, years, start, width };
}

/* ---- Einfaerben -------------------------------------------------------
   Farbe traegt die Frage, nicht die Dekoration. */

export const COLOR_MODES = [
  { id: "kontinent", label: "Kontinent" },
  { id: "zustand", label: "Zustand" },
  { id: "kanal", label: "Herkunft" },
  { id: "jahr", label: "Jahr" },
];

/** Wie weit ist dieses Foto geordnet? 0 = unberuehrt, 4 = fertig. */
export function tidiness(flags) {
  return (
    (flags & FLAG.PERSON ? 1 : 0) +
    (flags & FLAG.CAPTION ? 1 : 0) +
    (flags & FLAG.EXIF_DATE ? 1 : 0) +
    (flags & FLAG.EVENT ? 1 : 0)
  );
}

// Warm = hier liegt noch Arbeit, ruhig = eingeordnet. Bewusst nicht
// Boersen-Rot/Gruen: „unbearbeitet" ist kein Fehler, nur eine offene Aufgabe.
const TIDY_RAMP = ["#e0703c", "#e0a13c", "#c9b455", "#7fae86", "#3fa8a0"];

const CHANNEL_COLOR = {
  camera: "#3fa8a0",
  whatsapp: "#8b7cc8",
  "whatsapp-sent": "#c87c9e",
  screenshot: "#7c8794",
  document: "#7c8794",
  download: "#7c8794",
};

export function colorFor(model, i, mode) {
  switch (mode) {
    case "zustand":
      return TIDY_RAMP[tidiness(model.fl[i])];
    case "kanal":
      return CHANNEL_COLOR[model.channels[model.ch[i]]] || "#8a8a8a";
    case "jahr": {
      const y = model.year[i];
      if (y < 0) return "#5c5c5c";
      const span = Math.max(1, 2026 - 2002);
      const h = 205 - ((y - 2002) / span) * 195; // blau (alt) -> gelb (neu)
      return `hsl(${h.toFixed(0)} 58% 58%)`;
    }
    default: {
      const h = (model.cl[i] * 137.508) % 360;
      return `hsl(${h.toFixed(0)} 55% 60%)`;
    }
  }
}

export function legendFor(model, mode) {
  switch (mode) {
    case "zustand":
      return TIDY_RAMP.map((c, i) => ({
        color: c,
        label: ["unberührt", "1 von 4", "2 von 4", "3 von 4", "eingeordnet"][i],
      }));
    case "kanal":
      return model.channels.map((c) => ({ color: CHANNEL_COLOR[c] || "#8a8a8a", label: c }));
    case "jahr":
      return [2005, 2012, 2018, 2024].map((y) => ({
        color: `hsl(${(205 - ((y - 2002) / 24) * 195).toFixed(0)} 58% 58%)`,
        label: String(y),
      }));
    default:
      return [];
  }
}

/* ---- Filter -----------------------------------------------------------
   Ein Uint8Array statt einer Liste: die Zeichenschleife laeuft ohnehin ueber
   alle Punkte und darf nicht in einem Set nachschlagen muessen. */

export const FILTERS = [
  { id: "fold", label: "Stapel falten", hint: "Nahduplikate zu einem Bild zusammenfassen" },
  { id: "open", label: "nur Ungeordnete", hint: "ohne Person, ohne Beschreibung, ohne Serie" },
  { id: "camera", label: "nur eigene Aufnahmen", hint: "kein WhatsApp, keine Screenshots" },
];

export function visibleMask(model, filters) {
  const mask = new Uint8Array(model.n).fill(1);
  const camIndex = model.channels.indexOf("camera");
  for (let i = 0; i < model.n; i++) {
    const f = model.fl[i];
    const buried = filters.fold && f & FLAG.IN_STACK && !(f & FLAG.STACK_HEAD);
    const tidy = filters.open && tidiness(f) > 0;
    const foreign = filters.camera && model.ch[i] !== camIndex;
    if (buried || tidy || foreign) mask[i] = 0;
  }
  return mask;
}

/** Alle sichtbaren Fotos, auf denen diese Person bestaetigt ist.

    Identitaet steckt im Gesichtsvektor, nicht in CLIP -- die Karte ordnet
    nach Aussehen, und ein Spiegelselfie sieht wie ein Spiegelselfie aus,
    egal wer davorsteht. Wer wissen will, wo *eine Person* liegt, muss sie
    deshalb eigens einfaerben koennen. */
export function photosOfPerson(model, personIndex, mask) {
  const hit = new Set();
  for (let i = 0; i < model.n; i++) {
    if (mask[i] && model.pe[i]?.includes(personIndex)) hit.add(i);
  }
  return hit;
}

/** Wer ist auf diesem Foto bestaetigt? */
export function personNames(model, i) {
  return (model.pe[i] || []).map((k) => model.persons[k]);
}

/** Alle sichtbaren Fotos einer Serie, in ihrer Reihenfolge auf der Zeitachse. */
export function photosOfEvent(model, eventIndex, mask) {
  const out = [];
  for (let i = 0; i < model.n; i++) if (model.ev[i] === eventIndex && (!mask || mask[i])) out.push(i);
  out.sort((a, b) => model.t[a] - model.t[b]);
  return out;
}

export function countVisible(mask) {
  let n = 0;
  for (let i = 0; i < mask.length; i++) n += mask[i];
  return n;
}
