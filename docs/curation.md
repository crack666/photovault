# Aus dem Datengrab eine Bibliothek machen

> Entstanden am 24.08.2026 an 43 037 Bildern auf `\\192.0.2.10\photo`.
> Alle Zahlen sind an diesem Bestand gemessen.

Das Archiv ist kein Fotoalbum. Neben den Alben liegen Screenshots, Scans,
Weiterleitungen, ein Thumbnail-Cache und Privates. Die Aufgabe ist nicht,
alles zu indizieren, sondern **die sehenswerten Bilder zu finden** — die mit
Personen und Anlässen — und sie vom Rest zu trennen.

Vier Werkzeuge greifen dafür ineinander. Sie sind hier in der Reihenfolge
beschrieben, in der sie aufeinander aufbauen.

## 1. Auswahl: was überhaupt hineinkommt

Ein Ordner unter demselben Share zu liegen ist kein Grund, indiziert zu
werden. [sources.txt](../sources.txt) nennt die Verzeichnisse einzeln; `#`
kommentiert aus, ein führendes `-` schließt gezielt aus.

Der Anlass war konkret: ein Lauf über `/mnt/photo` hätte auch `confidential`
(8 947 Bilder, 16,5 GiB) aufgenommen. Er erreichte den Ordner nur deshalb
nicht, weil vorher abgebrochen wurde — Glück, kein Entwurf.

```bash
python -m ingest.pipeline --sources-file sources.txt --dry-run
```

Der Trockenlauf zeigt vor dem Schreiben, was aufgenommen würde, aufgeschlüsselt
bis auf Unterordner.

**`tools/prune.py`** hält den Index deckungsgleich mit der Liste. Es fragt
dabei nicht „liegt unter einer Quelle", sondern **„würde der Scanner das heute
aufnehmen"** — sonst bliebe liegen, was ein früherer Lauf mit anderen Regeln
aufgenommen hat. Von Hand vergebene Personen, Notizen und Captions blockieren
das Löschen.

### Punkt-Verzeichnisse

`_is_image` filterte Dateien mit führendem Punkt, aber keine **Verzeichnisse**.
Androids `Pictures/.thumbnails` allein hielt 5 119 Miniaturbilder — 22 % des
Handy-Ordners wären als eigene Fotos im Index gelandet.

## 2. Kanal: woher ein Bild stammt

Der stärkste Hinweis auf Zusammengehörigkeit ist die Quelle. Bei einer Kamera
genügt noch die Zeit: man nimmt sie zu einer Gelegenheit mit. Ein Handy ist
immer dabei — dort fallen am selben Nachmittag ein Partyfoto, drei Screenshots
und ein weitergeleitetes Meme an.

`ingest/provenance.py` leitet aus Pfad und Dateiname einen **Kanal** ab:

| Kanal | Fotos | |
|---|---|---|
| `whatsapp` | 10 014 | empfangen |
| `camera` | 5 691 | eigene Aufnahmen |
| `whatsapp-sent` | 1 732 | selbst verschickt |
| `screenshot` | 11 | |
| `download` / `document` | 5 | |

Bewusst **nicht das Kameramodell**: zwei Handys auf derselben Feier sollen
zusammenfinden, das ist dieselbe Gelegenheit aus zwei Blickwinkeln. Getrennt
gehört, was aus verschiedenen Quellen *stammt*.

Der Dateiname schlägt dabei das Verzeichnis. Elf Screenshots liegen in echten
Albumordnern (`Fotos/Junggesellenabschied`) und wurden nur am Namen erkannt; umgekehrt
sagt ein Ordner `Screenshots` mehr als ein nichtssagender Dateiname.

Der Kanal steht als indiziertes Feld im Payload und ist damit filterbar.

## 3. Ereignisse: was zusammengehört

`ingest/events.py` gruppiert Fotos zu Serien: eine Lücke über drei Stunden
beginnt eine neue Gelegenheit. Gruppiert wird **innerhalb eines Kanals**.

Zwei Dinge macht das möglich, die einzelne Bilder nicht können:

**Bewertung vererbt sich.** Zeigt ein Foto der Serie eine bekannte Person,
gehört die Landschaft zehn Minuten davor dazu — auch ohne Gesicht darauf.

**Namen vererben sich.** Wer eine Serie ansieht und „Max 30. Geburtstag"
erkennt, benennt alle Fotos auf einmal. Derselbe Ablauf wie beim Zuordnen von
Gesichtern: Cluster bilden, Mensch bestätigt, Bedeutung verteilt sich.

Am Bestand gemessen, Kanal `camera`:

| Beginn | Fotos | Dauer | Album |
|---|---|---|---|
| 2012-12-31 17:28 | 151 | 627 min | Silvester 2012-2013 |
| 2007-09-30 10:37 | 142 | 102 min | 18. Geburtstag |
| 2008-06-27 16:31 | 129 | 191 min | Abiball, Abistreich, Abiverleihung |
| 2011-10-23 18:32 | 122 | 14 min | Doppelgeburtstag 21 |

Drei Stunden halten Silvester über 10,5 Stunden zusammen und trennen trotzdem
Vormittag von Abend. Der Wert ist unkritisch: 0,5 h ergibt 1 923 Ereignisse,
12 h noch 1 257.

### Wo es nicht trägt

Bei `whatsapp` spannen die Serien 9 bis 10 Stunden. Der Grund ist
grundsätzlich: die Empfangszeit ist nicht die Aufnahmezeit, und über einen
wachen Tag entsteht nie eine Drei-Stunden-Lücke. Das sind keine Ereignisse,
das sind Tage.

**Ereignisvererbung trägt bei `camera`. Bei den 10 014 WhatsApp-Fotos muss das
Gesichtssignal die Auswahl machen.**

## 4. Zeit: die Voraussetzung für alles davor

`taken_at` stand bei jedem Foto auf Mitternacht. Der EXIF-Extraktor schnitt
die Uhrzeit mit `strftime("%Y-%m-%d")` ab, der Normalizer baute den Zeitstempel
aus dem Tagesdatum neu. Ohne Uhrzeit ist „das Bild um 11:59 gehört zu dem um
12:00" nicht entscheidbar — die ganze Ereignisbildung hing daran.

Jetzt gilt: EXIF-Aufnahmezeit, sonst Dateizeit, **aber nur wenn deren Tag zum
Datum passt**. Sonst ist sie der Kopierzeitpunkt und täuscht eine Präzision
vor, die es nicht gibt.

EXIF selbst hat drei Uhren. `DateTime` (Tag 306) ist oft der Import auf den
PC, `DateTimeOriginal` die Aufnahme. Ein früherer Extraktor las 306 zuerst —
dann clustert eine Kopiernacht fremde Anlässe zu einer Serie, und die UI sagt
trotzdem „aus den Bilddaten". Die Reihenfolge und die Probe, *bevor* man den
Index überschreibt, stehen in [dates.md](dates.md).

Bei WhatsApp trägt das erstaunlich weit: 9 866 von 15 310 Dateien (64,4 %)
haben eine Änderungszeit, deren Tag zum Dateinamen passt. Dass es der
Empfangs- und nicht der Aufnahmezeitpunkt ist, spielt für die Sortierung keine
Rolle.

Ergebnis: **90,7 % der Fotos haben eine echte Uhrzeit**, 100 % ein Datum.

`tools/backfill_taken_at.py` zieht das für bereits indizierte Fotos nach —
nur EXIF, keine Gesichtserkennung, keine Vektoren. 7 580 Fotos in 31 Sekunden.

## 5. EXIF zurückschreiben

Was wir ableiten, kann ins Original zurück. Dann profitieren künftige Läufe
**und jedes andere Programm**, das die Fotos öffnet.

`ingest/exif_writer.py` schreibt `DateTimeOriginal` über `piexif` — nur der
EXIF-Block wird getauscht, die Bilddaten bleiben byteweise identisch.

Vier Sicherungen, jede gegen einen konkreten Schaden:

**Herkunftsnotiz.** Ein aus dem Dateinamen abgeleitetes Datum als
`DateTimeOriginal` läse der nächste Lauf als EXIF mit Vertrauen 1,0 — unsere
Schätzung wäre zur Messung befördert. `UserComment` hält fest, woher der Wert
kam; der Extraktor liest das und behält die ursprüngliche Bewertung.

**Der alte Wert bleibt.** Beim Korrigieren wandert er als `prev=…` in dieselbe
Notiz. `revert()` stellt ihn daraus wieder her.

**Die Änderungszeit wird erhalten.** Das ist keine Kosmetik: bei WhatsApp ist
sie die *einzige* Uhrzeitquelle. Wer sie beim Schreiben verliert, hat genau
einen Versuch. Sie wird vorher gelesen, hinterher per `utime` zurückgesetzt und
zusätzlich in der Notiz festgehalten.

**Gegenprobe nach dem Schreiben.** Kommt beim Zurücklesen nicht der geschriebene
Wert heraus, wirft es — statt eine stille Beschädigung zu hinterlassen.

### Kamera-Uhren

`tools/clock_report.py` findet Geräte mit falsch gestellter Uhr. Das
Erkennungsmerkmal ist nicht der einzelne Ausreißer, sondern **eine bestimmte
Kamera in mehreren Alben**:

```
── FinePix A202                      24 Fotos, 3 Alben
     18. Geburtstag (2006)       13 Fotos  steht auf 2009-01-20 … 2009-10-06  Album 2006-12-17
     18. Geburtstag (2007)        9 Fotos  steht auf 2009-01-20 … 2015-11-26  Album 2007-06-02
── KODAK V530                        12 Fotos, 1 Album
     Videoabend      12 Fotos  alle auf 2005-01-01  → +1400,5 Tage verschieben
```

Zwei Fehlerbilder, die unterschiedlich behandelt werden müssen. Bei
**konstantem Versatz** — alle Fotos auf demselben Werksstand — lässt sich exakt
verschieben, Reihenfolge und Tageszeit bleiben stimmig. Bei **zurückgefallener
Uhr**, die über Jahre streut, ist die absolute Zeit verloren; dort ginge nur
das Albumdatum bei erhaltener Tageszeit. Der Bericht sagt das, statt eine
Korrektur vorzugaukeln.

---

## Wie das in der Oberfläche zusammenkommt

Der Zeitstrahl nach Jahren ist die richtige Grundlage. Bisher gruppiert er
innerhalb eines Jahres nach `(Ordner, Datum)` — eine Behelfsdefinition, die
jetzt durch die echte ersetzt werden kann.

### Drei Ebenen statt zwei

**Jahr** bleibt die oberste Ebene und die Sprungmarke. Bei 17 453 Fotos über
20 Jahre ist das die einzige Ebene, die vollständig auf einen Blick passt.

**Monat** kommt dazu, wo ein Jahr viele Fotos hat. Ein Jahr mit 3 000 Bildern
ist als Block unbrauchbar; zwölf Monatsbänder machen daraus etwas Greifbares.
Bei Jahren mit wenigen Fotos entfällt die Ebene — sie soll gliedern, nicht
Struktur behaupten, wo keine ist.

**Ereignis** ist die Einheit, mit der man tatsächlich arbeitet. Eine Karte je
Serie, mit dem, was sie unterscheidbar macht:

```
┌────────────────────────────────────────────────────────┐
│ Silvester 2012/13                    31.12.2012        │
│ 17:28 – 03:55 · 151 Fotos · eigene Aufnahmen           │
│ Lorenz Schulz, Mareike Meyer, Nina Hofmann +4             │
│ [▪▪▪▪▪▪▪▪ Vorschaubilder ▪▪▪▪▪▪▪▪]                     │
└────────────────────────────────────────────────────────┘
```

Die Uhrzeitspanne ist neu und trägt Information: „14 Minuten, 122 Fotos" ist
eine Fotoserie, „627 Minuten, 151 Fotos" ein durchgemachter Abend.

### Der Kanal als Filter, nicht als Gliederung

Ein Umschalter über dem Zeitstrahl — *Eigene Aufnahmen · Empfangen ·
Verschickt · Alle* — mit `camera` als Vorgabe. Das ist die Bibliothek im
engeren Sinn; WhatsApp bleibt erreichbar, drängt sich aber nicht auf.

Als Gliederungsebene taugt der Kanal nicht: niemand sucht „alle Screenshots aus
2019". Als Filter beantwortet er die Frage, die man tatsächlich hat: „zeig mir,
was ich selbst aufgenommen habe".

### Benennen als eigener Arbeitsablauf

Analog zu „Wer ist das?" eine Ansicht **„Unbenannte Serien"**: die größten
Ereignisse ohne Namen, absteigend, mit Vorschaubildern und einem Eingabefeld.
Ein Name deckt in einem Zug 50 bis 150 Fotos ab.

Bei `HandyPics` warten dafür schon zwei Serien mit 55 und 53 Fotos, beide ohne
erkannte Personen und ohne Albumnamen — genau die Fälle, die sonst nie
gefunden würden.

### Was der Zeitstrahl noch zeigen sollte

Die Balkenhöhe je Jahr gibt es bereits. Sinnvoll wäre, sie nach Kanal zu
stapeln: ein Jahr, das zu 90 % aus WhatsApp besteht, sieht anders aus als
eines mit 400 eigenen Aufnahmen — und man weiß sofort, wo sich das Hinsehen
lohnt.

---

## Reihenfolge der Arbeit

1. Auswahl in `sources.txt` festlegen, `--dry-run` prüfen, ingesten
2. `tools/prune.py` gegen dieselbe Liste — der Index folgt der Auswahl nicht von selbst
3. `python -m tools.backfill_taken_at --preview`, Stichproben nach [dates.md](dates.md), dann der Schreib-Lauf — falls Bestand aus älteren Läufen vorliegt
4. Gesichter benennen — das ist die Grundlage für alles Folgende
5. Ereignisse bilden, Serien benennen
6. Captions zuletzt: mit Namen im Kontext werden sie deutlich besser
