"""Die Identität eines Fotos -- und warum sie nicht am Pfad hängen darf.

Bis Stufe 1 dieser Umstellung war `photo_id` gleich `sha256(Dateipfad)`, und
daran hing alles: der Qdrant-Punkt (`uuid5` davon), der Fremdschlüssel der
Gesichter, der Schlüssel im Vorschaubild-Cache. Ein Pfad, vier Rollen.

Das kostete an drei Stellen:

*Ein Rename ausserhalb von PhotoVault erzeugte ein neues Foto.* Name,
Beschreibung, Notizen und Gesichter blieben am alten hängen -- ohne dass
jemand etwas merkte.

*Jedes Verschieben durch PhotoVault selbst* musste den Punkt auf eine neue ID
umschreiben und die Gesichter umhängen (`relocate.migrate_photo`).

*Der Cache verwaiste mit.* Gemessen 14.858 Kacheln, 94 MB, weil ihr
Schlüssel derselbe Pfad-Hash war.

Deshalb `photo_uid`: **einmal vergeben, danach eingefroren.** Der Wert ist
opak -- er wird beim ersten Sehen aus dem Pfad gebildet, weil das eine
brauchbare Eindeutigkeit ist, und danach nie wieder berechnet, sondern
gelesen. Genau das ist der Unterschied, nicht die Form der Zahl.

Fuer die 14.593 bestehenden Fotos ist `photo_uid` deshalb wertgleich mit dem
alten `photo_id`: Gesichter zeigen schon darauf, Punkt-IDs sind schon daraus
gebildet, der Cache liegt schon darunter. Die Umstellung ist ein
Feldzuwachs, kein Umbau.

Wiedererkannt wird eine verschobene Datei kuenftig ueber ihren
Inhalts-Hash (`content_sha256`, Stufe 2) -- die Bytes liest der Ingest
ohnehin komplett.
"""
from __future__ import annotations

import hashlib
import uuid


def photo_uid_for(file_path: str) -> str:
    """Die Kennung fuer ein *neu gesehenes* Foto.

    Nur beim ersten Mal. Ist die Datei schon bekannt -- ueber ihren Pfad oder
    ihren Inhalts-Hash -- gilt die gespeicherte Kennung, und diese Funktion
    darf nicht gefragt werden.
    """
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()


#: Alter Name derselben Rechnung. Bleibt, weil er an zwei Stellen die Frage
#: "welche Kennung *haette* dieser Pfad" beantwortet: beim Wiedererkennen im
#: Ingest und beim Aufraeumen alter Cache-Schluessel.
photo_id_for = photo_uid_for


def point_id_for(photo_uid: str) -> str:
    """Der Qdrant-Punkt zu einer Kennung. Bleibt unveraendert."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, photo_uid))


def point_id_for_path(file_path: str) -> str:
    """Nur fuer den Erstkontakt. Ein bekanntes Foto wird nachgesehen."""
    return point_id_for(photo_uid_for(file_path))


def uid_of(payload: dict) -> str:
    """Die Kennung aus einem Payload -- neues Feld zuerst, altes als Rueckfall.

    Waehrend der Umstellung tragen Punkte beides. Danach faellt `photo_id`
    weg, und dieser Zugriff bleibt die einzige Stelle, die das weiss.
    """
    return payload.get("photo_uid") or payload.get("photo_id") or ""
