/* Die Leinwand: Kamera, Zeichnen, Maus.

   17 370 Punkte sind fuer Canvas 2D unkritisch -- teuer waere nur, fuer jeden
   Punkt `fillStyle` neu zu setzen. Die Punkte werden deshalb einmal je
   Farbmodus nach Farbe sortiert und dann in Gruppen gezeichnet.

   Fotos erscheinen erst beim Hineinzoomen. In der Uebersicht waeren 17 000
   Bilder à 6 px ohnehin Matsch; sichtbar bleibt dort ein Leitbild je
   Kontinent. */

import { colorFor, spreadPoint } from "./model.js?v=25";
import { thumbUrl } from "../core/api.js?v=25";

//: Ab dieser Vergroesserung lohnen echte Fotos statt Punkte.
const THUMB_SCALE = 2600;

//: Die Zoomgrenzen. Auch die Kachelgroesse braucht sie -- sie spannen den
//: Bereich auf, ueber den sie mitwaechst.
const MIN_SCALE = 120;
const MAX_SCALE = 90000;

/* Wie gross eine Kachel bei welchem Zoom ist.

   Bisher wuchs sie mit dem Massstab, war aber bei 96 px gedeckelt -- ab
   Massstab 8640 blieb sie stehen, waehrend alles andere weiter
   auseinanderging. Ganz nah waren die Bilder dadurch zu klein fuer das, was
   man dort tut (eines ansehen), und knapp ueber der Bildschwelle zu gross
   fuer das, was man dort tut (sich einen Ueberblick verschaffen).

   Also folgt sie jetzt dem Zoom ueber die ganze Strecke, logarithmisch --
   Zoomen ist multiplikativ, ein linearer Verlauf saehe unten wie ein Sprung
   und oben wie Stillstand aus. Die Endpunkte sind abgelesen, nicht
   hergeleitet: 0,4 gibt gerade oberhalb der Bildschwelle den besseren
   Ueberblick, 1,3 passt ganz nah. Der Regler bleibt und multipliziert das --
   er ist damit eine Korrektur, keine Absolutangabe. */
const TILE_FAR = 0.4;
const TILE_NEAR = 1.3;
//: Obergrenze fuer gezeichnete Bilder je Bild. Kein Richtwert, sondern eine
//: Reissleine: was tatsaechlich gezeichnet wird, ergibt sich aus der Flaeche
//: des Fensters geteilt durch den Kachelabstand. Ein festes Budget war der
//: Grund, warum bei dichten Gegenden nur ein Kegel um die Bildmitte Bilder
//: zeigte und der Rand Punkte blieb -- die Liste ist von der Mitte nach
//: aussen sortiert, und nach 420 geladenen Bildern brach sie ab.
const MAX_THUMBS = 2400;

//: Unter so vielen lohnt kein Rastern -- so viele passen immer.
const MIN_THUMBS = 120;

//: Kachelabstand als Vielfaches der Kachelbreite, wenn "Entzerren" aus ist.
//: Ein Viertel heisst: die Bilder liegen dicht uebereinander, wie vorher --
//: aber ein Haufen in der Bildmitte kann nicht mehr das ganze Budget
//: aufbrauchen und den Rand als Punkte stehen lassen.
const SPACING = 0.25;

//: Durchgaenge beim Auseinanderschieben der Kontinente. Es sind 40
//: Kreise, also 780 Paare -- das kostet nichts und wird ohnehin nur
//: gerechnet, wenn sich der Regler bewegt.
const SPREAD_ITERATIONS = 60;

//: Etwas Luft zwischen zwei Kontinenten, sonst beruehren sie sich nur.
const SPREAD_AIR = 1.15;

//: Durchgaenge beim Auseinanderschieben. Sechs reichen fuer einen dichten
//: Haufen; danach bewegt sich kaum noch etwas, und jeder weitere kostet.
const FAN_ITERATIONS = 6;

/* Wie weit ein Bild beim Auffaechern hoechstens von seinem Platz wegdarf --
   gemessen in Weltkoordinaten, nicht in Pixeln. Darin steckt die ganze Idee.

   Beim Hineinzoomen entsteht Platz: derselbe Weltabstand ist auf dem Schirm
   doppelt so gross, wenn man doppelt so nah dran ist. Diesen Platz soll das
   Auffaechern nutzen -- aber nur ihn. Eine Grenze in Pixeln wuerde in der
   Uebersicht Kontinente verschieben und beim Hineinzoomen nichts mehr
   hergeben. In Weltkoordinaten ist es umgekehrt und richtig herum:

     Uebersicht (Massstab ~900)   0,004 * 900   =  3,6 px  -- unsichtbar
     nah dran   (Massstab 20000)  0,004 * 20000 =   80 px  -- echte Luft
     ganz nah   (Massstab 90000)  0,004 * 90000 =  360 px  -- ein Haufen
                                                              faechert auf

   Die Karte behauptet also nie eine Lage, die das grosse Ganze verfaelscht:
   der Fehler ist immer derselbe kleine Weltabstand, egal wie es aussieht.
   Und Fotos, die auf exakt derselben Stelle liegen -- Beinahe-Dubletten --
   trennen sich erst dann, wenn man wirklich hinsieht. */
const SHIFT_WORLD = 0.004;

//: Unter so vielen Pixeln lohnt das Auffaechern nicht -- es kostet sechs
//: Durchgaenge und bewegt nichts, was man sehen koennte.
const FAN_MIN_PX = 2;

/* Wieviele Nachbarn ein Bild je Durchgang hoechstens wegschiebt.

   Ohne diese Grenze ist das Verfahren quadratisch: vor dem ersten Durchgang
   liegen in einem dichten Haufen hunderte Punkte in *einem* Rasterfeld, und
   jeder wird gegen jeden geprueft. Gemessen: 123 ms je Bild, also acht Bilder
   je Sekunde beim Schwenken.

   Ein Deckel kostet fast nichts an Qualitaet, weil ueber sechs Durchgaenge
   ohnehin jeder mit jedem in Beruehrung kommt -- der Haufen dehnt sich nur
   etwas langsamer aus. Und er wirkt genau dort, wo er muss: in duennen
   Gegenden hat kein Punkt so viele Nachbarn. */
const FAN_NEIGHBOURS = 12;

/* Wieviel Zeit ein Bild kosten darf, und wie die Karte das selbst herausfindet.

   2.400 Vorschaubilder zu zeichnen kostete gemessen 140 ms -- acht Bilder je
   Sekunde beim Schwenken. Nicht weil `drawImage` langsam waere: 2.400 Aufrufe
   mit *demselben* Bild dauern 1,7 ms. Es sind die 2.400 *verschiedenen*
   Texturen, die dabei durch die Grafikkarte muessen.

   Eine feste Obergrenze waere wieder geraten -- auf einer schnellen Maschine
   zu niedrig, auf einer langsamen zu hoch. Also misst die Karte, was ein Bild
   bei ihr kostet, und leitet daraus ab, wieviele in ein ruhiges Bild passen.
   Die Zahl steht in der Fusszeile; wer sie ueberschreiten will, waehlt
   "Alles". */
const FRAME_BUDGET_MS = 12;

//: Startwert, bis gemessen wurde. Wird nach dem ersten Durchlauf ersetzt.
const MS_PER_THUMB_START = 0.03;

/* Wie traege der Messwert nachzieht -- und warum unterschiedlich.

   Symmetrisch geglaettet schwingt die Regelung: misst ein Durchlauf
   zufaellig 4 µs je Bild, springt das Budget auf 2.400, der naechste
   Durchlauf kostet 25 ms, das Budget faellt auf 800, dort misst es wieder
   billig, und so fort. Beobachtet als Springen zwischen 783 und 2.400.

   Also schnell reagieren, wenn es teurer wird -- da geht es um das ruhige
   Bild -- und langsam, wenn es billiger wird. Dann pendelt sich die Karte
   ein, statt zwischen zwei Zustaenden zu wechseln. */
const MS_RISE = 0.4;
const MS_FALL = 0.05;

//: Reissleine gegen Ausreisser: mehr als das kostet ein Vorschaubild nie,
//: und ein einzelner Messfehler soll die Karte nicht leerraeumen.
const MS_PER_THUMB_MAX = 0.15;

//: Rasterweite fuer die Schaetzung der belegten Flaeche. Grob genug, dass
//: ein Haufen als zusammenhaengende Flaeche zaehlt, fein genug, dass leere
//: Gegenden nicht mitgerechnet werden.
const COARSE = 48;
/* Gleichzeitige Abrufe.

   Der Engpass sitzt nicht hier, sondern im Server: ein Vorschaubild, das es
   noch nicht gibt, wird aus dem Original auf der Platte gerechnet, und das
   dauert rund 190 ms. Gemessen saettigt er bei etwa 55 Bildern je Sekunde --
   mit 8 gleichzeitigen sind es 48, mit 24 dann 59, mit 48 wieder 54. Also
   16: der Rest ist Warten auf dieselbe Warteschlange. */
const MAX_INFLIGHT = 16;

//: Nach so langer Zeit gilt eine Anfrage als verloren. Grosszuegig: sie soll
//: nur verhindern, dass ein haengender Abruf einen der acht Plaetze fuer
//: immer belegt, nicht langsame Abrufe abwuergen.
const LOAD_TIMEOUT_MS = 20000;

//: So oft darf ein Bild scheitern, bevor es endgueltig uebersprungen wird.
const MAX_TRIES = 2;
const TRANSITION_MS = 700;

export function createScene(canvas, model, hooks = {}) {
  const ctx = canvas.getContext("2d", { alpha: false });
  const cam = { scale: 1, tx: 0, ty: 0 };

  let layout = "bedeutung";
  let colorMode = "kontinent";
  let mode = "fotos";       // "fotos" | "serien"
  let minEventSize = 3;
  // Regler, die direkt aufs Zeichnen wirken. Keine Messung, Geschmack --
  // deshalb einstellbar und nicht von mir festgelegt.
  let spread = 0;      // Kontinente auseinanderziehen
  let tileScale = 1;   // Kachelgroesse
  //: Mindestabstand gezeichneter Bilder, als Vielfaches der Kachelbreite.
  //: Nicht in Pixeln: eine Kachel ist beim Herauszoomen 22 px breit und beim
  //: Hineinzoomen 96, und "60 px Abstand" heisst dann einmal viel Luft und
  //: einmal immer noch Ueberlappung. Bei 1.0 stossen die Bilder aneinander,
  //: darunter ueberlappen sie, darueber steht Luft dazwischen -- und das
  //: sieht man auf jeder Zoomstufe.
  let declutter = 0;
  //: Wie das Budget fuer Vorschaubilder verteilt wird. Kein Automatismus,
  //: sondern eine Wahl -- die Punktansicht zeigt Farbgruppen besser als
  //: jedes Bild, der Kegel taugt zum Verfolgen einer Spur, die Flaeche zum
  //: Ueberblick. Was davon richtig ist, haengt an der Frage, nicht am Code.
  let thumbMode = "flaeche";
  //: Was der letzte Durchlauf gekostet hat. Ohne Zahlen ist "ruckelt es?"
  //: Geschmackssache -- und die Antwort haengt am Geraet, nicht an meiner
  //: Meinung.
  const stats = { visible: 0, drawn: 0, budget: 0, raster: false, gap: 0, shift: 0,
                  ms: 0, fanMs: 0, imgMs: 0, cached: 0, off: "", gapReason: "",
                  tile: 0, tileAuto: 1,
                  perThumb: MS_PER_THUMB_START };

  //: Gemessene Kosten je gezeichnetem Bild. Siehe FRAME_BUDGET_MS.
  let msPerThumb = MS_PER_THUMB_START;

  /* Wo im letzten Durchlauf tatsaechlich ein Bild lag: [index, x, y, w, h].

     Beim Auffaechern steht ein Bild nicht mehr auf seinem Punkt. Wer dann
     mit der echten Lage sucht, trifft beim Klick auf ein Bild ein anderes
     oder gar nichts -- die Karte reagierte dann nachweislich falsch auf
     das, was zu sehen ist. Also wird gemerkt, was wo gezeichnet wurde. */
  let shown = [];

  /* Was dieser Durchlauf zeichnen wird: [index, sx, sy, img].

     `covered` merkt sich dieselben Indizes als Flaggen, damit die
     Punktschleife in einem Schritt entscheiden kann, ob ein Foto schon als
     Bild zu sehen ist. `touched` sammelt, was zurueckzusetzen ist -- ein
     Array von 17.000 Bytes je Bild zu leeren waere Verschwendung. */
  let planned = [];
  const covered = new Uint8Array(model.n);
  let touched = [];

  let mask = new Uint8Array(model.n).fill(1);
  //: Zaehlt jede Aenderung der Sichtbarkeit mit. Die Platzvergabe haelt nur
  //: so lange still, wie sich am Bestand nichts aendert -- ein neuer Filter
  //: muss sie also verwerfen duerfen.
  let maskVersion = 0;

  /* Die gehaltene Zuteilung: welches Foto liegt gerade wo.

     Kein Zwischenspeicher fuer Geschwindigkeit, sondern fuer Ruhe im Bild.
     Solange Kamera, Abstand und Filter gleich bleiben, behaelt jedes Foto
     seinen Platz, und nachladende Bilder fuellen nur Luecken. */
  const held = [];              // [index, sx, sy] in Zeichenreihenfolge
  const heldSet = new Set();    // dieselben Indizes, zum Nachschlagen
  let heldKey = "";             // Kamerazustand, zu dem die Zuteilung passt
  let heldGap = 0;              // eingefrorener Abstand dazu
  let heldBudget = 0;           // eingefrorene Bildzahl dazu
  let tileBox = 22;             // Kachelbreite des letzten Plans
  let selection = null; // Set<number> oder null
  let lassoPath = null;

  // Anordnungswechsel: alte und neue Position, dazwischen wird gemischt.
  let fromX = model.layouts.bedeutung.x, fromY = model.layouts.bedeutung.y;
  let toX = fromX, toY = fromY;
  let mix = 1, mixStart = 0;

  const px = new Float32Array(model.n);
  const py = new Float32Array(model.n);
  let buckets = [];

  //: Schluessel -> Bild, `false` (endgueltig gescheitert) oder `null`
  //: (angefragt). `null` heisst nicht "kommt gleich": es heisst nur, dass
  //: niemand es nochmal anfragen soll, solange es unterwegs ist.
  const images = new Map();
  let inflight = 0;
  const queue = [];
  //: Schluessel, die in `queue` warten und noch nicht gestartet sind. Nur
  //: die duerfen beim Neupriorisieren verworfen werden.
  const queued = new Set();
  //: Fehlversuche je Schluessel.
  const attempts = new Map();

  /* ---- Kamera ---------------------------------------------------------- */

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Entsteht die Leinwand, waehrend der Tab verborgen ist, meldet sie 0 x 0.
    // `fitAll` setzte dann Massstab 0 und die ganze Karte faellt auf einen
    // Punkt zusammen. Sobald sie eine Groesse hat, einmal neu einpassen.
    const wasEmpty = !canvas._w;
    canvas._w = r.width;
    canvas._h = r.height;
    if (wasEmpty) fitAll();
  }

  function fitAll() {
    const s = Math.min(canvas._w || 0, canvas._h || 0) * 0.92 || 1;
    cam.scale = s;
    cam.tx = (canvas._w - s) / 2;
    cam.ty = (canvas._h - s) / 2;
  }

  const toScreenX = (wx) => wx * cam.scale + cam.tx;
  const toScreenY = (wy) => wy * cam.scale + cam.ty;
  const toWorldX = (sx) => (sx - cam.tx) / cam.scale;
  const toWorldY = (sy) => (sy - cam.ty) / cam.scale;

  /** Der Zoomanteil der Kachelgroesse: 0,4 an der Bildschwelle, 1,3 ganz nah. */
  function autoTile() {
    const span = Math.log(MAX_SCALE / THUMB_SCALE);
    const t = Math.max(0, Math.min(1, Math.log(cam.scale / THUMB_SCALE) / span));
    return TILE_FAR + (TILE_NEAR - TILE_FAR) * t;
  }

  function zoomAt(sx, sy, factor) {
    const wx = toWorldX(sx), wy = toWorldY(sy);
    cam.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, cam.scale * factor));
    cam.tx = sx - wx * cam.scale;
    cam.ty = sy - wy * cam.scale;
    draw();
  }

  /** Auf einen Kontinent zufahren -- weich, damit der Bezug nicht abreisst. */
  function flyTo(wx, wy, scale) {
    const from = { ...cam };
    const t0 = performance.now();
    const target = { scale, tx: canvas._w / 2 - wx * scale, ty: canvas._h / 2 - wy * scale };
    (function step(now) {
      const p = Math.min(1, (now - t0) / 520);
      const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      cam.scale = from.scale + (target.scale - from.scale) * e;
      cam.tx = from.tx + (target.tx - from.tx) * e;
      cam.ty = from.ty + (target.ty - from.ty) * e;
      draw();
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  /** Auf eine Menge Punkte zufahren, so dass alle hineinpassen. */
  function flyToSet(indices) {
    if (!indices.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const i of indices) {
      x0 = Math.min(x0, px[i]); y0 = Math.min(y0, py[i]);
      x1 = Math.max(x1, px[i]); y1 = Math.max(y1, py[i]);
    }
    // Etwas Luft, und eine Untergrenze: liegen alle Treffer praktisch
    // aufeinander, soll die Kamera nicht ins Unendliche zoomen.
    const w = Math.max(x1 - x0, 0.012) * 1.35;
    const h = Math.max(y1 - y0, 0.012) * 1.35;
    const scale = Math.min(canvas._w / w, canvas._h / h);
    flyTo((x0 + x1) / 2, (y0 + y1) / 2, Math.min(scale, 26000));
  }

  /* ---- Farbgruppen ----------------------------------------------------- */

  function rebuildBuckets() {
    const byColor = new Map();
    for (let i = 0; i < model.n; i++) {
      const c = colorFor(model, i, colorMode);
      let list = byColor.get(c);
      if (!list) byColor.set(c, (list = []));
      list.push(i);
    }
    buckets = [...byColor.entries()].map(([color, list]) => ({
      color,
      idx: Int32Array.from(list),
    }));
  }

  /* ---- Bilder ---------------------------------------------------------- */

  function imageForId(id, size) {
    const key = `${id}:${size}`;
    const hit = images.get(key);
    if (hit !== undefined) return hit;
    images.set(key, null);
    queued.add(key);
    queue.push({ key, id, size });
    pump();
    return null;
  }

  const imageFor = (i, size) => imageForId(model.ids[i], size);

  /** Nachsehen, ohne anzufordern.

      Der erste Durchgang beim Verteilen darf keine Ladevorgaenge ausloesen:
      er soll nur wissen, was schon da ist, damit Geladenes seinen Platz
      behaelt. Wuerde er anfordern, waere jeder Durchlauf wieder eine Welle
      von Anfragen -- gemessen 4.809 bei einem einzigen Hineinzoomen. */
  function peekImage(id, size) {
    const hit = images.get(`${id}:${size}`);
    return hit || null;
  }

  //: Vorschaubildstufen, gross zuerst. Was schon da ist, ist besser als nichts.
  const SIZES = [320, 160];

  /** Das beste schon geladene Bild, und die gewuenschte Stufe anfordern.

      Beim Hineinzoomen wechselt die Stufe von 160 auf 320 -- und damit der
      Schluessel im Zwischenspeicher. Ohne Rueckfall war in dem Moment *jedes*
      sichtbare Bild ungeladen, und die Karte fiel auf Punkte zurueck, bis
      alles neu geholt war. Genau das sah aus wie "zu nah rangezoomt, Bilder
      weg". Jetzt bleibt die grobe Stufe stehen und wird scharf, sobald die
      feine da ist. */
  function bestImage(i, size) {
    const id = model.ids[i];
    const wanted = imageForId(id, size);
    if (wanted) return wanted;
    for (const other of SIZES) {
      if (other === size) continue;
      const hit = peekImage(id, other);
      if (hit) return hit;
    }
    return null;
  }

  /** Wie `bestImage`, aber ohne anzufordern -- fuer den ersten Durchgang. */
  function peekBest(i, size) {
    const id = model.ids[i];
    for (const s of [size, ...SIZES.filter((o) => o !== size)]) {
      const hit = peekImage(id, s);
      if (hit) return hit;
    }
    return null;
  }

  /* Die Warteschlange gilt fuer *dieses* Bild, nicht fuer die Sitzung.

     Vorher war sie ein Rueckstau: jeder Durchlauf haengte an, was er gerade
     brauchte, und nichts wurde je entfernt. Nach ein paar Schwenks standen
     11.615 Eintraege darin, von denen 231 wirklich angefragt worden waren --
     alles Weitere wartete hinter Fotos, die laengst aus dem Bild geschoben
     waren. Wer zur Seite schob, sah dort deshalb nur Punkte, und zwar
     dauerhaft: die neu sichtbaren Bilder standen ganz hinten an.

     Also wird vor jedem Durchlauf geleert, was noch nicht gestartet ist.
     Was laeuft, laeuft weiter; was gebraucht wird, stellt sich neu an -- und
     zwar wieder von der Bildmitte nach aussen. Die Schlange ist damit nie
     laenger als ein Bildschirm voll. */
  function resetQueue() {
    for (const k of queued) images.delete(k);
    queue.length = 0;
    queued.clear();
  }

  function pump() {
    while (inflight < MAX_INFLIGHT && queue.length) {
      // Der Reihe nach, nicht zuletzt-zuerst. Angefragt wird von der
      // Bildmitte nach aussen; bei eingeschalteter Entzerrung sind die
      // mittleren die *einzigen*, die gezeichnet werden. Auf einem Stapel
      // laegen sie ganz unten und die Karte bliebe minutenlang leer.
      const job = queue.shift();
      queued.delete(job.key);
      inflight++;
      const img = new Image();
      img.decoding = "async";
      let done = false;
      const finish = (value) => {
        if (done) return;
        done = true;
        inflight--;
        if (value) {
          images.set(job.key, value);
          schedule();
        } else {
          // Zweimal darf es schiefgehen -- ein Aussetzer soll ein Foto nicht
          // fuer den Rest der Sitzung schwaerzen.
          const tries = (attempts.get(job.key) || 0) + 1;
          attempts.set(job.key, tries);
          if (tries >= MAX_TRIES) images.set(job.key, false);
          else images.delete(job.key);
        }
        pump();
      };
      img.onload = () => finish(img);
      img.onerror = () => finish(false);
      // Sicherung: eine Anfrage, die weder ankommt noch scheitert, wuerde
      // sonst einen der acht Plaetze dauerhaft belegen -- und nach acht
      // solchen Faellen laedt die Karte nie wieder etwas nach.
      setTimeout(() => finish(false), LOAD_TIMEOUT_MS);
      img.src = thumbUrl(job.id, job.size);
    }
  }

  /* ---- Zeichnen -------------------------------------------------------- */

  let pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; draw(); });
  }

  function positions(now) {
    if (mix < 1) {
      const p = Math.min(1, (now - mixStart) / TRANSITION_MS);
      mix = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      if (p >= 1) mix = 1;
    }
    if (mix >= 1) {
      px.set(toX);
      py.set(toY);
      applySpread();
      return false;
    }
    for (let i = 0; i < model.n; i++) {
      px[i] = fromX[i] + (toX[i] - fromX[i]) * mix;
      py[i] = fromY[i] + (toY[i] - fromY[i]) * mix;
    }
    applySpread();
    return true;
  }

  /* Kontinente wirklich auseinander -- nicht nur zusammenziehen.

     `spreadPoint` zieht jeden Punkt zu seinem Kontinent-Mittelpunkt hin:
     (base + k*centroid) / (1+k). Die Kontinente werden dadurch kompakter,
     aber ihre Mittelpunkte bleiben, wo sie sind. Zwei Kontinente, die
     ineinanderliegen, liegen danach genauso ineinander -- nur dichter. Sie
     gehen also nicht auseinander, sie fahren durcheinander durch.

     Was fehlt, ist derselbe Gedanke wie bei den Bildern: die Mittelpunkte
     muessen einander abstossen. Jeder Kontinent bekommt einen Radius, und
     dann werden die Kreise so lange auseinandergeschoben, bis sie sich nicht
     mehr ueberdecken. Danach wird alles zurueck ins Bild gestaucht.

     Gerechnet wird das nur, wenn sich Regler oder Anordnung aendern -- nicht
     je Bild. */
  let spreadKey = "";
  let spreadDx = null;
  let spreadDy = null;

  function clusterRadii(cc) {
    // Wie weit ein Kontinent reicht: quadratisches Mittel der Abstaende
    // seiner Fotos vom eigenen Mittelpunkt. Robuster als das Maximum, das
    // ein einzelner Ausreisser sonst aufblaeht.
    const k = cc.length / 2;
    const sum = new Float64Array(k);
    const cnt = new Float64Array(k);
    for (let i = 0; i < model.n; i++) {
      const c = model.cl[i];
      const dx = toX[i] - cc[c * 2], dy = toY[i] - cc[c * 2 + 1];
      sum[c] += dx * dx + dy * dy;
      cnt[c]++;
    }
    const r = new Float64Array(k);
    for (let c = 0; c < k; c++) r[c] = cnt[c] ? Math.sqrt(sum[c] / cnt[c]) : 0;
    return r;
  }

  function buildSpread(strength) {
    const cc = model.layouts[layout].centroids;
    const k = cc.length / 2;
    const rad = clusterRadii(cc);
    // Nach dem Zusammenziehen ist jeder Kontinent um denselben Faktor
    // kleiner -- die Kreise, die einander ausweichen muessen, also auch.
    const shrink = 1 / (1 + strength);
    const x = new Float64Array(k), y = new Float64Array(k);
    for (let c = 0; c < k; c++) { x[c] = cc[c * 2]; y[c] = cc[c * 2 + 1]; }

    for (let it = 0; it < SPREAD_ITERATIONS; it++) {
      let moved = 0;
      for (let a = 0; a < k; a++) {
        for (let b = a + 1; b < k; b++) {
          const want = (rad[a] + rad[b]) * shrink * SPREAD_AIR;
          let dx = x[b] - x[a], dy = y[b] - y[a];
          let d2 = dx * dx + dy * dy;
          if (d2 >= want * want) continue;
          if (d2 < 1e-12) { const ang = a * 2.399963; dx = Math.cos(ang); dy = Math.sin(ang); d2 = 1; }
          const d = Math.sqrt(d2);
          // Der kleinere Kontinent weicht mehr aus: sonst schiebt ein
          // Haeufchen von zwoelf Fotos einen mit dreitausend beiseite.
          const wa = rad[b] / (rad[a] + rad[b] || 1);
          const push = (want - d);
          const ux = dx / d, uy = dy / d;
          x[a] -= ux * push * wa; y[a] -= uy * push * wa;
          x[b] += ux * push * (1 - wa); y[b] += uy * push * (1 - wa);
          moved++;
        }
      }
      if (!moved) break;
    }

    // Zurueck ins Bild: das Auseinanderschieben macht die Karte groesser,
    // und niemand will danach erst herauszoomen.
    let lo = Infinity, hi = -Infinity;
    for (let c = 0; c < k; c++) {
      lo = Math.min(lo, x[c] - rad[c] * shrink, y[c] - rad[c] * shrink);
      hi = Math.max(hi, x[c] + rad[c] * shrink, y[c] + rad[c] * shrink);
    }
    const span = Math.max(hi - lo, 1e-6);
    spreadDx = new Float32Array(k);
    spreadDy = new Float32Array(k);
    for (let c = 0; c < k; c++) {
      // Verschiebung *und* Stauchung in einem: wo der Mittelpunkt nach dem
      // Zurueckstauchen liegt, minus wo er vorher lag.
      spreadDx[c] = (x[c] - lo) / span - cc[c * 2];
      spreadDy[c] = (y[c] - lo) / span - cc[c * 2 + 1];
    }
  }

  /** Sicherstellen, dass die Verschiebung zur aktuellen Einstellung passt. */
  function ensureSpread() {
    const key = `${layout}|${spread}`;
    if (key !== spreadKey) { spreadKey = key; buildSpread(spread); }
  }

  /* Die Weltlage eines Punktes mit Kontinentverschiebung.

     Auch Beschriftungen und Trefferpruefung muessen hier durch. Sonst
     zeichnet die Karte die Kontinente an einer Stelle und ihre Namen an
     einer anderen -- beobachtet als "linke Kontinente ohne Beschriftung",
     weil deren Namen bei den unverschobenen Mittelpunkten zusammenstanden. */
  function spreadAt(bx, by, c) {
    if (!spread) return [bx, by];
    ensureSpread();
    return [spreadPoint(bx, model.layouts[layout].centroids[c * 2], spread) + spreadDx[c],
            spreadPoint(by, model.layouts[layout].centroids[c * 2 + 1], spread) + spreadDy[c]];
  }

  function applySpread() {
    if (!spread) return;
    ensureSpread();
    const cc = model.layouts[layout].centroids;
    for (let i = 0; i < model.n; i++) {
      const c = model.cl[i];
      px[i] = spreadPoint(px[i], cc[c * 2], spread) + spreadDx[c];
      py[i] = spreadPoint(py[i], cc[c * 2 + 1], spread) + spreadDy[c];
    }
  }

  function draw() {
    const now = performance.now();
    const animating = positions(now);
    stats.drawn = 0;
    stats.budget = 0;
    stats.off = "";
    shown.length = 0;
    const w = canvas._w, h = canvas._h;
    ctx.fillStyle = "#12151a";
    ctx.fillRect(0, 0, w, h);

    if (layout === "zeit" && mix > 0.5) drawYearBands();

    const r = mode === "serien" ? 1 : Math.max(1.1, Math.min(7, cam.scale / 900));
    // `selection` ist null, solange nichts gewaehlt wurde. In der Serien-Ebene
    // treten die Punkte trotzdem zurueck -- dort sind sie Untergrund.
    const picked = selection && selection.size > 0;
    const dim = mode === "serien" || picked;
    // In der Punktansicht bleiben die Farbgruppen unverdeckt -- Kontinente
    // und Zustaende liest man daran besser ab als an Bildern.
    const showThumbs = thumbMode !== "punkte" && cam.scale > THUMB_SCALE && !animating;

    /* Erst planen, dann zeichnen.

       Ein Punkt, unter dem sein eigenes Bild liegt, ist Doppelung -- und
       beim Abstossen wird daraus ein Widerspruch: das Bild wandert, der
       Punkt bleibt, und beide zeigen dasselbe Foto. Also muss vor der
       Punktschleife feststehen, welche Fotos als Bild erscheinen. */
    if (mode !== "serien" && showThumbs) planThumbs(w, h);
    else { planned = []; for (const i of touched) covered[i] = 0; touched = []; }

    // Punkte
    for (const b of buckets) {
      ctx.fillStyle = dim ? fade(b.color) : b.color;
      const idx = b.idx;
      for (let k = 0; k < idx.length; k++) {
        const i = idx[k];
        if (!mask[i] || covered[i]) continue;
        if (picked && selection.has(i)) continue;
        const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
        if (sx < -8 || sy < -8 || sx > w + 8 || sy > h + 8) continue;
        ctx.fillRect(sx - r, sy - r, r * 2, r * 2);
      }
    }

    // Ausgewaehlte oben drauf, damit sie nicht verdeckt werden
    if (picked) {
      ctx.fillStyle = "#ffffff";
      for (const i of selection) {
        if (!mask[i] || covered[i]) continue;
        const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
        if (sx < -8 || sy < -8 || sx > w + 8 || sy > h + 8) continue;
        ctx.fillRect(sx - r - 0.5, sy - r - 0.5, r * 2 + 1, r * 2 + 1);
      }
    }

    if (mode === "serien") {
      drawEvents(w, h);
      stats.off = "Serien-Ebene";
      stats.visible = 0;
    } else if (showThumbs) {
      paintThumbs(picked);
    } else {
      // Kein Bild gezeichnet ist nicht dasselbe wie kein Bild gefunden --
      // "0 von 1.823" laese sich wie ein Fehler.
      stats.visible = 0;
      stats.off = thumbMode === "punkte" ? "Punktansicht"
                : animating ? "Übergang läuft"
                : "zu weit weg für Bilder";
    }
    drawClusterLabels(w, h);
    if (lassoPath) drawLasso();
    // Nur der eigene Aufwand, ohne das Warten aufs Bild -- was der Browser
    // danach mit der Leinwand macht, steht hier nicht drin.
    stats.ms = Math.round((performance.now() - now) * 10) / 10;
    if (animating) schedule();
  }

  function fade(color) {
    // Nicht-Ausgewaehltes zuruecknehmen, ohne die Farbe zu verlieren.
    return color.startsWith("hsl") ? color.replace(")", " / 0.22)") : color + "38";
  }

  function drawYearBands() {
    const { years, start, width } = model.layouts.zeit;
    ctx.save();
    ctx.font = "500 11px system-ui, sans-serif";
    ctx.textBaseline = "top";
    for (const y of years) {
      const x0 = toScreenX(start.get(y));
      const x1 = toScreenX(start.get(y) + width.get(y));
      if (x1 < 0 || x0 > canvas._w) continue;
      ctx.fillStyle = y % 2 ? "#171b21" : "#151920";
      ctx.fillRect(x0, 0, x1 - x0, canvas._h);
      if (x1 - x0 > 26) {
        ctx.fillStyle = "#5d6773";
        ctx.fillText(String(y), x0 + 4, 6);
      }
    }
    ctx.restore();
  }

  /* Einen Haufen aufmachen, statt ihn auszuduennen.

     In einem dichten Kontinent liegen hunderte Fotos auf wenigen Pixeln. Wer
     dort Abstand erzwingt, bekommt wenige Bilder zu sehen und ringsum leeren
     Bildschirm -- der Platz waere da, der Haufen nutzt ihn nur nicht. Also
     werden die Bilder auseinandergeschoben, bis sie den Mindestabstand
     einhalten: der Haufen blaeht sich auf und fuellt die freie Flaeche.

     Was dabei verloren geht, ist die genaue Lage. Der Punkt darunter bleibt
     aber liegen, wo er hingehoert -- die Verschiebung ist also sichtbar und
     nicht behauptet. Und sie ist begrenzt: weiter als `LIMIT` Kachelbreiten
     wandert kein Bild von seinem Platz weg.

     Deterministisch, damit es beim Schwenken nicht zappelt: gleiche Kamera,
     gleiche Reihenfolge, gleiches Ergebnis. */
  function fanOut(cand, minDist, w, h, limit) {
    const md2 = minDist * minDist;
    const n = cand.length;
    if (n < 2) return;
    // Ausgangslage merken, um die Verschiebung begrenzen zu koennen.
    const ox = new Float32Array(n), oy = new Float32Array(n);
    for (let a = 0; a < n; a++) { ox[a] = cand[a][2]; oy[a] = cand[a][3]; }

    for (let it = 0; it < FAN_ITERATIONS; it++) {
      // Das Raster muss je Durchgang neu entstehen -- die Punkte sind ja
      // gerade umgezogen.
      const cells = new Map();
      for (let a = 0; a < n; a++) {
        const key = Math.floor(cand[a][2] / minDist) + ":" + Math.floor(cand[a][3] / minDist);
        const list = cells.get(key);
        if (list) list.push(a); else cells.set(key, [a]);
      }
      let moved = 0;
      for (let a = 0; a < n; a++) {
        const gx = Math.floor(cand[a][2] / minDist), gy = Math.floor(cand[a][3] / minDist);
        // Gezaehlt wird jeder *angesehene* Nachbar, nicht jeder verschobene.
        // Zaehlt man erst hinter dem Ueberspringen, laeuft der letzte Punkt
        // eines vollen Rasterfelds trotzdem durch die ganze Liste -- und
        // genau daran hingen die gemessenen 122 ms.
        let seen = 0;
        let ax = cand[a][2], ay = cand[a][3];
        for (let dx = -1; dx <= 1 && seen < FAN_NEIGHBOURS; dx++) {
          for (let dy = -1; dy <= 1 && seen < FAN_NEIGHBOURS; dy++) {
            const list = cells.get((gx + dx) + ":" + (gy + dy));
            if (!list) continue;
            for (const b of list) {
              if (seen >= FAN_NEIGHBOURS) break;
              if (b === a) continue;
              seen++;
              let ddx = cand[b][2] - ax, ddy = cand[b][3] - ay;
              let d2 = ddx * ddx + ddy * ddy;
              if (d2 >= md2) continue;
              if (d2 < 1e-6) {
                // Exakt uebereinander: ohne Richtung gibt es keinen Schub.
                // Der Index als Winkel ist willkuerlich, aber immer derselbe.
                const ang = (a * 2.399963) % 6.283185;
                ddx = Math.cos(ang); ddy = Math.sin(ang); d2 = 1;
              }
              const d = Math.sqrt(d2);
              // Nur der eigene Punkt weicht aus. Weil das jeder tut, ist die
              // Bewegung trotzdem gegenseitig -- und die Reihenfolge spielt
              // keine Rolle mehr, sodass der Deckel nicht die einen
              // bevorzugt und die anderen uebergeht.
              const push = (minDist - d) / 2;
              ax -= (ddx / d) * push;
              ay -= (ddy / d) * push;
              moved++;
            }
          }
        }
        cand[a][2] = ax; cand[a][3] = ay;
      }
      if (!moved) break;
    }

    for (let a = 0; a < n; a++) {
      // Nicht weiter als erlaubt vom eigenen Platz weg ...
      const dx = cand[a][2] - ox[a], dy = cand[a][3] - oy[a];
      const d = Math.hypot(dx, dy);
      if (d > limit) {
        cand[a][2] = ox[a] + (dx / d) * limit;
        cand[a][3] = oy[a] + (dy / d) * limit;
      }
      // ... und nicht aus dem Fenster heraus, sonst schiebt sich der Rand
      // eines Haufens ins Nichts.
      cand[a][2] = Math.max(-20, Math.min(w + 20, cand[a][2]));
      cand[a][3] = Math.max(-20, Math.min(h + 20, cand[a][3]));
    }
  }

  /* Vorschaubilder verteilen.

     Drei Fragen, die vorher stillschweigend beantwortet waren:

     Wie viele?   Nicht 420, sondern so viele, wie bei diesem Kachelabstand
                  ins Fenster passen. Ein fester Wert war auf einem breiten
                  Monitor zu wenig und auf dem Handy zu viel.

     Welche?      Das entscheidet `thumbMode`. Der Kegel nimmt die von der
                  Bildmitte nach aussen -- eine Taschenlampe. Die Flaeche
                  haelt Abstand, damit die Bilder bis an den Rand reichen.

     Wo genau?    Gezeichnet wird an der eigenen Stelle, nie eingerastet.
                  Ein Bild, das an ein Raster springt, luegt ueber seine
                  Position, und die Position ist hier die Aussage. */
  function planThumbs(w, h) {
    const t0 = performance.now();
    let fanMs = 0;
    // Von der Bildmitte nach aussen: passen nicht alle hin, gewinnt das
    // Naheliegende. Abstand einmal rechnen, nicht in jedem Vergleich.
    const cx = w / 2, cy = h / 2;
    const near = [];
    /* Wieviel Flaeche die sichtbaren Fotos tatsaechlich belegen.

       Nicht die Fensterflaeche: Fotos liegen in Haufen, und zwischen den
       Haufen ist leer. Rechnet man den Abstand aus der Fensterflaeche, kommt
       fuer ein kleines Haeufchen ein viel zu grosser Abstand heraus -- dann
       passen in den Haufen nur noch eine Handvoll Bilder, obwohl daneben
       alles frei ist. Gemessen: 52 gezeichnet von 4.014 sichtbaren.

       Grob genuegt: in Feldern von COARSE Pixeln zaehlen, wieviele belegt
       sind. Das kostet nichts, weil der Durchlauf ohnehin stattfindet. */
    const busy = new Set();
    for (let i = 0; i < model.n; i++) {
      if (!mask[i]) continue;
      const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
      if (sx < -60 || sy < -60 || sx > w + 60 || sy > h + 60) continue;
      near.push([(sx - cx) ** 2 + (sy - cy) ** 2, i, sx, sy]);
      busy.add(((sx / COARSE) | 0) * 4096 + ((sy / COARSE) | 0));
    }
    const filled = Math.max(COARSE * COARSE, busy.size * COARSE * COARSE);
    near.sort((a, b) => a[0] - b[0]);
    stats.visible = near.length;
/* Gemessen wird genau das Zeichnen, nichts sonst.

       Zwei Anlaeufe daneben, aus demselben Grund: alles ausser dem
       Zeichnen kostet gleich viel, ob danach zehn Bilder erscheinen oder
       tausend -- der Durchlauf ueber 17.000 Punkte, das Sortieren, und vor
       allem die Auswahl, die 4.000 Bewerber gegen das Raster prueft.
       Rechnet man davon irgendetwas mit, entsteht eine Spirale: kleineres
       Budget, gleicher Grundaufwand, also hoehere Kosten *je Bild*, also
       noch kleineres Budget. Beobachtet als 102 µs je Bild und ein Budget
       am Anschlag.

       Also die Uhr direkt um die drawImage-Aufrufe. Zwei Zeitabfragen je
       Bild kosten Bruchteile einer Mikrosekunde und messen dafuer das,
       worum es geht. */
    // Was noch nicht gestartet ist, gilt fuer dieses Bild neu.
    resetQueue();
    planned = [];
    for (const i of touched) covered[i] = 0;
    touched = [];

    const box = Math.max(22, Math.min(96, cam.scale / 90)) * autoTile() * tileScale;
    tileBox = box;
    stats.tile = Math.round(box);
    stats.tileAuto = Math.round(autoTile() * 100) / 100;

    /* Welche Vorschaubildstufe -- nach der gezeichneten Groesse, nicht nach
       dem Massstab.

       Vorher hing das am Zoom: ab Massstab 9000 wurden 320er geholt. Eine
       Kachel ist aber hoechstens 96 px breit, bei Standardgroesse also nie
       gross genug, um von 320 zu profitieren. Der Browser skalierte jedes
       Bild von 320 auf 96 herunter -- viermal so viele Quellpixel je Bild,
       bei jedem einzelnen Durchlauf. Gemessen: 112 ms fuer 2.400 Bilder,
       gegenueber rund 10 ms mit der kleinen Stufe. Dieselben 112 ms in jedem
       Modus, weshalb es zuerst wie ein Fehler im Auffaechern aussah.

       Jetzt entscheidet die Kachelbreite mal der Geraetepixel-Faktor: die
       feine Stufe kommt, wenn sie wirklich etwas beitraegt -- also bei
       vergroesserter Kachel. */
    const size = box * (window.devicePixelRatio || 1) > 160 ? 320 : 160;

    /* Abstand und Anzahl haengen zusammen, und in dieser Reihenfolge.

       Der Regler gibt den *gewuenschten* Abstand vor, als Vielfaches der
       Kachelbreite -- so wirkt er auf jeder Zoomstufe gleich. Steht er auf
       aus, greift ein Viertel: die Bilder liegen dann dicht uebereinander.

       Das allein reicht aber nicht. Zeichnen kann die Karte nur so viele
       Bilder, wie in ein ruhiges Bild passen -- gemessen, siehe unten. Sind
       das weniger, als bei diesem Abstand ins Fenster gehen, muessen sie
       *weiter* auseinander, sonst reichen sie nicht bis zum Rand. Genau das
       war der Fehler: 1.844 Bilder mit 16 px Abstand fuellen nur ein Viertel
       der 6.500 Rasterfelder, und weil von der Mitte nach aussen verteilt
       wird, blieb der Rand leer. Der Kegel war zurueck, nur groesser.

       Also andersherum gerechnet: um eine Flaeche mit N Bildern gleichmaessig
       zu belegen, braucht es die Wurzel aus Flaeche durch N als Abstand. Der
       groessere der beiden Wuensche gewinnt. */
    const affordable = Math.floor(FRAME_BUDGET_MS / Math.max(msPerThumb, 1e-4));
    // Nicht `planned` nennen: so heisst die Liste der vorgemerkten Bilder.
    const wish = Math.max(MIN_THUMBS, Math.min(MAX_THUMBS, affordable));
    const wanted = box * (declutter > 0 ? declutter : SPACING);
    const cover = Math.sqrt(filled / wish);

    /* Solange die Kamera steht, bleibt die Zuteilung stehen -- und dazu
       gehoert der Abstand. Er haengt am Budget, das Budget an der Messung,
       und die wandert bei jedem Bild ein Stueck. Rechnete man ihn jedes Mal
       neu, waere die Zuteilung schon deshalb bei jedem Bild hinfaellig, und
       die Bilder sprangen weiter. */
    const camKey = [cam.scale, cam.tx, cam.ty, thumbMode, declutter, tileScale,
                    layout, spread, mix < 1, maskVersion].join("|");
    if (camKey !== heldKey) {
      heldKey = camKey;
      held.length = 0;
      heldSet.clear();
      heldGap = Math.max(wanted, cover);
      // Auch die Zahl friert ein. Sie faellt, waehrend Bilder eintreffen --
      // mehr geladene Bilder heisst mehr Zeichenaufwand, heisst kleineres
      // Budget. Rechnete man weiter, verschwaenden Bilder, waehrend man sie
      // ansieht: beobachtet als Ruecklauf von 947 auf 666 bei stehender
      // Kamera. Neu geplant wird, sobald sich die Kamera bewegt -- und dann
      // mit einem Messwert, der die tatsaechlichen Kosten kennt.
      heldBudget = wish;
    }
    const gap = heldGap;
    const budget = heldBudget;
    const fits = Math.ceil((w + 2 * gap) / gap) * Math.ceil((h + 2 * gap) / gap);

    /* Was mit dem Abstand geschieht -- und das ist der Unterschied, um den
       es geht.

       Steht "Bilder entzerren" auf aus, bleibt jedes Foto an seiner Stelle.
       Passen nicht alle hin, wird ausgeduennt: das ist eine Auswahl, keine
       Entzerrung, und an der Ueberlappung der Uebriggebliebenen aendert sie
       nichts.

       Steht der Regler auf einem Wert, stossen die Bilder einander ab, bis
       der Abstand steht -- wie die Knoten in einem Graphen. Erst das ist
       Entzerren: es zeigt nicht weniger, es macht den Haufen auf.

       Der Platz dafuer kommt vom Hineinzoomen, und nur von dort. */
    const shift = SHIFT_WORLD * cam.scale;
    const repel = declutter > 0 && thumbMode !== "alles" && shift >= FAN_MIN_PX;
    //  punkte  -- kommt hier gar nicht an, draw() ueberspringt uns.
    //  kegel   -- kein Abstand, nur das Budget: die Mitte verbraucht es.
    //  flaeche -- ausduennen, sobald mehr sichtbar ist als passt.
    //  alles   -- weder noch, zum Vergleichen.
    const spaced = !repel && thumbMode === "flaeche" && near.length > budget;
    const capped = thumbMode !== "alles";

    /* Echter Mindestabstand, nicht ein Bild je Rasterfeld.

       Ein Feld allein zu pruefen garantiert keinen Abstand: zwei Punkte
       beiderseits einer Feldgrenze koennen einen Pixel auseinanderliegen und
       beide durchkommen. Genau deshalb aenderte der Regler bisher nur, *welche*
       Bilder erscheinen, und nichts an ihrer Lage. Also die neun Felder ringsum
       mitpruefen -- das Raster ist nur der Index, der Abstand die Bedingung. */
    const taken = new Map();
    const gap2 = gap * gap;

    // Zahl statt Zeichenkette als Rasterschluessel: je Bewerber neun
    // Nachschlagevorgaenge, bei 4.000 Bewerbern also 36.000 -- als
    // zusammengesetzte Zeichenkette waren das ebenso viele neue Objekte.
    const key = (gx, gy) => (gx + 2048) * 4096 + (gy + 2048);

    function free(sx, sy) {
      const gx = Math.floor(sx / gap), gy = Math.floor(sy / gap);
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          const list = taken.get(key(gx + dx, gy + dy));
          if (!list) continue;
          for (let k = 0; k < list.length; k += 2) {
            const ddx = list[k] - sx, ddy = list[k + 1] - sy;
            if (ddx * ddx + ddy * ddy < gap2) return false;
          }
        }
      }
      return true;
    }

    function claim(sx, sy) {
      const k = key(Math.floor(sx / gap), Math.floor(sy / gap));
      const list = taken.get(k);
      if (list) list.push(sx, sy);
      else taken.set(k, [sx, sy]);
    }

    let drawn = 0;

    /* Nur vormerken, noch nicht zeichnen.

       Der Punkt unter einem Bild ist Doppelung: er steht fuer dasselbe Foto,
       das daneben schon zu sehen ist -- beim Abstossen faellt das auf, weil
       das Bild wegwandert und der Punkt liegen bleibt. Um ihn weglassen zu
       koennen, muss vorher feststehen, welche Bilder wirklich erscheinen.
       Also erst planen, dann die Punkte zeichnen, dann die Bilder darauf. */
    function planTile(i, sx, sy) {
      const img = bestImage(i, size);
      // Der Platz bleibt belegt, auch wenn das Bild noch laedt: sonst
      // uebernimmt bei jedem Ladevorgang ein anderes Foto die Stelle. Der
      // Punkt bleibt dann sichtbar -- da ist ja noch kein Bild.
      if (!img) return;
      drawn++;
      planned.push([i, sx, sy, img]);
      covered[i] = 1;
      touched.push(i);
    }

    if (repel) {
      /* Abstossen statt Weglassen -- wie die Knoten in einem Graphen.

         "Weniger zeigen" ist keine Entzerrung, sondern eine Auswahl. Wer
         Abstand haben will, muss die Bilder bewegen: sie schieben einander
         weg, bis der Mindestabstand steht. Das kostet die genaue Lage, und
         deshalb ist die Verschiebung in Weltkoordinaten begrenzt -- in der
         Uebersicht unsichtbar klein, beim Hineinzoomen waechst sie mit dem
         Platz, den das Zoomen schafft. Der Punkt darunter bleibt liegen. */
      const cand = near.slice(0, budget);
      const tFan = performance.now();
      fanOut(cand, gap, w, h, shift);
      fanMs = performance.now() - tFan;
      for (const [, i, sx, sy] of cand) planTile(i, sx, sy);
    } else if (!spaced) {
      for (const [, i, sx, sy] of near) {
        if (capped && drawn >= budget) break;
        planTile(i, sx, sy);
      }
    } else {
      /* Platzvergabe rein nach Lage, nicht nach Ladezustand.

         Genau das war die Ursache des Flackerns beim Schieben: ein
         Durchgang, der geladene Bilder bevorzugt, vergibt die Plaetze bei
         jedem Bild anders, weil zwischendurch neue eintreffen. Wer nur nach
         der Reihenfolge von der Bildmitte nach aussen vergibt, kommt bei
         gleicher Kamera immer zum selben Ergebnis -- ob ein Bild schon da
         ist, aendert daran nichts.

         `held` haelt das Ergebnis zusaetzlich fest, solange die Kamera
         steht: dann waechst die Auswahl nur noch, sie ordnet sich nicht um. */
      for (const [i, sx, sy] of held) {
        claim(sx, sy);
        planTile(i, sx, sy);
      }
      for (const [, i, sx, sy] of near) {
        if (held.length >= budget) break;
        if (heldSet.has(i)) continue;
        if (!free(sx, sy)) continue;
        claim(sx, sy);
        held.push([i, sx, sy]);
        heldSet.add(i);
        planTile(i, sx, sy);
      }
    }

    stats.perThumb = msPerThumb;
    stats.drawn = drawn;
    stats.budget = capped ? budget : Infinity;
    stats.wish = wish;
    stats.raster = spaced || repel;
    stats.gap = spaced || repel ? Math.round(gap) : 0;
    stats.gapReason = repel ? "abstoßend" : (spaced ? "Fläche" : "");
    stats.shift = repel ? Math.round(shift) : 0;
    stats.fanMs = Math.round(fanMs * 10) / 10;
  }

  /** Den Plan aufs Bild bringen -- und dabei messen, was es kostet. */
  function paintThumbs(picked) {
    let imgMs = 0;
    for (const [i, sx, sy, img] of planned) {
      const ar = img.naturalWidth / img.naturalHeight || 1;
      const bw = ar >= 1 ? tileBox : tileBox * ar;
      const bh = ar >= 1 ? tileBox / ar : tileBox;
      const tImg = performance.now();
      ctx.globalAlpha = picked && !selection.has(i) ? 0.28 : 1;
      ctx.drawImage(img, sx - bw / 2, sy - bh / 2, bw, bh);
      if (picked && selection.has(i)) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(sx - bw / 2, sy - bh / 2, bw, bh);
      }
      ctx.globalAlpha = 1;
      imgMs += performance.now() - tImg;
      shown.push([i, sx - bw / 2, sy - bh / 2, bw, bh]);
    }
    // Nur messen, wenn genug gezeichnet wurde -- bei einer Handvoll Bildern
    // steckt in der Zahl mehr Grundrauschen als Bildkosten.
    if (planned.length > 30) {
      const each = Math.min(MS_PER_THUMB_MAX, imgMs / planned.length);
      msPerThumb += (each - msPerThumb) * (each > msPerThumb ? MS_RISE : MS_FALL);
    }
    stats.imgMs = Math.round(imgMs * 10) / 10;
  }

  /** Eine Kachel je Gelegenheit statt eines Punkts je Foto.

      17 370 Einzelbilder sind nicht stoeberbar, 1 755 Serien ab drei Fotos
      schon. Die Groesse folgt der Wurzel der Fotozahl -- linear wuerde
      „Silvester 2012" mit 163 Bildern alles andere verdecken. */
  function drawEvents(w, h) {
    const visible = [];
    for (const ev of model.events) {
      if (ev.n < minEventSize) continue;
      const sx = toScreenX(ev.x), sy = toScreenY(ev.y);
      if (sx < -80 || sy < -80 || sx > w + 80 || sy > h + 80) continue;
      visible.push([ev, sx, sy]);
    }
    // Kleine zuerst zeichnen, grosse oben drauf.
    visible.sort((a, b) => a[0].n - b[0].n);
    for (const [ev, sx, sy] of visible) {
      const box = Math.max(16, Math.min(88, Math.sqrt(ev.n) * 5 * Math.max(1, cam.scale / 900)));
      const img = imageForId(ev.cover, box > 46 ? 320 : 160);
      if (img) {
        const ar = img.naturalWidth / img.naturalHeight || 1;
        const bw = ar >= 1 ? box : box * ar;
        const bh = ar >= 1 ? box / ar : box;
        ctx.drawImage(img, sx - bw / 2, sy - bh / 2, bw, bh);
        // Weit gestreute Serien markieren: sie halten inhaltlich nicht
        // zusammen und sind oft gar keine Gelegenheit, sondern ein Tag.
        if (ev.spread > 0.18) {
          ctx.strokeStyle = "#e0703c";
          ctx.lineWidth = 1.5;
          ctx.strokeRect(sx - bw / 2, sy - bh / 2, bw, bh);
        }
      } else {
        ctx.fillStyle = "#2b323f";
        ctx.fillRect(sx - box / 2, sy - box / 2, box, box);
      }
    }
  }

  /** Naechste Serie zum Zeiger -- gewichtet, damit grosse Kacheln leichter treffen. */
  /** Kontinentname unter dem Zeiger -- sein Schwerpunkt traegt die Schrift. */
  function labelAt(sx, sy) {
    if (mode === "serien" || cam.scale > 6000) return -1;
    for (const c of model.clusters) {
      const [wx, wy] = spreadAt(c.x, c.y, c.i);
      const cx = toScreenX(wx), cy = toScreenY(wy);
      const label = model.clusterLabel[c.i];
      const half = label.length * 3.6 + 8;
      if (Math.abs(sx - cx) <= half && Math.abs(sy - cy) <= 11) return c.i;
    }
    return -1;
  }

  function nearestEvent(sx, sy) {
    let best = -1, bestD = Infinity;
    for (const ev of model.events) {
      if (ev.n < minEventSize) continue;
      const box = Math.max(16, Math.min(88, Math.sqrt(ev.n) * 5 * Math.max(1, cam.scale / 900)));
      const dx = toScreenX(ev.x) - sx, dy = toScreenY(ev.y) - sy;
      const d = Math.max(Math.abs(dx), Math.abs(dy)) - box / 2;
      if (d < bestD) { bestD = d; best = ev.i; }
    }
    return bestD <= 4 ? best : -1;
  }

  function drawClusterLabels(w, h) {
    // In der Uebersicht die Kontinentnamen, beim Hineinzoomen ausblenden --
    // dann sprechen die Fotos selbst.
    const strength = cam.scale > THUMB_SCALE ? 0.18 : 1;
    if (strength < 0.2 && cam.scale > 6000) return;
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const c of model.clusters) {
      const [wx, wy] = spreadAt(c.x, c.y, c.i);
      const sx = toScreenX(wx), sy = toScreenY(wy);
      if (sx < -40 || sy < -20 || sx > w + 40 || sy > h + 20) continue;
      const label = model.clusterLabel[c.i];
      const weak = c.cap_share < 0.15;
      ctx.font = `${weak ? 400 : 600} ${Math.min(15, 10 + c.n / 90)}px system-ui, sans-serif`;
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = "rgba(10,12,16,0.85)";
      ctx.globalAlpha = strength * (weak ? 0.5 : 0.95);
      ctx.strokeText(label, sx, sy);
      ctx.fillStyle = weak ? "#9aa4b0" : "#e8edf3";
      ctx.fillText(label, sx, sy);
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function drawLasso() {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(lassoPath[0][0], lassoPath[0][1]);
    for (const [x, y] of lassoPath.slice(1)) ctx.lineTo(x, y);
    ctx.closePath();
    ctx.fillStyle = "rgba(120,190,255,0.10)";
    ctx.fill();
    ctx.strokeStyle = "#7ec2ff";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.restore();
  }

  /* ---- Treffer --------------------------------------------------------- */

  function nearest(sx, sy, maxPx = 14) {
    // Was gezeichnet wurde, zuerst -- und von hinten, weil das zuletzt
    // gezeichnete Bild obenauf liegt. Ohne das trifft ein Klick auf ein
    // aufgefaechertes Bild den Punkt darunter, also ein anderes Foto.
    for (let k = shown.length - 1; k >= 0; k--) {
      const [i, x, y, bw, bh] = shown[k];
      if (sx >= x && sx <= x + bw && sy >= y && sy <= y + bh) return i;
    }
    let best = -1, bestD = maxPx * maxPx;
    for (let i = 0; i < model.n; i++) {
      if (!mask[i]) continue;
      const dx = toScreenX(px[i]) - sx, dy = toScreenY(py[i]) - sy;
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  function inPolygon(path, x, y) {
    let inside = false;
    for (let i = 0, j = path.length - 1; i < path.length; j = i++) {
      const [xi, yi] = path[i], [xj, yj] = path[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function pickInPath(path) {
    const hit = new Set();
    // Erst die Huelle, dann der teure Test -- spart bei kleinen Lassos fast alles.
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of path) {
      x0 = Math.min(x0, x); y0 = Math.min(y0, y);
      x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    }
    // Aufgefaecherte Bilder liegen nicht auf ihrem Punkt. Umkreist man sie,
    // muessen sie mitkommen -- sonst waehlt das Lasso etwas anderes aus als
    // das, was darin liegt.
    const moved = new Set();
    for (const [i, x, y, bw, bh] of shown) {
      moved.add(i);
      if (!mask[i]) continue;
      const cx = x + bw / 2, cy = y + bh / 2;
      if (cx < x0 || cx > x1 || cy < y0 || cy > y1) continue;
      if (inPolygon(path, cx, cy)) hit.add(i);
    }
    for (let i = 0; i < model.n; i++) {
      if (!mask[i] || moved.has(i)) continue;
      const sx = toScreenX(px[i]), sy = toScreenY(py[i]);
      if (sx < x0 || sx > x1 || sy < y0 || sy > y1) continue;
      if (inPolygon(path, sx, sy)) hit.add(i);
    }
    return hit;
  }

  /* ---- Maus ------------------------------------------------------------ */

  let drag = null;
  let lassoMode = false;

  canvas.addEventListener("mousedown", (e) => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (lassoMode || e.shiftKey) {
      lassoPath = [[sx, sy]];
      drag = { kind: "lasso" };
    } else {
      drag = { kind: "pan", sx, sy, tx: cam.tx, ty: cam.ty, moved: false };
    }
  });

  window.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (!drag) {
      if (sx < 0 || sy < 0 || sx > r.width || sy > r.height) return;
      if (mode === "serien") { hooks.onHoverEvent?.(nearestEvent(sx, sy), sx, sy); return; }
      const overLabel = labelAt(sx, sy) >= 0;
      canvas.classList.toggle("on-label", overLabel);
      hooks.onHover?.(overLabel ? -1 : nearest(sx, sy), sx, sy);
      return;
    }
    if (drag.kind === "lasso") {
      const last = lassoPath[lassoPath.length - 1];
      if ((last[0] - sx) ** 2 + (last[1] - sy) ** 2 > 9) lassoPath.push([sx, sy]);
      schedule();
    } else {
      cam.tx = drag.tx + (sx - drag.sx);
      cam.ty = drag.ty + (sy - drag.sy);
      if (Math.abs(sx - drag.sx) + Math.abs(sy - drag.sy) > 3) drag.moved = true;
      schedule();
    }
  });

  window.addEventListener("mouseup", (e) => {
    if (!drag) return;
    if (drag.kind === "lasso" && lassoPath && lassoPath.length > 2) {
      hooks.onLasso?.(pickInPath(lassoPath),
                      { subtract: e.altKey, add: e.ctrlKey || e.metaKey });
    } else if (drag.kind === "pan" && !drag.moved) {
      const r = canvas.getBoundingClientRect();
      const sx = e.clientX - r.left, sy = e.clientY - r.top;
      if (mode === "serien") {
        const ev = nearestEvent(sx, sy);
        if (ev >= 0) hooks.onPickEvent?.(ev, e.ctrlKey || e.metaKey);
        return;
      }
      // Ein Klick auf den Kontinentnamen waehlt den ganzen Kontinent. Das ist
      // die praezise Alternative zum Lasso: wo die Raender ineinander
      // sprenkeln, erwischt ein gezogener Kreis immer zu viel.
      const lab = labelAt(sx, sy);
      if (lab >= 0) { hooks.onPickCluster?.(lab, e.shiftKey); return; }
      const i = nearest(sx, sy);
      // Strg/Cmd nimmt den Faden auf, statt das Bild zu oeffnen.
      if (i >= 0) (e.ctrlKey || e.metaKey ? hooks.onThread : hooks.onPick)?.(i);
    }
    lassoPath = null;
    drag = null;
    schedule();
  });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
  }, { passive: false });

  canvas.addEventListener("mouseleave", () => hooks.onHover?.(-1));

  /* ---- Aussenschnittstelle --------------------------------------------- */

  const scene = {
    get camera() { return cam; },
    get layout() { return layout; },
    setLayout(name) {
      if (name === layout) return;
      layout = name;
      fromX = Float32Array.from(px);
      fromY = Float32Array.from(py);
      toX = model.layouts[name].x;
      toY = model.layouts[name].y;
      mix = 0;
      mixStart = performance.now();
      schedule();
    },
    setColorMode(mode) { colorMode = mode; rebuildBuckets(); schedule(); },
    setMask(m) { mask = m; maskVersion++; schedule(); },
    setSelection(sel) { selection = sel; schedule(); },
    setMode(m) { mode = m; schedule(); },
    setSpread(k) { spread = k; schedule(); },
    setTileScale(k) { tileScale = k; schedule(); },
    setDeclutter(px_) { declutter = px_; schedule(); },
    setThumbMode(m) { thumbMode = m; schedule(); },
    stats: () => {
      // Nur wirklich geladene Bilder zaehlen. `images.size` enthielt auch die
      // Platzhalter der Angefragten -- 11.615 "Bilder im Speicher" bei 231
      // tatsaechlichen Abrufen, und die Megabyte daneben waren entsprechend
      // erfunden.
      let real = 0;
      for (const v of images.values()) if (v) real++;
      return { ...stats, cached: real, queued: queue.length, inflight };
    },
    setMinEventSize(n) { minEventSize = n; schedule(); },
    get mode() { return mode; },
    setLassoMode(on) { lassoMode = on; canvas.classList.toggle("lasso", on); },
    focusCluster(c) { flyTo(c.x, c.y, 5200); },
    focusSet(indices) { flyToSet(indices); },
    reset() { fitAll(); scene.setLayout("bedeutung"); schedule(); },
    resize() { resize(); schedule(); },
    fitAll() { fitAll(); schedule(); },
    draw: schedule,
    positionOf(i) { return [toScreenX(px[i]), toScreenY(py[i])]; },
  };

  new ResizeObserver(() => { resize(); schedule(); }).observe(canvas);
  resize();
  fitAll();
  rebuildBuckets();
  px.set(toX);
  py.set(toY);
  schedule();
  return scene;
}
