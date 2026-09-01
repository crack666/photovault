/* Die Leinwand: Kamera, Zeichnen, Maus.

   17 370 Punkte sind fuer Canvas 2D unkritisch -- teuer waere nur, fuer jeden
   Punkt `fillStyle` neu zu setzen. Die Punkte werden deshalb einmal je
   Farbmodus nach Farbe sortiert und dann in Gruppen gezeichnet.

   Fotos erscheinen erst beim Hineinzoomen. In der Uebersicht waeren 17 000
   Bilder à 6 px ohnehin Matsch; sichtbar bleibt dort ein Leitbild je
   Kontinent. */

import { colorFor, spreadPoint } from "./model.js?v=63";
import { thumbUrl } from "../core/api.js?v=63";

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
   gemessen in Kachelabstaenden, nicht in Weltkoordinaten.

   Hier stand eine feste Weltdistanz (0,004), mit dem Argument, der Fehler
   solle auf jeder Zoomstufe derselbe kleine Weltabstand sein. Das klingt
   sauber und war der Grund, warum der Regler nichts bewirkte: die Kachel
   waechst beim Hineinzoomen viel langsamer als der Massstab -- sie klemmt bei
   96 px --, der geforderte Abstand haengt an ihr, die erlaubte Verschiebung
   dagegen am Massstab. Gemessen ueber den nutzbaren Bereich:

     Massstab   2600   gap  31 px   Grenze  10 px   = 0,34 * gap
     Massstab   9000   gap  69 px   Grenze  36 px   = 0,52 * gap
     Massstab  20000   gap  88 px   Grenze  80 px   = 0,91 * gap

   Ueberall weniger als der Abstand, den die Bilder herstellen sollen. Die
   Relaxation schiebt auseinander, die Grenze zieht zurueck, und heraus kam
   ein Nachbarabstand von 11,6 -> 11,8 px bei gefordertem 69. Nicht wenig --
   nichts.

   Der Massstab ist hier die falsche Einheit. Die Frage lautet "wie weit darf
   ein Foto von seinem Punkt weg, ohne dass man es ihm noch zuordnet", und die
   beantwortet man in Kacheln: drei Kachelabstaende sind auf jeder Zoomstufe
   dasselbe fuer das Auge -- und der Punkt darunter bleibt ohnehin liegen,
   die Verschiebung ist sichtbar und nicht behauptet.

   Drei, nicht anderthalb, seit die Bilder in den leeren Raum neben dem
   Haufen ruecken duerfen (FAN_PACK): die Grenze ist nur noch die Decke
   fuers Aufblasen des Haufens. Gemessen bindet sie selten -- die mittlere
   Verschiebung liegt bei 25 bis 44 px, die groesste um 90 bis 120 -- aber
   sie haelt den Schwanz: kein Bild landet weiter als drei Kacheln von
   seinem Punkt. */
const FAN_REACH = 3;

/* Wieviele Bilder hierher passen, sagt das Fenster -- nicht der Haufen.

   Der erste Wurf hat die Bewerber auf der *heutigen* Flaeche des Haufens
   ausgesiebt: was dort bei knappem Abstand ueberlebte, durfte bleiben. Das
   war zu wenig, und der Einwand dagegen war richtig -- insgesamt ist ja
   Platz. Ein gedraengter Haufen soll seine Nachbarn zur Seite schieben und
   sich in den leeren Raum ausbreiten, wie die Knoten eines Kraftgraphen.
   Die Frage "wieviele" beantwortet deshalb die Fensterflaeche:

     Kapazitaet = FAN_PACK * Fensterflaeche / gap²

   0,9 ist gemessen die Stelle, an der beides noch gilt: der Abstand kommt
   an (Nachbarabstand 0,94 bis 1,01 mal gap; das zehnte Perzentil, also die
   schlimmste Draengelei, nicht unter 0,73), und die Karte steht nach ein
   bis vier Sekunden. Bei 1,1 kippt es: fuenf Sekunden Wanderung fuer drei
   Prozent weniger Abstand.

   Gegen schlichtes Ausduennen, das vorher der heimliche Massstab war:

     Massstab  9000   ausduennen 191    jetzt 274 Bilder
     Massstab 20000   ausduennen 118    jetzt 166
     Massstab 40000   ausduennen  83    jetzt 117

   Anderthalbmal so viele, alle beim vollen Abstand. */
const FAN_PACK = 0.9;

/* Wer die Plaetze bekommt: gesiebt, von grob nach fein.

   Das erste Sieb nimmt, wer den Abstand schon von allein hat -- diese
   Bilder muessen sich gar nicht bewegen. Jedes weitere laesst dichter
   Stehendes nach, bis die Kapazitaet erreicht ist; das letzte nimmt auch
   exakt Uebereinanderliegendes, das erst das Auffaechern trennt. Die
   Reihenfolge ist die der Wahrheit: erst die Unverrueckten, dann die, die
   Platz brauchen. Kein Teiler ist groesser als 1, deshalb genuegt dem
   Sieb dasselbe Raster mit Zellgroesse gap. */
const FAN_SIEBE = [1, 1.4, 2, 3.2, 5, Infinity];

/* Die Weltspreizung: ein Mindestabstand fuer jedes Fotopaar, in
   Weltkoordinaten.

   Die Bildschirm-Relaxation oben kann nur verteilen, was das Fenster
   hergibt. Der eigentliche Engpass liegt aber eine Ebene tiefer: die
   Einbettung legt aehnliche Fotos praktisch uebereinander. Gemessen am
   echten Bestand waren nur 24 Prozent aller Fotos ueberhaupt jemals
   darstellbar -- selbst am Zoomende lagen drei Viertel dichter beieinander,
   als eine Kachel breit ist. Kein Fensterverfahren der Welt aendert das:
   der Platz muss in der *Welt* entstehen, nicht auf dem Schirm.

   Also werden die Weltkoordinaten selbst relaxiert -- einmal je Anordnung,
   mit demselben Verfahren wie beim Zeichnen (Raster, gemittelte Schuebe),
   bis jedes Paar mindestens BLAST_D auseinanderliegt. Der Wert ist so
   gewaehlt, dass die ganze Karte dann in den Zoombereich passt:

     0,006 * Zoom 45000 = 270 px  -- jede Kachel hat Luft
     Belegung: 14.887 * 0,866 * 0,006² = 46 % des Einheitsquadrats

   Gemessen: nach 15 Durchgaengen sind 93 Prozent frei, nach 60 sind es
   99,9, nach 120 alle. Der Median wandert dabei 1,1 Prozent der
   Kartenbreite -- die Karte bleibt sie selbst, sie atmet nur aus.

   In der Zeit-Anordnung ist x das Datum und bleibt gesperrt; dort enden
   die Saeulen bei 76 Prozent. Das ist der Preis der Entscheidung aus
   Stufe 1, und er ist es wert: ein Foto unter falschem Jahr waere eine
   Luege, ein Punkt statt einer Kachel ist nur ein Punkt. */
const BLAST_D = 0.006;

//: Obergrenze der Durchgaenge -- danach ist praktisch alles frei, und der
//: Rest sind Reststapel, die auch der Bildschirm-Finisher trennen kann.
const BLAST_ITER = 200;

//: Rechenzeit je Frame fuers Entfalten. Ein Durchgang kostet gemessen
//: 5 bis 10 ms; ein Budget knapp darunter heisst ein Durchgang je Frame,
//: und das Entfalten ist selbst die Animation.
const BLAST_MS = 9;

/* Wieviele Nachbarn ein Bild je Durchgang hoechstens wegschiebt.

   Ohne diese Grenze ist das Verfahren quadratisch: vor dem ersten Durchgang
   liegen in einem dichten Haufen hunderte Punkte in *einem* Rasterfeld, und
   jeder wird gegen jeden geprueft. Gemessen: 123 ms je Bild, also acht Bilder
   je Sekunde beim Schwenken.

   Ein Deckel kostet fast nichts an Qualitaet, weil ueber sechs Durchgaenge
   ohnehin jeder mit jedem in Beruehrung kommt -- der Haufen dehnt sich nur
   etwas langsamer aus. Und er wirkt genau dort, wo er muss: in duennen
   Gegenden hat kein Punkt so viele Nachbarn. */
//: Wie weit eine Kachel je Frame ihrer neuen Sollage folgt. Ganz (1.0)
//: hiesse: jedes Frame die volle Loesung, also auch jeden Sprung der
//: Relaxation ungefiltert. Ein Drittel macht aus dem Nachziehen beim
//: Schwenken eine weiche Bewegung, ohne dass es traege wirkt -- gemessen
//: 0,8 px groesster Schirmsprung waehrend eines Schwenks.
const FAN_EASE = 0.34;

/* Was noch als Bewegung zaehlt, in Pixeln je Kachel und Frame.

   Nicht nur ein Abbruchkriterium, sondern die Schwelle beim Uebernehmen
   selbst: was sich um weniger verschieben wuerde, bleibt genau liegen. Der
   Unterschied ist nicht kosmetisch. Ohne die Schwelle laeuft die Schleife
   ewig weiter, denn der Nachbarsatz einer Kachel ist keine stetige Funktion
   ihrer Lage -- an einer Rasterkante wechselt sie das Feld, sieht andere
   Nachbarn, bekommt eine andere Mittelung und wandert zurueck. Gemessen im
   Browser: 0,1 px Unruhe, ueber zwoelf Sekunden unveraendert, bei voellig
   stehender Kamera und leerer Ladeschlange. Unsichtbar auf einer 88-px-
   Kachel, aber die Karte zeichnet dafuer bis zum Sankt-Nimmerleins-Tag.

   Bleibt in einem Frame jede Kachel liegen, ist der naechste Frame Zeichen
   fuer Zeichen derselbe -- damit steht es, und zwar beweisbar, nicht nur
   annaehernd. Der Wert entscheidet, wie lange das dauert und was es kostet:

     0,08 px   steht nach 3,6 bis 5,1 s   Abstand 67,9 von 69
     0,25 px   steht nach 0,9 bis 2,0 s   Abstand 66,5 von 69
     0,40 px   steht nach 0,8 bis 1,3 s   Abstand 65,3 von 69

   Ein Viertelpixel sieht niemand, und zwei Prozent Abstand ist ein Preis,
   den man fuer eine Karte zahlt, die auch wirklich stillsteht. Auf den
   Schwenk wirkt die Schwelle nicht: der groesste Schirmsprung bleibt bei
   allen drei Werten 0,74 px. */
const FAN_RUHE = 0.25;

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
  const stats = { visible: 0, drawn: 0, budget: 0, raster: false, gap: 0, verschoben: 0,
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

  /* Die Verschiebungen des Abstossens, ueber den Frame hinaus.

     Bewusst NICHT an den Kamerazustand gebunden -- anders als `held`. Genau
     beim Schwenken sollen sie ja erhalten bleiben, sonst faengt das
     Abstossen bei jedem Pixel von vorn an. Verworfen wird nur, wenn sich
     aendert, *was* abgestossen wird oder *wie weit*. */
  let fanned = new Map();       // index -> [dx, dy] gegen die wahre Lage
  let fanKey = "";              // Einstellung, zu der die Verschiebungen passen
  let unruhe = 0;               // mittlere Aenderung je Kachel und Frame, px
  let verschoben = 0;           // mittlere Abweichung von der wahren Lage, px
  let fanKern = new Set();      // wer beim Abstossen zuletzt dabei war
  let blast = null;             // Weltspreizung: { layout, xs, ys, iter, fertig, lockX }

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
      applyBlast();
      applySpread();
      return false;
    }
    for (let i = 0; i < model.n; i++) {
      px[i] = fromX[i] + (toX[i] - fromX[i]) * mix;
      py[i] = fromY[i] + (toY[i] - fromY[i]) * mix;
    }
    applyBlast();
    applySpread();
    return true;
  }

  /* Die Weltspreizung einblenden, so weit der Regler steht.

     Als Differenz zur Ziel-Anordnung, nicht als Ersatz: so laesst sie sich
     stufenlos mischen, und der Regler bei 0 kostet exakt nichts. Ueber 1
     waechst sie nicht weiter -- mehr Luft holt dort der Bildschirm-Teil,
     der den Wunschabstand kennt. */
  function applyBlast() {
    if (declutter <= 0 || mode === "serien") return;
    if (!blast || blast.layout !== layout) return;
    const ramp = Math.min(1, declutter);
    const bx = blast.xs, by = blast.ys;
    for (let i = 0; i < model.n; i++) {
      px[i] += (bx[i] - toX[i]) * ramp;
      py[i] += (by[i] - toY[i]) * ramp;
    }
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

  /* In der Zeit-Anordnung ist die Waagerechte das Aufnahmedatum.

     Damit ist sie keine Bildschirmrichtung mehr, sondern eine Aussage. Ein
     Kontinent, der nach rechts ausweicht, behauptet ein spaeteres Datum --
     und die Jahresbaender darunter widersprechen ihm. Gemessen lag rund die
     Haelfte der gesamten Ausweichbewegung auf dieser Achse, und bei Regler
     0,5 stand die Mehrheit der Fotos unter einem fremden Band.

     Also: in "zeit" wird x festgehalten. Ausgewichen wird nur senkrecht --
     die Senkrechte traegt hier ohnehin nichts, sie kommt unveraendert aus
     der Bedeutungs-Anordnung (model.js, byTime gibt `y: m.y` durch). Damit
     stimmen die Baender ohne eigene Umrechnung, weil an x nichts geschieht. */
  function timeLocked() { return layout === "zeit"; }

  /* Die Weltspreizung rechnet im Hintergrund, ein Durchgang je Frame.

     Kein Worker, kein Blocken: draw() ruft ensureBlast(), das hoechstens
     BLAST_MS verbraucht und sagt, ob es noch etwas zu tun gibt. Solange
     ja, wird weitergezeichnet -- das Entfalten der Karte ist damit selbst
     die Animation, wie bei einem Kraftgraphen, der sich setzt. */
  function blastStart() {
    const L = model.layouts[layout];
    blast = {
      layout,
      xs: Float64Array.from(L.x),
      ys: Float64Array.from(L.y),
      iter: 0,
      fertig: false,
      lockX: timeLocked(),
    };
  }

  function blastStep(budgetMs) {
    const t0 = performance.now();
    const d = BLAST_D, d2 = d * d;
    const xs = blast.xs, ys = blast.ys;
    const key = (gx, gy) => gx * 100000 + gy;
    while (blast.iter < BLAST_ITER && performance.now() - t0 < budgetMs) {
      const grid = new Map();
      for (let i = 0; i < model.n; i++) {
        const k = key(Math.floor(xs[i] / d), Math.floor(ys[i] / d));
        const l = grid.get(k);
        if (l) l.push(i); else grid.set(k, [i]);
      }
      let bewegt = 0;
      for (let i = 0; i < model.n; i++) {
        const gx = Math.floor(xs[i] / d), gy = Math.floor(ys[i] / d);
        let sx = 0, sy = 0, treffer = 0;
        for (let a = gx - 1; a <= gx + 1; a++) {
          for (let b = gy - 1; b <= gy + 1; b++) {
            const l = grid.get(key(a, b));
            if (!l) continue;
            for (const j of l) {
              if (j === i) continue;
              let dx = xs[j] - xs[i], dy = ys[j] - ys[i];
              let dd = dx * dx + dy * dy;
              if (dd >= d2) continue;
              if (dd < 1e-16) {
                const ang = (i * 2.399963) % 6.283185;
                dx = Math.cos(ang); dy = Math.sin(ang); dd = 1;
              }
              const dist = Math.sqrt(dd);
              const push = (d - dist) / 2;
              sx -= (dx / dist) * push;
              sy -= (dy / dist) * push;
              treffer++;
            }
          }
        }
        if (treffer) {
          if (!blast.lockX) xs[i] += sx / treffer;
          ys[i] += sy / treffer;
          bewegt++;
        }
      }
      blast.iter++;
      if (!bewegt) { blast.fertig = true; return; }
    }
    if (blast.iter >= BLAST_ITER) blast.fertig = true;
  }

  /** @returns true, solange noch entfaltet wird -- dann weiterzeichnen. */
  function ensureBlast() {
    if (declutter <= 0 || mode === "serien") return false;
    if (!blast || blast.layout !== layout) blastStart();
    if (!blast.fertig) blastStep(BLAST_MS);
    return !blast.fertig;
  }

  function clusterRadii(cc) {
    // Wie weit ein Kontinent reicht: quadratisches Mittel der Abstaende
    // seiner Fotos vom eigenen Mittelpunkt. Robuster als das Maximum, das
    // ein einzelner Ausreisser sonst aufblaeht.
    //
    // Getrennt nach Achsen, weil die Kontinente in der Zeit-Anordnung breit
    // und flach liegen: gemessen 0,146 waagerecht gegen 0,049 senkrecht. Ein
    // einziger runder Radius fordert senkrecht dreimal mehr Luft, als sie
    // dort einnehmen -- und weil sie die nicht bekommen, weichen sie
    // waagerecht aus. Genau durch die Chronologie.
    const k = cc.length / 2;
    const sx = new Float64Array(k), sy = new Float64Array(k);
    const cnt = new Float64Array(k);
    for (let i = 0; i < model.n; i++) {
      const c = model.cl[i];
      const dx = toX[i] - cc[c * 2], dy = toY[i] - cc[c * 2 + 1];
      sx[c] += dx * dx; sy[c] += dy * dy;
      cnt[c]++;
    }
    const rx = new Float64Array(k), ry = new Float64Array(k), r = new Float64Array(k);
    for (let c = 0; c < k; c++) {
      rx[c] = cnt[c] ? Math.sqrt(sx[c] / cnt[c]) : 0;
      ry[c] = cnt[c] ? Math.sqrt(sy[c] / cnt[c]) : 0;
      r[c] = Math.hypot(rx[c], ry[c]);
    }
    return { rx, ry, r };
  }

  function buildSpread(strength) {
    const cc = model.layouts[layout].centroids;
    const k = cc.length / 2;
    const { rx, ry, r } = clusterRadii(cc);
    const locked = timeLocked();
    // Nach dem Zusammenziehen ist jeder Kontinent um denselben Faktor
    // kleiner -- die Kreise, die einander ausweichen muessen, also auch.
    const shrink = 1 / (1 + strength);
    const x = new Float64Array(k), y = new Float64Array(k);
    for (let c = 0; c < k; c++) { x[c] = cc[c * 2]; y[c] = cc[c * 2 + 1]; }

    for (let it = 0; it < SPREAD_ITERATIONS; it++) {
      let moved = 0;
      for (let a = 0; a < k; a++) {
        for (let b = a + 1; b < k; b++) {
          // Der kleinere Kontinent weicht mehr aus: sonst schiebt ein
          // Haeufchen von zwoelf Fotos einen mit dreitausend beiseite.
          if (locked) {
            /* Nur senkrecht -- und nur, wenn sie sich waagerecht ueberhaupt
               ins Gehege kommen. Zwei Kontinente aus 2003 und 2023 stehen
               nebeneinander, nicht uebereinander; sie muessen einander nicht
               ausweichen, und wer sie trotzdem trennt, verbraucht Hoehe fuer
               nichts. */
            const overlapX = (rx[a] + rx[b]) * shrink * SPREAD_AIR;
            if (Math.abs(x[b] - x[a]) >= overlapX) continue;
            const want = (ry[a] + ry[b]) * shrink * SPREAD_AIR;
            let dy = y[b] - y[a];
            const d = Math.abs(dy);
            if (d >= want) continue;
            // Genau uebereinander: eine Richtung muss her, und sie muss bei
            // jedem Durchgang dieselbe sein.
            if (d < 1e-9) dy = (a % 2 ? -1 : 1) * 1e-9;
            const wa = ry[b] / (ry[a] + ry[b] || 1);
            const push = (want - d) * Math.sign(dy || 1);
            y[a] -= push * wa;
            y[b] += push * (1 - wa);
            moved++;
            continue;
          }
          const want = (r[a] + r[b]) * shrink * SPREAD_AIR;
          let dx = x[b] - x[a], dy = y[b] - y[a];
          let d2 = dx * dx + dy * dy;
          if (d2 >= want * want) continue;
          if (d2 < 1e-12) { const ang = a * 2.399963; dx = Math.cos(ang); dy = Math.sin(ang); d2 = 1; }
          const d = Math.sqrt(d2);
          const wa = r[b] / (r[a] + r[b] || 1);
          const push = (want - d);
          const ux = dx / d, uy = dy / d;
          x[a] -= ux * push * wa; y[a] -= uy * push * wa;
          x[b] += ux * push * (1 - wa); y[b] += uy * push * (1 - wa);
          moved++;
        }
      }
      if (!moved) break;
    }

    spreadDx = new Float32Array(k);
    spreadDy = new Float32Array(k);

    /* Zurueck ins Bild: das Auseinanderschieben macht die Karte groesser,
       und niemand will danach erst herauszoomen.

       In der Bedeutungs-Anordnung wird dafuer *ein* gemeinsames lo/span ueber
       beide Achsen benutzt. Das ist Absicht und kein Fehler: dort sind x und
       y zwei Richtungen derselben Einbettung, und wer sie einzeln staucht,
       verzerrt die Abstaende, um die es in dieser Karte gerade geht.

       In der Zeit-Anordnung ist es genau umgekehrt. x gehoert den
       Jahresbaendern und wird gar nicht angefasst; nur die Senkrechte wird
       zurueckgeholt, und die darf das, weil sie nichts behauptet. */
    if (locked) {
      let lo = Infinity, hi = -Infinity;
      for (let c = 0; c < k; c++) {
        lo = Math.min(lo, y[c] - ry[c] * shrink);
        hi = Math.max(hi, y[c] + ry[c] * shrink);
      }
      const span = Math.max(hi - lo, 1e-6);
      for (let c = 0; c < k; c++) {
        spreadDx[c] = 0;
        spreadDy[c] = (y[c] - lo) / span - cc[c * 2 + 1];
      }
      return;
    }

    let lo = Infinity, hi = -Infinity;
    for (let c = 0; c < k; c++) {
      lo = Math.min(lo, x[c] - r[c] * shrink, y[c] - r[c] * shrink);
      hi = Math.max(hi, x[c] + r[c] * shrink, y[c] + r[c] * shrink);
    }
    const span = Math.max(hi - lo, 1e-6);
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
    const cc = model.layouts[layout].centroids;
    // In "zeit" bleibt die Waagerechte, wie sie ist -- auch das
    // Zusammenziehen zum Schwerpunkt faellt weg. Es zoege jeden Kontinent
    // auf sein mittleres Aufnahmedatum zusammen und machte aus vierzehn
    // Jahren sieben.
    const x = timeLocked() ? bx : spreadPoint(bx, cc[c * 2], spread) + spreadDx[c];
    return [x, spreadPoint(by, cc[c * 2 + 1], spread) + spreadDy[c]];
  }

  /* Wo der Name eines Kontinents steht -- in der Anordnung, die gerade gilt.

     Bisher stand hier c.x/c.y aus atlas.json, und das ist das UMAP-Ergebnis,
     also die Bedeutungs-Anordnung. In "zeit" zeigte der Name damit auf eine
     Stelle, die dieser Kontinent nur in der *anderen* Karte hat: gemessen im
     Mittel 0,274 Kartenbreiten daneben, nach der Bandbelegung rund zehn
     Jahre. Die Hoehe stimmte zufaellig, weil byTime dieselbe y-Achse
     durchreicht -- das Jahr nicht. Und weil der Regler daran nichts aendert,
     war es schon in der Grundeinstellung falsch.

     Der Schwerpunkt je Anordnung liegt fertig vor (model.js, centroidsOf). */
  function clusterAnchor(ci) {
    const cc = model.layouts[layout].centroids;
    return spreadAt(cc[ci * 2], cc[ci * 2 + 1], ci);
  }

  function applySpread() {
    if (!spread) return;
    ensureSpread();
    const cc = model.layouts[layout].centroids;
    const locked = timeLocked();
    for (let i = 0; i < model.n; i++) {
      const c = model.cl[i];
      if (!locked) px[i] = spreadPoint(px[i], cc[c * 2], spread) + spreadDx[c];
      py[i] = spreadPoint(py[i], cc[c * 2 + 1], spread) + spreadDy[c];
    }
  }

  function draw() {
    const now = performance.now();
    const entfaltet = ensureBlast();
    const animating = positions(now);
    stats.drawn = 0;
    stats.budget = 0;
    stats.off = "";
    shown.length = 0;
    const w = canvas._w, h = canvas._h;
    ctx.fillStyle = "#12151a";
    ctx.fillRect(0, 0, w, h);

    // Nicht in der Serien-Ebene: dort liegen die Kacheln auf den
    // UMAP-Koordinaten ihres Ereignisses und tragen gar kein Datum. Eine
    // beschriftete Jahresachse darunter ist nicht ungenau, sie gilt nicht.
    if (layout === "zeit" && mix > 0.5 && mode !== "serien") drawYearBands();

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
    if (animating || entfaltet) schedule();
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
  function fanOut(cand, minDist, w, h, limit, originX, originY) {
    const md2 = minDist * minDist;
    const n = cand.length;
    if (n < 2) return;
    /* Ausgangslage merken, um die Verschiebung begrenzen zu koennen.

       Wird eine mitgegeben, gilt sie: seit die Relaxation mit der
       Verschiebung des Vorframes startet, ist ihr eigener Startwert nicht
       mehr die wahre Lage des Fotos, und gegen die muss die Begrenzung
       rechnen -- sonst wandert eine Kachel ueber viele Frames beliebig weit
       ab, jedes Mal um weniger als das Limit. */
    const ox = originX || new Float32Array(n), oy = originY || new Float32Array(n);
    if (!originX) for (let a = 0; a < n; a++) { ox[a] = cand[a][2]; oy[a] = cand[a][3]; }

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
        let seen = 0, treffer = 0;
        const ax = cand[a][2], ay = cand[a][3];
        let schubX = 0, schubY = 0;
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
              schubX -= (ddx / d) * push;
              schubY -= (ddy / d) * push;
              treffer++;
              moved++;
            }
          }
        }
        /* Der Mittelwert der Schuebe, nicht ihre Summe -- daran hing das
           Wabbeln.

           Summiert weicht ein Bild jedem Nachbarn einzeln um die volle halbe
           Fehldistanz aus. In einem dichten Haufen sind das zwoelf Schuebe in
           einem Durchgang, zusammen bis zum Sechsfachen des Sollabstands. Es
           schiesst weit ueber, die Begrenzung zieht es zurueck, im naechsten
           Frame steht die Nachbarschaft anders -- das ist keine Annaeherung,
           sondern ein Grenzzyklus. Er endet nie von selbst.

           Gemessen bei stehender Kamera, Massstab 9000, ueber 200 Frames:
           summiert 0,94 px je Kachel und Frame, dauerhaft. Gemittelt 0,025,
           und ab Frame 49 steht die Karte still. Waehrend eines Schwenks
           faellt der groesste Schirmsprung von 6,2 px auf 0,8 px.

           Der Mittelwert zeigt in dieselbe Richtung wie die Summe, nur mit
           einer Schrittweite, die eine Loesung anlaeuft statt sie zu
           ueberspringen. Ueber sechs Durchgaenge kommt derselbe Abstand
           heraus. */
        if (treffer) {
          cand[a][2] = ax + schubX / treffer;
          cand[a][3] = ay + schubY / treffer;
        }
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
    let size = box * (window.devicePixelRatio || 1) > 160 ? 320 : 160;

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

    /* Der Schirm nimmt den Abstand, den die Welt hergibt -- und laesst die
       Kachel mitschrumpfen, statt Fotos fallen zu lassen.

       Beobachtet nach der Weltspreizung: ganz nah war jedes Foto eine
       Kachel, eine Zoomstufe hoeher fielen dieselben Fotos zurueck auf
       Punkte. Der Grund war die Kapazitaetsrechnung mit dem *Wunsch*abstand:
       der haengt an der Kachel, die Kachel schrumpft beim Herauszoomen kaum,
       also traegt das Fenster immer weniger Bilder -- obwohl die Welt
       laengst garantiert, dass jedes Paar BLAST_D auseinanderliegt.

       Diese Garantie ist auf dem Schirm BLAST_D mal Massstab. Wer sie als
       wirksamen Abstand uebernimmt, bekommt eine Kapazitaet, die auf jeder
       Zoomstufe fuer alle Sichtbaren reicht: 0,9 / 0,866 ist groesser als
       eins, mehr als dicht an dicht liegt in der Welt nichts. Die Kachel
       wird dann hoechstens so gross wie dieser Abstand -- der Zoom ist das
       Groessenrad, der Regler das Luftrad: ueber 1,0 kauft er Luft, indem
       die Kachel weiter schrumpft, nie indem Fotos verschwinden.

       Punkte bleiben genau zweimal ehrlich uebrig: unterhalb der
       Bildschwelle (BLAST_D * 2600 sind 16 px -- kleiner waere ohnehin
       nichts zu erkennen) und wo das Leistungsbudget deckelt. */
    const repel = declutter > 0 && thumbMode !== "alles";
    let gapEff = gap;
    if (repel) {
      /* Nicht aus der Weltgarantie hergeleitet, sondern aus der Zaehlung:
         soviel Abstand, dass die Kapazitaet fuer alle Sichtbaren reicht --
         das gilt per Konstruktion, unabhaengig davon, wie weit die
         Relaxation der Welt schon gekommen ist. Die Weltspreizung sorgt
         dafuer, dass die Sichtbarenzahl beim Hineinzoomen faellt und die
         Kachel dadurch waechst; erst zusammen ergibt beides "auf jeder
         Stufe alles, und je naeher, desto groesser". */
      const anz = Math.max(1, Math.min(near.length, budget));
      gapEff = Math.max(10, Math.min(gap, Math.sqrt((FAN_PACK * w * h) / anz)));
      const kachel = Math.max(10, gapEff / Math.max(1, declutter));
      if (kachel < tileBox) {
        tileBox = kachel;
        stats.tile = Math.round(kachel);
        size = kachel * (window.devicePixelRatio || 1) > 160 ? 320 : 160;
      }
    }

    /* Was das Abstossen bestimmt -- ohne Kameralage. Aendert sich der Regler,
       der Kachelmodus, die Anordnung oder die Auswahl, sind die alten
       Verschiebungen sinnlos. Ein Schwenk dagegen ist genau der Fall, fuer
       den sie da sind. */
    const einstellung = [thumbMode, declutter, tileScale, layout, spread,
                         maskVersion, Math.round(gapEff)].join("|");
    if (einstellung !== fanKey) {
      fanKey = einstellung;
      fanned.clear();
      fanKern = new Set();
      unruhe = 0;
      verschoben = 0;
    }

    /* Was mit dem Abstand geschieht -- und das ist der Unterschied, um den
       es geht.

       Steht "Bilder entzerren" auf aus, bleibt jedes Foto an seiner Stelle.
       Passen nicht alle hin, wird ausgeduennt: das ist eine Auswahl, keine
       Entzerrung, und an der Ueberlappung der Uebriggebliebenen aendert sie
       nichts.

       Steht der Regler auf einem Wert, stossen die Bilder einander ab, bis
       der Abstand steht -- wie die Knoten eines Kraftgraphen. Gezeigt wird,
       was das Fenster bei diesem Abstand traegt (FAN_PACK); der Haufen
       schiebt sich dafuer in den leeren Raum daneben. */
    const grenze = gapEff * FAN_REACH;
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

    // Der Radius ist waehlbar, damit die Siebe des Abstoss-Zweigs dasselbe
    // Raster benutzen koennen -- aber nie groesser als gap, sonst reicht
    // der 3x3-Ring nicht.
    function free(sx, sy, r2 = gap2) {
      const gx = Math.floor(sx / gap), gy = Math.floor(sy / gap);
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          const list = taken.get(key(gx + dx, gy + dy));
          if (!list) continue;
          for (let k = 0; k < list.length; k += 2) {
            const ddx = list[k] - sx, ddy = list[k + 1] - sy;
            if (ddx * ddx + ddy * ddy < r2) return false;
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
      /* Wieviele, sagt die Kapazitaet. Wer, sagen die Siebe. Wohin, das
         Auffaechern. Und wer einmal dabei ist, bleibt dabei.

         Ohne das Letzte wechselte beim Schwenken je Frame ein Drittel der
         Auswahl: die Siebe greifen in der Reihenfolge des Abstands zur
         Bildmitte, und die permutiert bei jedem Pixel Kameralage. Gemessen
         ueber einen Schwenk von 240 px: 79 Wechsel je Frame ohne den Kern,
         0,2 mit ihm -- und nach dem Halt steht dieselbe Guete wie beim
         Kaltstart, Nachbarabstand 0,98 mal gap nach 1,6 s.

         Kernmitglieder raeumen ihren Platz erst, wenn sie aus dem Fenster
         fallen oder die Kapazitaet sinkt; sie belegen das Raster, damit
         Neuzugaenge zu ihnen Abstand halten. Der Punkt unter jedem Bild
         bleibt liegen. */
      /* Und zwar so, dass es dabei zur Ruhe kommt.

         Bis hierher fing jeder Frame bei null an: `fanOut` startete an der
         wahren Bildschirmlage und rechnete die ganze Wolke neu. Bei
         stehender Kamera kommt zweimal dasselbe heraus -- deshalb steht ein
         ruhendes Bild still. Sobald sich aber irgendetwas ruehrt, ist das
         Ergebnis keine stetige Funktion der Eingabe mehr: die Reihenfolge
         haengt am Abstand zur Bildmitte und permutiert bei jedem Pixel, das
         Rasterfeld verschiebt sich gegen die Punkte, und der Nachbardeckel
         schneidet nach Begegnungs- statt nach Naehereihenfolge ab. Jede der
         drei Unstetigkeiten allein genuegt, damit ein Schwenk um ein Pixel
         eine Kachel um zwanzig springen laesst.

         Die Reparatur ist nicht, die Relaxation stetig zu machen -- das ist
         sie ihrer Natur nach nicht. Sie besteht darin, ihr einen Startwert
         zu geben: die Verschiebung aus dem Vorframe. Dann rechnet sie nicht
         eine neue Loesung aus, sondern zieht die vorhandene nach, und aus
         dem Sprung wird eine Bewegung.

         Dasselbe Mittel, das der `spaced`-Zweig mit `held` schon benutzt:
         `fanned` traegt die Lage von Frame zu Frame, der Kern die Auswahl. */
      const kap = Math.min(budget,
        Math.max(24, Math.floor((FAN_PACK * w * h) / (gapEff * gapEff))));
      const cand = [];
      const drin = new Uint8Array(near.length);
      for (let a = 0; a < near.length && cand.length < kap; a++) {
        if (!fanKern.has(near[a][1])) continue;
        drin[a] = 1;
        claim(near[a][2], near[a][3]);
        cand.push(near[a].slice());
      }
      for (const teiler of FAN_SIEBE) {
        if (cand.length >= kap) break;
        const r2 = teiler === Infinity ? 0 : (gapEff / teiler) ** 2;
        for (let a = 0; a < near.length && cand.length < kap; a++) {
          if (drin[a]) continue;
          if (r2 && !free(near[a][2], near[a][3], r2)) continue;
          drin[a] = 1;
          claim(near[a][2], near[a][3]);
          cand.push(near[a].slice());
        }
      }
      fanKern = new Set();
      for (const c of cand) fanKern.add(c[1]);
      // Die wahre Lage getrennt halten: die Begrenzung der Verschiebung
      // muss sich auf sie beziehen, nicht auf den mitgebrachten Startwert.
      const trueX = new Float32Array(cand.length);
      const trueY = new Float32Array(cand.length);
      for (let a = 0; a < cand.length; a++) {
        trueX[a] = cand[a][2]; trueY[a] = cand[a][3];
        const d = fanned.get(cand[a][1]);
        if (d) { cand[a][2] += d[0]; cand[a][3] += d[1]; }
      }
      const tFan = performance.now();
      fanOut(cand, gapEff, w, h, grenze, trueX, trueY);
      fanMs = performance.now() - tFan;

      /* Gedaempft uebernehmen, nicht roh.

         Nicht mehr gegen ein Zittern -- das hatte zwei andere Ursachen und
         ist dort behoben, in der Auswahl und im Mittelwert der Schuebe. Was
         bleibt, ist der Zweck, fuer den die Daempfung ohnehin taugt: die
         Relaxation ist keine stetige Funktion der Kameralage, ein Schwenk um
         ein Pixel kann eine Kachel um mehrere springen lassen. Ein Drittel je
         Frame macht daraus eine Bewegung.

         Gemessen ueber einen Schwenk von 240 px: groesster Schirmsprung
         0,8 px, Restzappeln nach dem Anhalten 0,11 px. */
      let unruheSumme = 0, unruheZahl = 0, wegSumme = 0, bewegt = 0;
      const naechste = new Map();
      for (let a = 0; a < cand.length; a++) {
        const zielX = cand[a][2] - trueX[a], zielY = cand[a][3] - trueY[a];
        /* Wer noch keinen Vorframe hat, beginnt bei null Verschiebung und
           east von dort -- der Einstieg ist Teil der Bewegung, kein Sprung.

           Vorher nahm ein neues Bild sofort die rohe Loesung an und zaehlte
           nicht als bewegt. Nach dem Einschalten war deshalb der allererste
           Frame zugleich der letzte: nichts galt als in Bewegung, kein
           weiterer wurde angefordert, und die Karte fror mitten im
           Aufblasen ein -- beobachtet als 168 gezeichnete bei 276 Plaetzen
           und im Mittel 12 statt 25 px Verschiebung. Aufgefallen ist es
           erst mit vollem Bildspeicher: solange Bilder nachluden, stiess
           jedes fertige Bild den naechsten Frame an und deckte den Fehler
           zu. */
        const vor = fanned.get(cand[a][1]) || [0, 0];
        let dx = vor[0] + (zielX - vor[0]) * FAN_EASE;
        let dy = vor[1] + (zielY - vor[1]) * FAN_EASE;
        const weg = Math.hypot(dx - vor[0], dy - vor[1]);
        // Unter der Schwelle genau liegen bleiben, nicht fast: nur der
        // unveraenderte Wert macht den naechsten Frame identisch und damit
        // das Stillstehen endgueltig.
        if (weg < FAN_RUHE) { dx = vor[0]; dy = vor[1]; }
        else { bewegt++; unruheSumme += weg; }
        unruheZahl++;
        // Was uebernommen wurde, muss auch gezeichnet werden.
        cand[a][2] = trueX[a] + dx;
        cand[a][3] = trueY[a] + dy;
        naechste.set(cand[a][1], [dx, dy]);
        wegSumme += Math.hypot(dx, dy);
      }
      fanned = naechste;
      unruhe = unruheZahl ? unruheSumme / unruheZahl : 0;
      verschoben = cand.length ? wegSumme / cand.length : 0;
      /* Solange sich noch etwas ruehrt, den naechsten Frame anfordern --
         sonst friert die Bewegung auf halbem Weg ein, wenn nichts anderes
         mehr zeichnen laesst.

         Gefragt wird nach der Anzahl, nicht nach dem Mittelwert. Der
         Mittelwert faellt schon unter die Schwelle, waehrend einzelne
         Kacheln noch weit wandern -- die blieben dann auf halbem Weg
         stehen, bis zufaellig etwas anderes einen Frame ausloest. */
      if (bewegt) schedule();

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
    stats.gap = repel ? Math.round(gapEff) : (spaced ? Math.round(gap) : 0);
    stats.gapReason = repel ? "abstoßend" : (spaced ? "Fläche" : "");
    /* Was tatsaechlich verschoben wurde, nicht die Obergrenze.

       Frueher stand hier `shift`, und solange das eine Weltdistanz war,
       war es auch eine Aussage. Seit die Grenze ein festes Vielfaches von
       `gap` ist, steht sie schon zwei Angaben weiter links -- die Zahl
       waere nur noch Arithmetik. Interessant ist, wieviel die Karte sich
       bei diesem Spielraum wirklich herausnimmt: gemessen 13 bis 30 px,
       also weit unter dem Erlaubten. Wer wissen will, ob die Karte ihm
       etwas vormacht, liest genau das. */
    stats.verschoben = repel ? Math.round(verschoben) : 0;
    stats.fanMs = Math.round(fanMs * 10) / 10;
    stats.unruhe = repel ? Math.round(unruhe * 10) / 10 : 0;
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
      const [wx, wy] = clusterAnchor(c.i);
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
      const [wx, wy] = clusterAnchor(c.i);
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

  /* Welche Taste welches Lasso zieht.

     Der Umschalter in der Leiste hatte einen Preis, der ihn fast unbrauchbar
     machte: solange er an war, zog *jeder* Zug ein Lasso, und schwenken ging
     nicht mehr. Man musste ihn also fuer jede Auswahl an- und danach wieder
     ausschalten.

     Jede Variante haengt deshalb an ihrer eigenen Taste. Ohne Taste schwenkt
     ein Zug immer -- das ist die haeufigste Handlung und braucht keine
     Vorbereitung.

       Umschalt  neu waehlen (ersetzt)
       Strg      dazunehmen
       Alt       abziehen
       Strg+Alt  eingrenzen: nur was schon gewaehlt war

     Die Bedeutungen sind dieselben wie vorher; neu ist nur, dass sie das
     Lasso auch *starten*. */
  const lassoKeys = (e) => e.shiftKey || e.ctrlKey || e.metaKey || e.altKey;

  canvas.addEventListener("mousedown", (e) => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (lassoMode || lassoKeys(e)) {
      // Sonst markiert der Zug Text oder zieht das Bild als Objekt mit.
      e.preventDefault();
      lassoPath = [[sx, sy]];
      // Die Tasten beim Anfassen festhalten: laesst man Strg vor der
      // Maustaste los, waere die Absicht sonst pluetzlich eine andere.
      drag = { kind: "lasso", mods: { add: e.ctrlKey || e.metaKey, subtract: e.altKey } };
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
    const kurz = !lassoPath || lassoPath.length <= 2;
    if (drag.kind === "lasso" && !kurz) {
      // Beim Anfassen *oder* beim Loslassen gedrueckt zaehlt. Wer mitten im
      // Zug noch Strg dazunimmt, meint es; wer es vorher losgelassen hat,
      // meinte es auch.
      hooks.onLasso?.(pickInPath(lassoPath), {
        subtract: drag.mods.subtract || e.altKey,
        add: drag.mods.add || e.ctrlKey || e.metaKey,
      });
    } else if (drag.kind === "lasso" && kurz) {
      /* Ein Lasso ohne Zug ist ein Klick.

         Strg+Klick heisst "mehr davon" -- das darf nicht verschwinden, nur
         weil Strg jetzt auch ein Lasso startet. Wer nicht gezogen hat, wollte
         klicken. */
      const r = canvas.getBoundingClientRect();
      const sx = e.clientX - r.left, sy = e.clientY - r.top;
      const i = mode === "serien" ? -1 : nearest(sx, sy);
      if (i >= 0) (e.ctrlKey || e.metaKey ? hooks.onThread : hooks.onPick)?.(i);
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
      /* Aus den *rohen* Koordinaten der bisherigen Anordnung starten, nicht
         aus px. In px steckt die Spreizung schon drin, und positions() legt
         nach dem Mischen noch einmal applySpread darueber -- die Spreizung
         wirkte also doppelt, sichtbar als Sprung zu Beginn des Uebergangs.
         Bei Regler 0 fiel das nicht auf, weil applySpread dann nichts tut. */
      fromX = Float32Array.from(model.layouts[layout].x);
      fromY = Float32Array.from(model.layouts[layout].y);
      layout = name;
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
    focusCluster(c) {
      // Denselben Anker wie Beschriftung und Trefferpruefung, sonst fliegt
      // die Kamera woandershin, als der Name steht.
      const [wx, wy] = clusterAnchor(c.i);
      flyTo(wx, wy, 5200);
    },
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
