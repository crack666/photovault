---
titel: Fertigstellungs-Plan
stand: 2026-09-01
zweck: Auftragsbeschreibung fuer eine Ultracode-Session
---

# PhotoVault — Plan zur Fertigstellung

PhotoVault wird produktiv benutzt und ist nicht fertig. Viele Funktionen sind da und tun
"so halb". Dieser Plan sagt, was davon belegbar kaputt ist, in welcher Reihenfolge es
angefasst gehoert und warum genau in dieser.

**Fokus der Session: Benutzbarkeit und Gestaltung. Keine neuen Funktionen.**

## Stand 2026-09-01

| Stufe | Zustand |
|---|---|
| 0 — Vorarbeiten | **erledigt** (`842c21a`) |
| 1 — Atlas, Zeit-Anordnung | **erledigt** (`a47d87b`). 1.3 entfiel: wenn x festgehalten wird, stimmen die Bänder von selbst |
| 2 — Atlas, Flackern | **gebaut** (`f87fb73`), Nachmessung offen — siehe unten |
| 3 — `app.js` schneiden | **3.1–3.3 erledigt** (`8468ff7`, `541fb64`, `df35faf`). app.js 2938 → 2491 Zeilen. Offen: 3.4 Galerie, 3.5 Serien, 3.6 Suche/Personen/Unbekannte |
| 4 — Datenschicht | 4.1–4.6 **erledigt** (`aa846ec`, `45608fb`, `01c26d8`); 4.7 offen |
| 5 — Gestaltung | **angefangen** (`863c863`): Tokens, Hauptknopf, Fingergrößen. Offen: Knopf-Bibliothek, Skalen, `jobs.html` |

**Entwurfsentscheidung getroffen:** In der Zeit-Anordnung gehört x dem Datum.
Ausgewichen wird nur senkrecht, und nur zwischen Kontinenten, die sich waagerecht
überschneiden. Die Karte wird dadurch nicht höher — die Rückstauchung rechnet in dieser
Anordnung nur über y, die Kontinente werden also senkrecht dünner.

**Was in Stufe 2 noch fehlt:** die Nachmessung. Die neue Kennzahl *Unruhe* steht in der
Aufwand-Anzeige (mittlere Änderung der Verschiebung je Kachel und Frame). Vor der Dämpfung
stand sie bei **5,5 px trotz stehender Kamera**. Der Wert danach ist nicht abgenommen: die
Vorschau war ausgeblendet, und ohne sichtbares Fenster feuert `requestAnimationFrame`
nicht, der Canvas zeichnet also gar nicht. Prüfen: Atlas öffnen, *Aufwand* und *Bilder
abstoßen* einschalten, ziehen. Bei stehender Kamera gehört die Zahl nahe null.

### Zum Schnitt: die Reihenfolge ist enger als hier notiert

Fünf parallel erhobene Spezifikationen waren einzeln sauber und zusammen nicht
anwendbar — sie beschrieben alle `app.js` **wie es heute ist**, nicht wie es nach den
vorherigen Schnitten aussieht. Echt zu entscheiden war nur zweierlei, und beides ist
entschieden:

- **Die reinen Textbausteine gehören `core/format.js`, nicht der Galerie.** Hauptaufrufer
  zu sein begründet kein Eigentum; Reinheit tut es. Die Galerie importiert sie.
- **`eventWhen` und `evWhen` werden zusammengelegt**, mit `{ unknown = "" }`. Der
  Standard muss leer bleiben: die Galerie ruft die Funktion nur über `eventMeta`, und das
  schiebt jede nichtleere Rückgabe in die Metazeile.

Daraus folgt eine harte Reihenfolge, die im Plan fehlte: **3.4 Galerie muss vor 3.5
Serien**, weil der Serien-Block `bindShotStrip` braucht und das bis dahin in `app.js`
liegt. Andernfalls bräuchte 3.5 eine Injektion — genau die Sorte Umweg, die 3.3 gerade
abgeschafft hat.

**Drei Sicherheitsfehler, gefunden beim Schreiben der fehlenden Tests** (`cd66b7f`,
`b51afa5`): der Papierkorb liess sich mit expliziten Kennungen vollständig überspringen;
der Löschpfad gab den Pfad aus dem Payload ungeprüft an `unlink`; und die Albumliste
hielt die Freigabe selbst für ein Album, dessen Umbenennen die ganze Sammlung verschoben
hätte. Alle drei behoben, alle drei durch Tests abgedeckt.

**Was beim Bauen zusätzlich gefunden wurde** (stand nicht im Plan):

- Das Blättern der Suche hätte gar nicht funktioniert. Der `offset` von `scroll` ist ein
  Punkt-Cursor, keine Zahl — Seite zwei lieferte dieselben Fotos wie Seite eins. Ein Pager
  davor hätte stumm nichts getan.
- Der Startfehler bei `?tab=events` ist bestätigt, nicht nur hergeleitet: `queue-meta` blieb
  leer, weil `loadQueue()` nie erreicht wurde.
- Die gemeinsame Rückstauchung über beide Achsen ist in der Bedeutungs-Anordnung **kein**
  Fehler, anders als hier zunächst notiert. Dort sind x und y zwei Richtungen derselben
  Einbettung; wer sie einzeln staucht, verzerrt genau die Abstände, um die es geht.

## Herkunft und Geltung

Alle Befunde stammen aus dem Lesen des Codes am 01.09.2026, keiner aus einem laufenden
Browser. Was gelesen ist, ist als solches belegt (Datei:Zeile). Zwei Befunde zum Flackern
wurden zusaetzlich von zwei unabhaengigen Gegenlesern angegriffen; wo sie widersprochen
haben, steht der Widerspruch drin und nicht die urspruengliche Fassung.

Die Zahlen im Abschnitt Zeit-Anordnung wurden gegen die vorliegende `atlas.json`
nachgerechnet (14.887 Fotos, 40 Kontinente).

## Lagebild

| | Zeilen | Zustand |
|---|---|---|
| Frontend `web/` | 9.751 | ein Modul zu gross, Rest bereits geschnitten |
| Backend `api/` `ingest/` `tools/` `tests/` | 19.803 | Struktur tragfaehig, Datenschicht fehlerhaft |

Der Schnitt ist **kein Neuentwurf**. `core/`, `atlas/`, `faces/` und `trash/` sind bereits
eigene Module mit einwandfreier Abhaengigkeitsrichtung; `app.js` (2.880 Zeilen) ist der
Rest, aus dem noch nicht extrahiert wurde. Es gibt keinen Zyklus in der Datei und keinen
von aussen: `app.js` wird von niemandem importiert und setzt keine `window`-Globals.

Der Atlas haengt nicht an diesem Schnitt. `atlas/` importiert ausschliesslich aus `core/`;
`app.js` erreicht ihn nur per dynamischem Import (`app.js:32`). **Atlas-Arbeit und
app.js-Schnitt blockieren einander nicht** und koennen getrennte Sitzungen sein.

---

## Stufe 0 — Vorarbeiten, ohne die jede Messung luegt

Diese vier zuerst. Sie kosten fast nichts und verhindern, dass die Session Phantomen
nachjagt.

### 0.1 Cache-Nummern vereinheitlichen

`index.html` laedt `app.js?v=53`, alle 23 Modulimporte stehen auf `?v=33`. `styles.css`
laeuft als `v=11` in `index.html` gegen `v=9` in `jobs.html`, `ui.css` als `v=12` gegen
`v=3`. Acht divergente Werte.

Der Code verlangt das Gegenteil ausdruecklich (`app.js:24-28`): *"Die Zahl gilt fuer alle
Module gemeinsam und wird gemeinsam erhoeht"* — und *"Genau das ist passiert"*, das
Projekt wurde davon schon einmal gebissen.

Solange das offen ist, kann jeder Fehlerbericht ein Cache-Artefakt sein. **Erst hier
aufraeumen, dann alles andere beurteilen.**

### 0.2 Startfehler bei `?tab=events`

`app.js:693` ruft `showTab(tabFromUrl())`, das bei `tab=events` ueber `loadEventTab`
(`app.js:1956`) die Variable `evTab` liest — deklariert erst in `app.js:1943` mit `let`.
Temporal Dead Zone: der Zugriff wirft, bevor die Deklaration erreicht ist.

Folge: die Modulauswertung bricht in Zeile 693 ab. Alles danach wird nie gebunden —
Queue-Start (`app.js:1931`), Lightbox-Listener (ab `app.js:1500`), Serien, Datalists
(`app.js:2879`).

Der Weg dorthin ist real: `core/nav.js:35` verlinkt von der Jobs-Seite genau so.

*Sofortmassnahme:* Deklarationen 1942-1954 vor Zeile 693 ziehen, oder Zeile 693 ans
Dateiende. Stufe 3.5 (Serien als eigenes Modul) behebt es danach von selbst.

*Nicht im Browser nachgestellt — die Ableitung stammt aus der Auswertungsreihenfolge.*

### 0.3 Bedienelement-Grundlage

Groesster sichtbarer Effekt pro Zeile im ganzen Plan.

- **Kein `font: inherit` fuer Bedienelemente.** Einziger globaler Reset ist
  `* { box-sizing: border-box }` (`styles.css:10`). `nav.js:33-34` erzeugt auf
  `index.html` ein `<button class="tab">` und auf `jobs.html` ein `<a class="tab">` —
  der Link erbt 16px system-ui, der Knopf faellt auf die Formularschrift des Browsers.
  Dieselbe Kopfleiste, zwei Schriften. Ebenso das zentrale Namensfeld "Wer ist das?"
  (`styles.css:62-65`).
- **Kein `color-scheme`.** Null Treffer im ganzen `web/`-Baum. Alle nativen Elemente
  rendern hell: Bildlaufleisten, jede Aufklappliste, alle Ankreuzfelder, und vor allem
  der Kalender von `input[type=date]` — auf einer sonst durchgehend dunklen Oberflaeche.
- **`accent-color` nur an zwei Stellen** (`atlas.css:314`, `styles.css:601`). Alle
  uebrigen Ankreuzfelder bekommen Systemblau; im selben Dialog stehen zwei Blautoene.
- **Fokus.** Vier `:focus`-Regeln in fuenf Stilquellen, drei davon *entfernen* den
  Umriss. `:focus-visible` kommt kein einziges Mal vor.

### 0.4 Toter Zustand raus

`lastGallery` (`app.js:1002`, einzige Zuweisung `app.js:1081`) wird nie gelesen. Muss in
kein Modul mitwandern. `fits` (`scene.js:932`) wird berechnet und nie gelesen; der
Kommentar `scene.js:893-903` beschreibt eine Rechnung, die nicht mehr stattfindet.

---

## Stufe 1 — Der Atlas: zeitliche Anordnung

Der Atlas ist die Funktion, die den Unterschied macht. Hier zuerst polieren, nicht
anderswo. Die Reihenfolge unten ist erzwungen, nicht gewaehlt.

### 1.1 Kontinent-Beschriftungen an die aktive Anordnung koppeln — **klein, wirkt sofort**

Die Beschriftungen stehen in der Zeit-Anordnung auf der x-Koordinate der
*Bedeutungs*-Anordnung. `drawClusterLabels` (`scene.js:1171`) und `labelAt`
(`scene.js:1141`) nehmen `c.x/c.y` aus `atlas.json`, und das ist das UMAP-Ergebnis
(`tools/atlas_build.py:594`). Fuer "zeit" legt `model.js:60/63` eigene Zentroide an — die
werden fuer Beschriftungen nie benutzt. Die Hoehe stimmt zufaellig, das Jahr nicht.

Alle 40 Kontinente betroffen, Median 0,274 Kartenbreiten daneben, Maximum 0,438 — bei der
gemessenen Bandbelegung rund **10 Jahre**. Beispiel `cl19` ("monitor, gaming", echte Jahre
2025/2024): Label bei x=0,297 (Band 2011), Fotos bei x=0,735 (Band 2023).

**Das wirkt bereits bei Reglerstellung 0, also in der Grundeinstellung** (`index.js:565`).
Wahrscheinlich der groesste Einzelbeitrag zum Eindruck "stimmt nicht".

*Achtung:* `c.x/c.y` werden an drei Stellen benutzt (`scene.js:1141`, `:1171`, `:1410`) —
alle drei gleichzeitig umstellen, sonst zeigen Beschriftung, Hover-Treffer und Kamerafahrt
auf drei verschiedene Punkte.

### 1.2 Spreizung in "zeit" auf die Bedeutungsachse beschraenken — **gross, Entwurfsentscheidung**

Vier Befunde mit einer Wurzel: **die Spreizung ist achsen-blind**, obwohl in "zeit" die
x-Achse Chronologie traegt.

- `clusterRadii` (`scene.js:512-527`) bildet einen isotropen Kreisradius aus
  `dx*dx+dy*dy`. Gemessen: mediane x-Streuung 0,146 gegen y-Streuung 0,049, benutzter
  Radius 0,156. Die Kreise fordern senkrecht rund dreimal mehr Luft, als die Kontinente
  dort einnehmen — und weil sie sie nicht bekommen, weichen sie **waagerecht** aus, also
  durch die Chronologie. Rund die Haelfte der gesamten Zentroid-Verschiebung landet auf
  der Zeitachse.
- `spreadPoint` (`model.js:164-166`) zieht jeden Punkt zum x-Schwerpunkt seines
  Kontinents — in dieser Anordnung das mittlere Aufnahmedatum. Bei Regler 1,0 halbiert
  sich die zeitliche Ausdehnung jedes Kontinents. Der Kommentar `scene.js:495-500` nennt
  das korrekt "kompakter"; auf einer Bedeutungsachse ist das harmlos, auf einer Zeitachse
  ist es eine Falschaussage ueber das Aufnahmedatum.
- Die Renormierung (`scene.js:566-577`) mischt **beide Achsen in ein gemeinsames
  `lo`/`span`** und skaliert x und y mit demselben Faktor. Gemessen bei Regler 0,5:
  lo=-0,367, hi=1,485, span=1,852 — alle x-Werte werden durch 1,852 geteilt und um +0,198
  verschoben, waehrend `drawYearBands` weiter von 0 bis 1 zeichnet. **Das allein reicht
  fuer "stimmt nicht"**, ganz ohne den Abstoss-Anteil.
- Die Packung **konvergiert nicht**: die Schleife laeuft alle 60 Durchgaenge
  (`scene.js:540`, `SPREAD_ITERATIONS`), `moved` wird bei keinem der Reglerwerte 0,5 / 1,0
  / 2,0 jemals 0. Das gezeichnete Ergebnis ist ein willkuerlicher Zwischenstand — eine
  Handvoll Fotos mehr verschiebt die Kontinente sprunghaft.

Noetig: getrennte `rx`/`ry` in `clusterRadii`, achsenweise Bedingung in `buildSpread`,
getrennte `lo`/`span` je Achse in der Renormierung.

**Das ist eine Entwurfsentscheidung, keine Reparatur.** 40 Kontinente auf einer Hoehe von
0,049 koennen einander nicht allein senkrecht ausweichen — entweder wird die Karte sehr
hoch, oder das Versprechen "sie ueberdecken sich nicht mehr" (`index.js:566`) muss fuer
diese Anordnung zurueckgenommen werden. **Diese Entscheidung gehoert vor die Session.**

### 1.3 Jahresbaender durch dieselbe Umrechnung schicken — **mittel, haengt an 1.2**

`drawYearBands` (`scene.js:695-701`) zeichnet in den rohen Layout-Koordinaten, waehrend
die Punkte vorher durch `applySpread` gelaufen sind (`scene.js:606`). Die passende
Umrechnung existiert als `spreadAt` (`scene.js:595`) und wird fuer Beschriftungen und
Trefferpruefung bereits benutzt — die Baender sind die einzige Zeichnung, die sie auslaesst.

Gemessen bei Regler 0,5: **12.512 von 14.887 Fotos (84,0 %) liegen unter einem Jahresband,
das nicht ihr Jahr ist.** 36 von 40 Kontinent-Schwerpunkten wechseln das Band. Bei 1,0
sind es 85,1 %, bei 2,0 noch 77,2 %.

*Reihenfolge einhalten:* ein Band gehoert zu keinem Kontinent und hat kein `spreadDx`. Die
Umrechnung wird erst eindeutig, wenn 1.2 die x-Achse festhaelt.

### 1.4 Serien-Ebene und Zeit-Anordnung entkoppeln — **eine Zeile**

`scene.js:622` prueft nur `layout`, nicht `mode`. In der Serien-Ebene liegen also
Jahresbaender unter Kacheln, deren x-Position aus `atlas_build.py:430` die UMAP-Koordinate
ist und **gar kein Datum traegt**. Die irrefuehrendste Kombination im ganzen Atlas: hier
ist nichts "ein bisschen verschoben", die Achse gilt nicht.

Baender bei `mode === "serien"` weglassen. Eine Zeile, und sie luegt nicht mehr.

### 1.5 Kleinkram mit Zeitbezug

- **`jahrVon` ersatzlos streichen.** Drei Stellen machen aus `t` ein Jahr, eine
  widerspricht: `atlas_build.py:473-481` liefert `-1` fuer fehlendes Datum, `model.js:59`
  deutet das korrekt als "kein Jahr", `index.js:153` rechnet stur weiter und macht daraus
  **1969**. `index.js:162` (`if (model.t[i])`) laesst `-1` durch und wirft `t === 0`
  (1.1.1970) hinaus; `index.js:193` derselbe Fehler. `model.year` existiert und wird in
  `index.js:1017` schon richtig benutzt. Heute latent (kein `t <= 0` im Bestand), schlaegt
  zu, sobald undatierte Fotos dazukommen — und trifft echte Aufnahmen vor 1970.
- **Farbskala klemmen.** `model.js:210-212` hat 2002-2026 fest verdrahtet und begrenzt `h`
  nicht. Der Bestand beginnt exakt bei 2002, liegt also auf der Kante; ein Scan von 1998
  bekaeme h=237 (blauviolett), einer von 1990 h=302 (magenta) — optisch *neuer* als die
  blauen 2002er.
- **Uebergang aus ungespreizten Koordinaten starten.** `scene.js:1382` merkt sich die
  bereits gespreizten `px` als Startpunkt, `applySpread` laeuft danach erneut darueber.
  Sichtbarer Sprung zu Beginn jedes Anordnungswechsels mit aktiver Spreizung.

### Nicht reparierbar, nur erklaerbar

Zwei Dinge sehen nach Fehler aus und sind keiner:

- **Jahre ohne Fotos fehlen ersatzlos** (`model.js:97-118`). In der vorliegenden
  `atlas.json` fehlt 2021 vollstaendig (2020: 50 Fotos, 2022: 2.288); Band 2020 endet bei
  x=0,6395 und 2022 beginnt exakt dort. Die Flaechenaufteilung nach `Anzahl^0.4`
  (`model.js:94`) ist so gewollt und dokumentiert — aber wer Abstaende als Zeitabstaende
  liest, sieht einen Fehler. **Ein Hinweis in der Legende waere billiger als eine
  Umstellung.**
- **Nur 37,1 % der Fotos haben ein EXIF-Datum** (5.527 von 14.887, `atlas_build.py:456`).
  Jahresangaben ohne Tag landen exakt auf der linken Bandkante (`model.js:128`). Das
  erklaert Einzelfaelle im falschen Jahr, nicht den Gesamteindruck. Datenbefund, kein
  Layoutbefund.

---

## Stufe 2 — Der Atlas: das Flackern beim Abstossen

**Bevor irgendetwas gebaut wird: vier naheliegende Erklaerungen sind widerlegt.**

| Vermutung | Warum sie nicht traegt |
|---|---|
| Flache Kopie: `near.slice(0, budget)` teilt die inneren Arrays, `fanOut` mutiert sie | `near` ist eine frame-lokale `const` (`scene.js:824`), wird jeden Frame neu gefuellt (`:840`). Die Mutation ueberlebt den Frame nicht. Bleibt eine **Falle fuer spaeter**: wer hinter `:1027` auf `near` zugreift, bekommt aufgefaecherte statt echte Koordinaten. |
| Schwellwert `shift >= FAN_MIN_PX` kippt beim Zoomen | Kann nicht kippen. `showThumbs` verlangt `cam.scale > 2600` (`:631`, `:15`), also ist der kleinste moegliche `shift` 2600 x 0,004 = **10,4 px** gegen eine Schwelle von 2 (`:85`, `:89`). Die Bedingung ist tot. |
| Buchfuehrung `planned`/`covered`/`touched` leckt zwischen Frames | Symmetrisch und sauber (`:640` gegen `:862-864`), `planTile` ist der einzige Schreiber und setzt `covered[i]` und `touched.push(i)` immer gemeinsam (`:1010-1011`). |
| `animating` schaltet die Kachelebene | `animating` ist nur bei einer Anordnungs-Ueberblendung wahr (`:613`); `setDeclutter` (`:1396`) ruehrt `mix` nicht an. |

**Und der naheliegendste Reparaturgedanke ist ebenfalls widerlegt:** *"camKey groeber
rastern"* wuerde nichts helfen. `camKey` gattert die Neuberechnung gar nicht — der
if-Block `scene.js:917-929` setzt ausschliesslich `held`/`heldSet`/`heldGap`/`heldBudget`.
`fanOut` steht bei `scene.js:1025` **unbedingt** im repel-Zweig und laeuft in *jedem*
`draw()`. `camKey` friert nur `gap` und `budget` ein; die Koordinaten in `near` kommen
direkt aus `cam.tx/ty/scale` (`:838`). Ein gerastertes `camKey` liesse die Kacheln
weiterspringen.

### Was es ist

**Der repel-Zweig besitzt keine zeitliche Kohaerenz.** `fanOut` startet in jedem Aufruf
bei `ox/oy` = aktuelle Bildschirmlage (`:733-734`): kein Uebertrag aus dem Vorframe, keine
Daempfung, kein Einrasten. Der `held`-Stabilisator, den der Kommentar `:1035-1042`
ausdruecklich als Behebung des Flackerns beschreibt, wird **nur im `spaced`-Zweig**
benutzt (`:1045`) — und `spaced` schliesst `repel` aus (`:953`). Die Reparatur existiert
und ist in genau dem Modus unerreichbar, um den es geht.

Bei konstantem Zustand ist das Ergebnis bitgleich, deshalb steht ein ruhendes Bild still.
Sobald sich etwas aendert, wird die komplette Wolke neu gerechnet — und das Ergebnis ist
**keine stetige Funktion der Eingabe**. Drei Verstaerker:

1. **Sweep-Reihenfolge.** `near` ist nach Abstand zur Bildmitte sortiert (`:844`). Zwei
   fast gleich weit entfernte Kandidaten tauschen bei einem Pixel Schwenk den Platz, und
   das Verfahren ist ordnungsabhaengig — Gauss-Seidel: `:783` schreibt `cand[a]` erst am
   Ende von `a`s Durchgang zurueck, `:762` liest `cand[b]` fuer `b < a` also bereits
   aktualisiert. *Der Kommentar `:776-780` behauptet das Gegenteil ("die Reihenfolge
   spielt keine Rolle mehr") und ist falsch — beim Anfassen mitkorrigieren.*
2. **Rasterversatz.** Der Hash `floor(x / minDist)` (`:741`, `:747`) verschiebt sich beim
   Schwenken gegen die Punkte. Bei `gap` um 70 px wechselt pro Pixel rund 1,4 % aller
   Kandidaten das Feld und aendert damit die Nachbarlisten zweier Felder fuer alle darin.
3. **Nachbardeckel.** `FAN_NEIGHBOURS = 12` (`:102`) schneidet nach *Begegnungsreihenfolge*
   ab, nicht nach Naehe (`:754-761`, `seen++` steht vor dem Abstandstest). In einem dichten
   Haufen entscheidet die Listenreihenfolge, welche 12 von 300 Nachbarn schieben; ueber
   `FAN_ITERATIONS = 6` kaskadiert das.

**Streitpunkt zwischen den Gegenlesern, offen:** ob die *Eingaben* von `fanOut` — `gap`
(`:930`) und `budget` (`:931`) — schwerer wiegen als seine inneren Unstetigkeiten. `gap`
ist die Laengenskala des Kraftfelds und geht sowohl in die Schubweite als auch in die
Rasterteilung ein; beide werden aus `msPerThumb` abgeleitet, einem selbstmessenden
Regelkreis (`:904-906`, `:1092-1094`). Gegenargument: `budget` wirkt nur auf den Schwanz
von `cand`, weil `near` von der Mitte nach aussen sortiert ist, und `MS_RISE`/`MS_FALL`
(`:131-132`) wurden genau gegen dieses Schwingen eingebaut.

*Trennmessung vor dem Bauen:* pruefen, ob `gap` und `budget` beim langsamen Schwenken
konstant bleiben. Bei hinreichend grossem Regler ist das der Fall, weil dann
`wanted = box * declutter` ueber `cover` liegt (`:907`, `:908`, `:921`) und `box` nur an
`cam.scale` haengt. Bleiben sie konstant und flackert es trotzdem, tragen allein die
inneren Unstetigkeiten — und die Reparatur ist Positionsuebertrag plus Daempfung.

### Zwei weitere Flackerquellen, unabhaengig vom Regler

- **Andere Modi flackern auch.** `scene.js:1030`: `if (capped && drawn >= budget) break;`
  — und `drawn` wird erst *nach* `if (!img) return;` hochgezaehlt (`:1007-1008`). Die
  Abbruchgrenze zaehlt also nur **geladene** Bilder. Bei stehender Kamera wandert der
  Schnitt nach innen, waehrend Bilder eintreffen: bereits gezeichnete aeussere Kacheln
  fallen aus dem Budget, verschwinden, und ihre Punkte kommen zurueck (`:648`). Das ist
  exakt der Fehler, den der Kommentar `:1035-1042` fuer den `spaced`-Zweig als behoben
  beschreibt. Faellt im Modus "kegel" auf.
- **`camKey` kennt die Geometrie nicht.** Weder `canvas._w/_h` noch `devicePixelRatio`
  stehen darin (`:915-916`), obwohl beide in die Planung eingehen (`:822`, `:839`,
  `:798-799`, `:885`). `resize()` ruft `schedule()` (`:1419`), aendert `camKey` aber
  nicht — `held`, `heldGap` und `heldBudget` bleiben zur alten Geometrie eingefroren.

### Wie man das ueberhaupt beobachtet

Ein Test "Kamera stillhalten und schauen" trennt nichts: `planTile` setzt `covered[i]` nur
fuer geladene Bilder, jedes eintreffende Bild ruft selbst `schedule()` (`:443`), und
`resetQueue()` (`:861`) verwirft jeden Frame die noch nicht gestarteten Anfragen. Bei
`MAX_INFLIGHT = 16` (`:149`) und rund 190 ms je Vorschaubild dauert das Fuellen Sekunden —
man sieht weiter Kacheln an- und Punkte ausgehen.

**Erst warten, bis die Anzeige "N von M gezeichnet" und "Bilder im Speicher"
(`index.js:682-692`) stehen. Dann messen.**

---

## Stufe 3 — `app.js` zu Ende schneiden

Eigene Sitzung. Blockiert nichts aus Stufe 1 und 2.

Die Datei zerfaellt in 15 zusammenhaengende Bloecke; nur 7 Kommentar-Naehte sind gesetzt,
und **nur vier davon markieren echte Kanten**. Drei Features liegen in mehreren, weit
auseinander liegenden Stuecken.

| Block | Zeilen | Naht? |
|---|---|---|
| Kopf/Router | 1-41 | — |
| Unbekannt | 43-279 | ja, unvollstaendig |
| Suchformular | 281-687 | ja, unvollstaendig |
| Nav-Boot | 688-693 | — |
| Queue | 695-853 | — |
| Personenliste | 855-923 | — |
| Galerie | 925-1172 | ja |
| Fotoauswahl | 1174-1280 | ja, unvollstaendig |
| Lightbox | 1282-1633 | — |
| Panes + Personendetail | 1635-1802 | — |
| Suchlauf | 1804-1929 | — |
| Queue-Boot | 1931-1933 | — |
| Serien | 1936-2773 | ja, haelt vollstaendig |
| Kandidaten | 2775-2851 | ja |
| Datalists | 2846-2880 | ja |

**Naehte, die nicht halten:**

- Zeile 281 "Ausdrucks-Builder" deckt das Formular (286-687), der zugehoerige Suchlauf
  steht 1100 Zeilen weiter (1804-1929) ohne eigene Naht. `qbTree` wird in beiden Haelften
  benutzt (`:310`, `:1826`, `:1863`).
- Zeile 43 "Unbekannte Gesichter" deckt nur 47-279; der Kandidaten-Block 2775-2851 gehoert
  zum selben Tab, wird aus derselben Zeile geladen (`:17`) und ruft zurueck (`:2834`).
- Zeile 1174 deckt nur 1178-1280. Danach folgen Lightbox, Panes/Personendetail und
  Suchlauf ohne eigene Naht — **der groesste unbeschriftete Bereich der Datei**.
- Der Personen-Tab liegt auf **vier** Stellen verteilt (855-923, 1006-1021, 1635-1731,
  1733-1802) und laesst sich nur als Ganzes schneiden.

### Erzwungene Reihenfolge

**3.1 `core/format.js`** — reine Formatierer und Konstanten. Loest drei Bloecke
gleichzeitig voneinander: `MONTHS` aus Galerie und Serien, `CHANNEL_FILTERS` aus Galerie
und Serien, `faceStatsLine` aus Queue und Unbekannt (`:74`, `:102`, `:719`). Ohne diesen
Schritt importiert spaeter ein Feature-Modul aus dem anderen.
*Vorsicht:* `eventWhen` (`:954`) und `evWhen` (`:2020`) sind Beinah-Duplikate und
unterscheiden sich nur im Leerfall ("" gegen "Uhrzeit unbekannt"). Zusammenlegen mit
Parameter — der Serien-Text darf nicht in die Galerie durchschlagen.

**3.2 `core/names.js`** — Vorschlagslisten. Reines Blatt ohne Modulzustand, aus vier
Bloecken aufgerufen. Der Start-Aufruf (`:2879-2880`) bleibt im Router.

**3.3 `lightbox/index.js`** — **der eigentliche Schluessel.** `showLightbox` wird aus fuenf
Richtungen gebraucht (`:1150`, `:1342`, `:1926` plus die Injektionen `:33` und `:40`).
Kommt sie zuerst heraus, koennen Serien, Suche, Personen und Unbekannt sie danach einfach
statisch importieren — **und die zwei bestehenden `deps`-Injektionen fuer `atlas/` und
`trash/` koennen ersatzlos entfallen.** Bleibt sie drin, braucht jeder weitere Tab-Schnitt
eine neue Injektion.
*Vorher noetig:* `fillGallery` schreibt `lbPhotos` direkt (`:1117`, `:1132`) und liest es
wieder (`:1144`, `:1150`) — auf ein lokales Array umstellen, so wie `bindShotStrip`
(`:1342`) und `renderResults` (`:1926`) es bereits tun. Danach ist die Lightbox nach aussen
nur noch `showLightbox(liste, i)`. Die zwei dokumentweiten Listener (`:1623`, `:1624`)
gehoeren in ein `bindLightbox()` nach dem Vorbild `trash/index.js:203`.

**3.4 `gallery/index.js`** — Zeitstrahl, Kanalleiste, Bilderstrom, Shotstrip.

**3.5 `events/index.js`** — Serien, 1942-2773, rund 840 Zeilen. **Der sauberste
Grossschnitt:** saemtlicher Modulzustand wird nur innerhalb des Bereichs gelesen und
geschrieben. Nach aussen braucht der Block nur `MONTHS`, `CHANNEL_FILTERS`,
`bindShotStrip` und `refreshEventNames`. Behebt nebenbei den Startfehler aus 0.2.

**3.6 Danach erst** Suche, Personen, Unbekannt.
*Blocker:* `peopleCache` (`:307`) ist der schaedlichste geteilte Zustand — vier Bloecke
schreiben ihn, drei nur um ihn zu leeren und ein Nachladen zu erzwingen (`:198`, `:889`,
`:1776`, `:2833`). Sobald Suche und Personen getrennte Module sind, wirkt `peopleCache = []`
nicht mehr ueber die Grenze und die Namensliste im Suchtab veraltet still. **Braucht einen
eigenen Besitzer, bevor beide geschnitten werden.**

### Muster, die schon da sind — nicht neu erfinden

Zyklen werden heute auf zwei erprobte Arten vermieden: dynamischer Import plus Injektion
in einem `deps`-Objekt (`atlas/`, `trash/`), oder statischer Import plus Rueckweg als
Options-Callback (`faces/strip.js`). Welches passt, entscheidet sich daran, ob das Modul
eine App-Funktion dauerhaft braucht oder nur pro Aufruf. Auch "Feature-Modul will den Tab
wechseln" ist geloest: simulierter Klick (`:1849`), `showTab` muss nicht exportiert werden.

---

## Stufe 4 — Datenschicht: die Wurzeln von "tut nur halb"

Die Struktur des Backends ist tragfaehig — 12 Router, alle genau einmal registriert, kein
Router importiert einen anderen. Die Fehler liegen eine Ebene tiefer.

### 4.1 `space` wird beim Ingest nie geschrieben — **der Bereichs-Waehler ist deshalb leer**

`ingest/qdrant_writer.py:111-150` schreibt 30 Payload-Schluessel, `space` ist keiner davon.
Die einzigen Schreiber sind das Verschieben (`ingest/relocate.py:73-75`) und
`tools/backfill_spaces.py`. Nach einem frischen Ingest facettiert
`api/routes/search.py:357` ueber ein Feld, das es nicht gibt.

**Die Funktion sieht kaputt aus, obwohl nur ein Feld fehlt.** Genau das Muster, das der
Nutzer als "tut nur halb" beschreibt.

### 4.2 Payload-Indizes erreichen bestehende Installationen nie

`_ensure_collection` (`ingest/qdrant_writer.py:29-41`) kehrt vorzeitig zurueck, wenn die
Collection existiert — die Index-Schleife (`:55-69`) wird dann nie erreicht. Ein spaeter
hinzugekommener Index landet in einer bestehenden Installation nicht.

Der Schaden ist bereits eingetreten und wurde per Hand geflickt:
`tools/backfill_spaces.py:13-15` legt drei fehlende Indizes nach und nennt die Folge im
eigenen Docstring ("die Albumsuche lief als Full Scan").

Zusaetzlich fehlen `space` und `trashed_at` in der Indexliste **auch fuer frische
Installationen** — und `trashed_at` wird von *jeder* Anzeige-Abfrage gefiltert. Der
Papierkorb-Filter laeuft also unindiziert, bis jemand `tools.backfill_spaces` startet, was
nur `README:572` erwaehnt.

### 4.3 `space` und `trashed_at` fehlen in `PRESERVE_FIELDS`

`ingest/pipeline.py:638-647`. Ein Lauf mit `--no-resume` verliert beides wieder. Im
Normalfall greift `resume=True`, die Gefahr ist auf den ausdruecklichen Lauf beschraenkt —
aber die Liste wurde genau gegen diesen Fall gebaut.

### 4.4 Die Suche zeigt hoechstens 48 Treffer und hat keinen Weg zu Seite 2

`app.js:1866` sendet `limit: 48` und nie ein `offset`; `api/routes/search.py:216` setzt
`total = len(results)`, also die Seitenlaenge. Bei 5.000 passenden Fotos steht "48 Treffer
(erste Seite)" und es gibt keinen Knopf. Nur "Auf der Karte zeigen" (`:1823`, `ids_only`)
holt wirklich alle IDs.

**Der Pager existiert bereits** (`app.js:1983`) und wird in zwei anderen Tabs benutzt. Es
fehlt nur die Verdrahtung — und ein echter Zaehler im Backend.

Dieselbe Sache noch zweimal: "Benannte Serien" schneidet bei 200 ab (`app.js:2143`,
`events.py:378`), "Ganze Ordner" bei 400 (`app.js:2706`) — beide nennen in der Kopfzeile
die volle Zahl und zeigen einen Ausschnitt, ohne das zu sagen.

### 4.5 Das Freitext-Gate greift an einer von vier Stellen

Ohne Ollama ist das Freitextfeld gesperrt (`app.js:316`), aber zwei der fuenf
Beispielkacheln schreiben daran vorbei (`:609`, `:618`) und loesen einen **503** aus.
"Beschreibung setzen" und der Bulk-Caption-Knopf bleiben ebenfalls bedienbar, obwohl
`api/capabilities.py:94` und `:141` die passenden Eintraege berechnen — die im Frontend
niemand abfragt. Genau der Zustand, den der Kopfkommentar von
`api/routes/capabilities.py` als behoben beschreibt.

Dazu: jedes Speichern einer Caption oder eines Datums ruft **synchron** Ollama
(`photos.py:128`, `:189`, `:264`), ohne Gate und ohne Hinweis, Timeout 60 s.

### 4.6 Kleinere Ehrlichkeitsluecken

- `POST /api/ingest/start` meldet `"started"` und startet nichts — der ganze Rumpf ist ein
  `return` (`api/routes/ingest.py:18-20`).
- "Speichern" und "Behalten" in der Grossansicht tun dasselbe (`app.js:1503-1512` gegen
  `:1594-1604`, identischer Body).
- Der Reembed-Haken im Atlas wirkt auf "Notiz anhaengen" (`atlas/index.js:1310`), aber
  nicht auf "Beschreibung setzen" — dort wird immer neu eingebettet.
- "Unbekannte Person: X" in der Trefferliste ist toter Code: `renderResults` hat den
  Parameter (`:1892`), der einzige Aufruf (`:1889`) uebergibt ihn nicht. Ein nicht
  aufloesbarer Name ist der haeufigste Grund fuer "keine Treffer" — und die Erklaerung
  dafuer ist zu drei Vierteln gebaut.
- `POST /api/search` ist eine abgeloeste zweite Suchimplementierung mit demselben
  `total`-Fehler, ueber `/docs` erreichbar, mit anderen Ergebnissen als die UI.
- `/api/health` antwortet unbedingt `ok` (`api/main.py:59-61`) und wird von niemandem
  abgefragt. Faellt Qdrant nach dem Start aus, meldet die API weiter `ok`.
- `PHOTOVAULT_COLLECTION` respektieren neun Ingest-Einstiege, die API kein einziges Mal.
  Wer die Variable setzt, ingestiert in eine Collection, die die Oberflaeche nie liest.

### 4.7 Der Atlas ist ein Standbild

Alles, was ueber die Karte getan wird, erscheint erst nach einem neuen `atlas_build`.
Das ist Absicht und dokumentiert (`api/routes/atlas.py:1-16`), aber es ist die
**wahrscheinlichste Quelle fuer "tut nur halb"**: Notiz gesetzt, Foto verschoben,
Beschreibung vergeben — die Karte bleibt gleich. Nur der Papierkorb wird live nachgefuehrt.

Keine Reparatur noetig, aber die Erwartung muss an der Oberflaeche stehen. Dazu:
`forgetCapabilities` (`core/capabilities.js:25`) ist der im Kommentar versprochene Weg,
nach einem Lauf frisch nachzufragen — er wird nie gegangen, also bleibt die Sperre bis zum
Neuladen bestehen.

---

## Stufe 5 — Gestaltung: warum es "nicht so aussieht, wie es soll"

### 5.1 Die Tokenschicht ist zur Haelfte nicht vorhanden

`:root` definiert **7** Variablen (`styles.css:1-9`, das einzige `:root` im ganzen Baum).
Dem stehen **140 verschiedene hart gesetzte Hexwerte** gegenueber (styles.css 129
Vorkommen, ui.css 77, atlas.css 78, strip.css 26, jobs.html 40). Jede Farbaenderung muss
an rund 350 Stellen von Hand nachgezogen werden. **Das ist die Wurzel fast aller folgenden
Befunde.**

Schlimmer: es gibt eine **zweite, nie angelegte Tokenschicht.** `--line`, `--fg` und
`--bg2` werden 13-mal benutzt und nirgends definiert — die Fallbacks greifen produktiv.
`styles.css` zerfaellt damit in zwei Haelften: Zeile 1-452 im alten Stil, ab 453 im Stil
einer Tokenschicht, die es nicht gibt.

Und der undefinierte `--line`-Fallback hat **zwei verschiedene Werte**: `#2a2f3d` in
`styles.css:505`, `#2a2f36` in `jobs.html:12`. Zusammen mit den harten Rahmenfarben
(`#2a3140` 26x, `#3a4254` 20x, `#313a4a` 11x, `#2f3747` 5x, `#3a4353` 5x, `#2a2e38`) sind
**acht Grautoene fuer dieselbe Aufgabe "Rahmen"** im Einsatz. Direkt sichtbar: `.mini` hat
Rahmen `#3a4254` (`styles.css:117`), dasselbe `.mini` im Pager hat `var(--line)`
(`:643`). **Gleicher Knopf, zwei Rahmenfarben, je nach Elternelement.**

### 5.2 `var()`-Fallbacks, die dem Token widersprechen

`var(--accent, #7aa2f7)` steht 9-mal da — `--accent` ist aber `#8ab4ff`.
`var(--muted, #8b93a7)` 4-mal — `--muted` ist `#9aa3b2`. `var(--card, #161a1f)` in
`jobs.html` — `--card` ist `#1c1f27`.

Toter, aber irrefuehrender Code: wer die Regeln liest, sieht zwei Blautoene und glaubt an
zwei Absichten. Bei `--fg` greift der abweichende Wert wirklich: `.chanbar .chan.on`
faerbt mit `#e6e8ef`, der Rest der UI mit `--text`.

### 5.3 Vier Namen fuer einen Knopf

`.primary` (`styles.css:22`), `.joiner.on` (`:418`), `#btn-select-mode.on` (`:450`),
`.pv-modal footer button.primary` (`ui.css:93-98`) — vier Schreibweisen fuer "der
bestaetigende Knopf", ohne gemeinsame Regel. Die zugehoerige Textfarbe `#0b1020` steht
achtmal hart in vier Dateien. Eine Aenderung am Hauptknopf trifft eine davon.

Dieselbe Klasse `.danger-action` sieht in zwei Dateien unterschiedlich aus: im Papierkorb
ein gefuellter roter Knopf, im Atlas nur ein roter Rand auf grauem Grund.

### 5.4 Die Oberflaeche verletzt ihre eigene Regel

`ui.css:5-7` haelt ausdruecklich fest: *"Durchgehend fingergerecht … ein 24-Pixel-Knopf
unbenutzbar"* — und nennt als Grund, dass die Oberflaeche ueber Tailscale auch vom Handy
bedient wird.

`.mini` (`styles.css:115-118`) hat keine `min-height`, `font-size: .7rem`, `padding:
4px 9px`, also rund **24 px**. Die Klasse kommt allein in `index.html` **26-mal** vor. Die
Atlas-Werkzeugleiste liegt ebenfalls darunter (`.tog`, `.tool`, `.atlas-select`).

Am selben Bedienweg: das Muster "kleines ✕ erscheint beim Ueberfahren" existiert viermal
und ist viermal anders geloest. Die beiden `.drop`-Knoepfe sind **18x18 px und ohne Hover
unsichtbar** — am Handy gibt es kein Hover. Der Ausschluss-Knopf am Gesichtsbild und am
Serienstreifen ist dort nicht erreichbar.

### 5.5 Kleinteilige Unruhe

**32 `font-size`-Werte in drei Einheiten und 20 Radien.** Das erzeugt den Eindruck "sieht
nicht ganz richtig aus", ohne dass man auf einen Fehler zeigen koennte. Skalen einfuehren.

Dazu: `prefers-reduced-motion` fehlt bei 7 Bewegungen (der Fotostrom skaliert jedes
ueberfahrene Bild); `jobs.html` traegt 100 Zeilen inline-CSS als fuenfte Stilquelle, die
`ui.css`-Muster nachbaut und dabei abweicht — jede Reparatur in `ui.css` geht daran vorbei.

---

## Ausdruecklich nicht in dieser Session

- **Hell-Modus.** Es gibt keinen, in keiner Form: kein `prefers-color-scheme`, keine
  `[data-theme]`-Regel, keine zweite Farbdefinition. Das ist keine offene Baustelle,
  sondern eine nie begonnene — und mit 140 harten Hexwerten derzeit auch nicht machbar.
  **Produktentscheidung, kein Befund.** Erst nach 5.1 ueberhaupt diskutierbar.
- **Tastaturbedienung.** Kein einziges `tabindex` im ganzen Baum. Fotos oeffnen, Fotos
  auswaehlen und Papierkorb-Kacheln fuer das endgueltige Loeschen anhaken sind reine
  Maushandlungen. Eigenstaendiges, grosses Paket.
- **Tests fuer den Loeschpfad.** `api/routes/trash.py:128` loescht Dateien per `unlink`
  und raeumt Punkte samt Gesichtern aus dem Index — **kein Test fasst das Modul an**, und
  es gibt im ganzen Repo keinen einzigen Request-Level-Test (`TestClient` kommt nirgends
  vor). Der einzige unwiderrufliche Pfad ist der einzige ungesicherte. Eigenes Paket,
  aber **das wichtigste der drei**.
- **`persons.py` teilen.** 1019 Zeilen, und tatsaechlich zwei Dinge: neben 17
  Personen-Routen der Galerie-/Zeitleisten-Bau (`:411-524`) und die
  Foto-Payload-Synchronisierung (`:880-928`), beide mit eigenen Tests und wenigen
  Aufrufern. Zwei fertige Schnittkanten liegen offen; danach bleiben ~800 Zeilen reine
  Personen-Routen, was fuer die Anzahl Endpunkte normal ist. Kein Druck.

## Was zuerst am laufenden Programm zu klaeren ist

Der Plan ist aus dem Code gelesen. Diese Punkte entscheiden sich nur im Betrieb:

1. Ob die Cache-Drift heute schon alte Dateien ausliefert (Stufe 0.1 macht die Frage moot).
2. Ob `gap` und `budget` beim langsamen Schwenken konstant bleiben — die Trennmessung aus
   Stufe 2, vor jedem Reparaturversuch am Flackern.
3. Ob `space` im Bestand gefuellt ist oder ueberall `"?"` steht — sichtbar an den
   Bereichs-Chips ueber der Suche.
4. Ob Ollama laeuft. Ohne das sind die Gate-Befunde aus 4.5 gar nicht erst ausloesbar.
5. Ob die Abschneide-Grenzen beissen: mehr als 48 Treffer, mehr als 200 benannte Serien,
   mehr als 400 Albumordner.
6. Ob `subprocess.Popen(..., start_new_session=True)` (`api/routes/jobs.py:315`) auf dieser
   Maschine abkoppelt. Unter POSIX ja; unter Windows ignoriert CPython den Parameter.
7. Ob geloeschte Fotos noch als Mitglieder in der Ereignis-Ablage stehen — `trash.py`
   raeumt PHOTOS und FACES, ein Aufraeumschritt fuer `events_store` ist nicht auffindbar.

## Reihenfolge in einem Satz

Stufe 0 macht Messungen ehrlich; Stufe 1 repariert den Atlas dort, wo er schon in der
Grundeinstellung falsch liegt; Stufe 2 braucht vorher eine Messung und danach eine
Entwurfsentscheidung; Stufe 3 ist eine eigene Sitzung und blockiert nichts; Stufe 4
beseitigt die Ursachen von "tut nur halb"; Stufe 5 ist die Gestaltung, und die lohnt erst,
wenn die Tokenschicht existiert.
