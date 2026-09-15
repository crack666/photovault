# EXIF-Prüfbericht: falsch gestellte Kamera-Uhren

**Gemessen am 2026-09-15** gegen den laufenden Index (`photos` auf
`localhost:6333`, 16 313 Punkte, 81 Alben) mit `python -m tools.clock_report`,
Stand `master` 72fccf1. Dieses Dokument stellt nur fest; geändert wurde nichts.

Reproduzierbar:

```bash
~/.venvs/photovault/bin/python -m tools.clock_report
~/.venvs/photovault/bin/python -m tools.clock_report --album "Wettessen Dennis Leo"
```

## Befund

**42 Fotos in 5 Gruppen** fallen aus ihrem Album heraus, verteilt auf drei
Kameras. Das ist der gesamte Verdacht im Bestand — nicht 14 Alben (siehe unten).

| Kamera | Album | Fotos | steht auf | Album liegt bei | Art |
|---|---|---|---|---|---|
| KODAK V530 ZOOM | Videoabend bei Chris ohne Video 1.11.08 | 12 | 2005-01-01 | 2008-11-01 | **Versatz** +1400,5 Tage |
| OnePlus Nord2 5G | OnePlusNord 5 | 15 | 2022-01-15 … 2024-04-21 | 2025-05-10 | zurückgefallen |
| unbekannt | Wettessen Dennis Leo | 8 | 2008-03-22 … 2011-03-16 | 2006-10-10 | zurückgefallen |
| unbekannt | WhatsApp Stickers | 4 | 2022-01-29 … 2025-07-06 | 2024-07-01 | zurückgefallen |
| unbekannt | Timo Jasmin 21 | 3 | 2015-08-30 … 2015-12-01 | 2011-10-23 | zurückgefallen |

**12 Fotos sind exakt korrigierbar** (konstanter Versatz, Uhrzeit und
Reihenfolge bleiben stimmig), **30 sind zurückgefallen** — dort ist die
absolute Zeit verloren, übernehmbar wäre nur das Datum aus dem Album.

## Was der Befund taugt und was nicht

Nur die **KODAK-Gruppe** ist ein klarer Uhrfehler: zwölf Fotos stehen exakt auf
dem Werksdatum `2005-01-01`, das Album nennt seinen Tag im Namen
(`1.11.08`). Versatz und Ziel sind belegt, nicht geraten.

Die drei Gruppen unter „unbekannt" sind **kein Gerät**, sondern die Fotos ohne
`exif.Model`. `clockcheck` bündelt nach Kameramodell; fehlt es, landen fremde
Quellen im selben Topf. „Dieselbe Kamera in mehreren Alben" — sonst das
tragende Argument des Verfahrens — trägt hier also nicht. Bei `WhatsApp
Stickers` ist ein Album-Bezugsdatum ohnehin sinnlos: ein Stickerordner hat
keinen Aufnahmetag.

`OnePlusNord 5` ist wahrscheinlich gar kein Fehler, sondern ein Ordner, in den
über drei Jahre kopiert wurde: 505 Fotos, davon 422 aus 2025 — die Mehrheit
bildet das Bezugsdatum, die älteren 15 fallen heraus, obwohl ihre Zeit stimmen
dürfte. Das Verfahren nimmt hier den Ordner für ein Ereignis.

## Woher die Zahl 14 kam

Im Backlog stand „14 Alben mit gespaltenem Jahr". Diese Zahl ist am 2026-09-15
**nicht reproduzierbar**; gemessen wurde stattdessen:

| Maß | Wert |
|---|---|
| Alben, deren Fotos in mehr als einem Jahr liegen | 30 |
| davon mit mindestens 8 Fotos | 23 |
| davon vom Verfahren als Uhrverdacht gemeldet | **5** |

Ein gespaltenes Jahr ist eben kein Uhrfehler. Die größten gespaltenen Alben
sind Sammelordner, die es zu Recht sind: `WhatsApp Images` (7 332 Fotos, 2013
bis 2025), `HandyPics` (3 172), `Sent` (1 693). Wer die reparierte, machte
richtige Daten kaputt.

## Vorschlag für den nächsten Schritt

Reparieren lohnt **nur für die KODAK-Gruppe**: 12 Fotos, ein Versatz, belegtes
Ziel. Der Schreibpfad dafür steht (`ingest/exif_writer.py`, Trockenlauf ist
Standard, `revert` vorhanden), aber `tools/exif_repair.py` schreibt heute nur
*fehlende* Zeiten und rührt vorhandene nicht an — ein Versatz-Lauf wäre neu.

Die 30 zurückgefallenen Fotos sollten **liegen bleiben**: bei zweien ist die
Gruppierung wackelig, bei einem ist das Album als Bezug ungeeignet, und
gewonnen würde ein Datum, dessen Uhrzeit dann nachweislich falsch ist.

Vorher lohnt eine Kleinigkeit am Verfahren: Fotos ohne `exif.Model` gar nicht
erst zu Gruppen zusammenfassen. Sie erzeugen drei der fünf Meldungen und
machen den Bericht schwerer lesbar, als der Bestand ist.
