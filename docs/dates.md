# Aufnahmezeit: Kopierzeit ist nicht die Party

Serien, Suche und „Wer war dabei?" hängen an `taken_at`. Liegt das Datum falsch,
landet der Abiball in derselben Nacht wie ein Import auf den PC — und niemand
merkt es, weil die Oberfläche „aus den Bilddaten" sagt und Vertrauen 1,0
anzeigt.

Dieses Dokument ist die **Probe aufs Exempel**: nicht „das Skript hat gelaufen",
sondern *woran man erkennt, dass die Korrektur stimmt* — in diesem Archiv und
in jedem anderen.

## Die drei Uhren in einer JPEG-Datei

EXIF kennt mindestens drei Zeitstempel. Sie meinen nicht dasselbe.

| Tag | Name | Was es meistens ist |
|---|---|---|
| 36867 | DateTimeOriginal | Aufnahme. Die Kamera schreibt das im Moment des Auslösers. |
| 36868 | DateTimeDigitized | Digitalisierung. Bei Digitalkameras oft gleich Original. |
| 306 | DateTime (IFD0) | Letzte Änderung am EXIF-Block. Import, „Speichern unter", Photoshop. |

Windows, Bildbearbeiter und manches Handy schreiben beim Kopieren **306 neu**
und lassen Original unangetastet. Ein halbstündiger Dump auf die Festplatte
erzeugt dann hunderte Dateien mit derselben „Aufnahmezeit" — in Wirklichkeit
die Importnacht.

PhotoVault liest deshalb **Original, sonst Digitalisiert, sonst DateTime**.
Die Reihenfolge steht in `ingest.exif_extractor.exif_capture_stamp` und gilt
für Ingest und Backfill. README und Spec sagten das schon; ein früherer
Extraktor hat 306 zuerst genommen. Genau das mischt fremde Anlässe.

Zwei andere Fehler sehen ähnlich aus und sind es nicht:

- **Zurückgefallene Kamera-Uhr** (Batteriewechsel, Werksdatum 2005-01-01) —
  Original *und* 306 sind falsch. Das findet `tools/clock_report.py`.
- **WhatsApp ohne EXIF** — es gibt nichts zu lesen. Datum kommt aus Dateiname
  oder Dateizeit, nie aus Tag 306.

## Warum „aus den Bilddaten" trügt

Die UI übersetzt `date_source=exif` mit „aus den Bilddaten". Das ist wahr:
der Wert steht in der Datei. Es sagt nicht, *welcher* EXIF-Tag es war.
Vertrauen 1,0 auf 306 einer Importnacht ist technisch konsistent und inhaltlich
falsch. Die Probe unterscheidet die Tags, die Anzeige (noch) nicht.

## Das Werkzeug

```bash
python -m tools.probe_dates --file /pfad/zum.jpg          # eine Datei: 306 vs Original
python -m tools.probe_dates --night 2009-01-20            # alle Index-Fotos dieses Tags
python -m tools.backfill_taken_at --preview               # Bestand, nichts schreiben
python -m tools.backfill_taken_at --dry-run
python -m tools.backfill_taken_at                         # taken_at und date im Index
```

`--preview` öffnet jede indizierte Datei einmal (kein CLIP, keine Gesichter).
Es vergleicht den Indexwert mit Original-zuerst und gruppiert **Tagessprünge**
nach Album. Eine Stichprobe je Gruppe nennt Dateiname, alten Wert, neuen Wert
und 306, falls der abweicht.

An einem Bestand von 17 453 Fotos (cifs-Mount, 8 Worker) dauerte der Lauf
34 Sekunden:

| | |
|---|---|
| unverändert (Original = Index) | 5 487 |
| nur Uhrzeit, gleicher Kalendertag | 73 |
| anderer Kalendertag | 238 |
| kein EXIF | 11 629 |
| Datei fehlt / unlesbar | 26 |

Die große Mehrheit braucht keine Korrektur. Die 238 Tagessprünge sind die
Probe: wenn *die* plausibel sind, darf der Schreib-Lauf laufen.

Der ältere Backfill (nur Uhrzeit nachziehen, Tag muss schon stimmen) hätte
genau diese 238 übersprungen. `--preview` ist deshalb nicht optional.

## Wonach man schaut

Grün — das Verfahren hat recht — wenn **zwei unabhängige Hinweise** denselben
Tag stützen wie Original, und 306 der Index ist:

1. **Dateiname.** Android `20130420_124739.jpg` und Original 2013-04-20 12:47.
   Der Name entstand in der Kamera, nicht beim Import.
2. **Ordnername.** `18. Geburtstag 15.05.2009` und Original 2009-05-15.
   Der Mensch hat den Ordner nach dem Anlass benannt.
3. **„bearbeitet"-Export.** Dateiname enthält den Anlass (`Abiball_2008_…`),
   Original liegt am Festtag, 306 ein, zwei Tage oder Wochen später
   (Photoshop speichert 306 neu).
4. **Importnacht.** Viele Alben, 306 in einem Fenster von Minuten bis einer
   Stunde, Originale über Jahre verteilt. Das ist Kopieren, keine Party.
5. **Silvester über Mitternacht.** 31.12. 23:xx und 01.01. 00:xx — Original
   darf den Kalendertag wechseln, 306 oft erst Tage später (Export).

Rot — nicht schreiben, erst nachdenken:

- Original und Dateiname widersprechen sich *und* der Ordner widerspricht
  beiden. Dann ist vielleicht die Datei falsch einsortiert, nicht nur der Tag.
- Alle Originale einer Kamera stehen auf 2005-01-01 oder einem Werksdatum.
  Das ist `clock_report`, kein Tag-306-Problem.
- Der Sprung geht *in die Zukunft* hinter den Import (Original nach 306 um
  Jahre). Selten; einzelne Dateien mit absichtlich gesetztem Datum.
- WhatsApp-Ordner mit Original: ungewöhnlich. Erst prüfen, ob wirklich EXIF
  an der Datei klebt oder ein Wrapper.

Grau, harmlos: gleicher Kalendertag, Original und 306 unterscheiden sich um
Sekunden. Dieselbe Aufnahme, zwei Tags.

## Wie ein LLM (oder ein Mensch) die Probe macht

Das ist derselbe Ablauf, mit dem dieser Bestand geprüft wurde. Er ist
wiederholbar auf einem anderen Archiv, ohne die UI zu brauchen.

1. **Eine verdächtige Serie finden.** Eine benannte Serie, deren Fotos in
   *mehreren sprechenden Alben* liegen (zwei Geburtstage, Abiball und
   Abistreich, Dump + Anlass) und deren `taken_at` in einem engen Fenster
   liegt — oft nachts, oft unter einer Stunde.
2. **Die Dateien selbst lesen**, nicht nur den Index. Pro Datei:
   - Payload: `folder_name`, `taken_at`, `date_source`, `file_path`
   - EXIF 306, 36867, 36868, Make/Model
   - Dateiname, Ordnername
3. **Die Hypothese.** Wenn 306 ≈ Index und 36867 ein anderer Tag ist, der zu
   Ordner oder Dateiname passt, ist 306 die Kopierzeit.
4. **`--preview` über den ganzen Bestand.** Nicht nur die eine Serie: dasselbe
   Muster muss in anderen Alben wiederkommen, sonst war es Zufall.
5. **Die Tabelle der Tagessprünge lesen.** Gruppen mit `n ≥ 5` zuerst. Je
   Gruppe eine Datei stichprobenartig: stimmt Original mit Ordner oder Namen?
6. **Gegenprobe auf Unveränderte.** Eine Kamera-Serie, die schon im richtigen
   Album am richtigen Tag liegt, darf sich nicht bewegen.
7. **Erst dann schreiben.** `--preview` ändert nichts. Der Schreib-Lauf setzt
   `taken_at` und `date` aus Original; `date_source` bleibt `exif`.

Kein Schritt braucht das Sprachmodell für Pixel. Das LLM strukturiert den
Vergleich und hält die Stichprobe ehrlich: was *nicht* passt, gehört in die
rote Liste, nicht unter den Teppich.

Schritt 2 ist ein Befehl, kein Snippet:

```bash
python -m tools.probe_dates --file "$DATEI"
python -m tools.probe_dates --night 2009-01-20 --sample 8
```

`gewählt` muss mit `exif_capture_stamp` und mit `--preview` unter „original"
übereinstimmen. Tut es das nicht, stimmt die Probe nicht mit dem Schreiben
überein. Tests: `tests/test_probe_dates.py`.

## Was danach passiert

Nach dem Schreiben clustern die Serien neu: die Importnacht zerfällt, die
echten Anlässe stehen an ihren Tagen. Ein Name, der an der Importnacht hing
(„alles vom 20.01.2009 01:46–02:15"), trifft dann nur noch Dateien, die
wirklich in diesem Fenster aufgenommen wurden — oft wenige oder keine.

Dateien ohne Original bleiben auf 306. Zwei Fotos eines Anlasses ohne
DateTimeOriginal ändern sich nicht; ihre Nachbarn mit Original rutschen auf
den Festtag. Das ist richtig, kein Verlust.

WhatsApp und andere Dateien ohne EXIF rührt *dieser* Lauf nicht an. Die
gehören in den nächsten Schritt: die Ableitung **in die Datei schreiben**,
sonst ist sie nach dem nächsten Kopieren weg.

## Was fehlt, hineinschreiben

WhatsApp, „Sent“, Screenshots: kein Original, oft kein EXIF. PhotoVault hat
den Tag aus dem Dateinamen (`IMG-20181021-WA0081`) und die Uhr oft aus der
Dateizeit. Das ist Empfangszeit, nicht Auslöser — aber es ist die beste
Uhr, die die Datei je haben wird, und **Kopieren ändert mtime**. Steht der
Wert nur im Index, ist er nach einem Umzug auf die nächste Platte verloren
oder muss neu geschätzt werden, mit schlechterer Grundlage.

Deshalb schreibt `tools/exif_repair.py` den schon ermittelten `taken_at`
als `DateTimeOriginal` in JPEGs, **die noch keine Aufnahmezeit haben**.
Captions ebenso: `ingest.caption_pass` schreibt den Satz nach
`ImageDescription` (und `XPComment` für den Explorer). `UserComment` bleibt
die Herkunftsnotiz (`photovault:cap=llm`), damit ein LLM-Satz nicht als
Kameratext gelesen wird. Eine fremde Beschreibung wird nicht überschrieben.

Sicherheiten, jede gegen einen konkreten Schaden:

- Standard ist Trockenlauf. `--apply` ist absichtlich extra.
- `date_source=exif` wird nie angefasst. Eine Kamera bleibt eine Kamera.
- Mitternacht im Index heißt „Uhr unbekannt“ — nichts schreiben, keine
  erfundene Präzision.
- Herkunftsnotiz `photovault:src=filename` (oder `file_time` / `folder`).
  Der nächste Ingest liest das und gibt nicht Vertrauen 1,0 wie bei einer
  Messung.
- Dateizeit und Birth-Time werden nach dem Schreiben wiederhergestellt.
  Sonst zerstört genau dieser Lauf die Quelle, aus der die Uhr kam.
- Gegenprobe: nach dem Insert muss `read_capture_time` denselben Wert lesen.
- PNG und andere Formate ohne verlustfreien EXIF-Tausch bleiben draußen.

```bash
python -m tools.exif_repair --preview     # wer käme in Frage (Index)
python -m tools.exif_repair               # Trockenlauf, Dateien öffnen
python -m tools.exif_repair --apply --limit 200
```

Reihenfolge: erst `--preview` auf Tag 306 vs. Original (`backfill_taken_at`),
Index korrigieren, **dann** fehlende EXIF einfrieren. Umgekehrt würde eine
Importnacht als „Messung“ in WhatsApp-fremde Kameradateien nicht landen
(`date_source=exif` ist tabu) — aber die Probe gehört trotzdem zuerst,
damit der Index die Uhr kennt, die in die Datei soll.

Das ist Empfangszeit, eingefroren. Nicht die Party. Trotzdem: Explorer,
andere Programme und der nächste Scan sehen dasselbe Datum, und ein Move
auf dem NAS ändert es nicht mehr.

## Tests, die das festhalten

`tests/test_exif_extractor.py`: Original schlägt 306, auch wenn 306 eine
Importnacht ist. 306 gilt nur, wenn Original fehlt.

`tests/test_probe_dates.py`: die Probe-Datei liest dieselben Tags.

`tests/test_exif_repair.py`: Kamera-EXIF ist kein Kandidat; WhatsApp mit
Dateiname und Uhrzeit ist einer; Mitternacht nicht.

Wer das Verfahren auf einem fremden Bestand wiederholt, braucht keinen neuen
Unit-Test — der ist lokal und kennt keine Alben. Die Probe ist `--preview`
plus die Checkliste oben.

## Kurz

- 306 ist oft der Import, 36867 die Aufnahme.
- PhotoVault liest 36867 zuerst.
- `--preview` beweist das an *diesem* Archiv, bevor jemand schreibt.
- Vertrauen kommt aus Stichproben, die Original gegen Ordner und Dateiname
  halten — nicht aus der Zahl „238 Korrekturen".
- Was kein EXIF hat, bekommt eines — mit Notiz, ohne Kameradaten zu
  überschreiben. Kopieren darf die Uhr danach nicht mehr löschen.
