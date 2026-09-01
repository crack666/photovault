"""Der Bereich wird beim Ingest geschrieben, der Papierkorb-Stempel überlebt ihn.

Zwei Lücken, die zusammengehören, weil beide erst beim `upsert` auffallen und
beide still bleiben: der Bereichs-Wähler der Suche war nach einem frischen
Ingest leer, weil `space` gar nicht geschrieben wurde -- die Funktion sah
kaputt aus, obwohl nur ein Feld fehlte. Und ein Lauf mit `--no-resume` holte
weggeworfene Fotos zurück, weil `upsert` das ganze Payload ersetzt und
`trashed_at` in keiner Datei steht.
"""
from ingest.pipeline import PhotoRecord, IngestPipeline, _apply_prior
from ingest.qdrant_writer import QdrantWriter
from ingest.spaces import space_of


class FakeClient:
    """Nimmt Upserts entgegen und merkt sich das Payload."""

    def __init__(self):
        self.payloads = []
        self.indexes = []

    def upsert(self, collection_name, points, wait=False):
        self.payloads.append(points[0].payload)

    def create_payload_index(self, collection, field_name=None, field_schema=None):
        self.indexes.append(field_name)


def writer_with(space_root):
    """Ein Writer ohne Netzwerk -- nur der Payload-Bau wird geprüft."""
    w = QdrantWriter.__new__(QdrantWriter)
    w.client = FakeClient()
    w.collection = "photos"
    w.faces_collection = "faces"
    w.space_root = space_root
    return w


class TestSpaceWirdGeschrieben:
    def test_erste_ordnerebene_unter_der_wurzel(self):
        w = writer_with("/mnt/photo")
        w.upsert(PhotoRecord(photo_id="a", file_path="/mnt/photo/Fotos/2013/a.jpg"))
        assert w.client.payloads[0]["space"] == "Fotos"

    def test_zweite_quelle_bekommt_eigenen_bereich(self):
        w = writer_with("/mnt/photo")
        w.upsert(PhotoRecord(photo_id="b", file_path="/mnt/photo/Handys/x/b.jpg"))
        assert w.client.payloads[0]["space"] == "Handys"

    def test_datei_direkt_in_der_wurzel_hat_keinen_bereich(self):
        w = writer_with("/mnt/photo")
        w.upsert(PhotoRecord(photo_id="c", file_path="/mnt/photo/lose.jpg"))
        assert w.client.payloads[0]["space"] == "?"

    def test_ohne_wurzel_bleibt_das_feld_leer_statt_falsch(self):
        w = writer_with(None)
        w.upsert(PhotoRecord(photo_id="d", file_path="/mnt/photo/Fotos/a.jpg"))
        assert w.client.payloads[0]["space"] is None

    def test_windows_pfad_wird_nicht_zu_fragezeichen(self):
        # Ohne Normalisierung fände die Zerlegung keinen Trenner und hielte
        # den ganzen Pfad für einen Dateinamen.
        root = "D:" + chr(92) + "Fotos"
        pfad = chr(92).join(["D:", "Fotos", "Handys", "x", "y.jpg"])
        assert space_of(pfad, root) == "Handys"


class TestPapierkorbUeberlebtReIngest:
    def test_stempel_wird_uebernommen(self):
        record = PhotoRecord(photo_id="a", file_path="/mnt/photo/Fotos/a.jpg")
        _apply_prior(record, {"trashed_at": "2026-08-30T12:00:00Z"})
        assert record.trashed_at == "2026-08-30T12:00:00Z"

    def test_und_landet_im_payload(self):
        w = writer_with("/mnt/photo")
        record = PhotoRecord(photo_id="a", file_path="/mnt/photo/Fotos/a.jpg")
        record.trashed_at = "2026-08-30T12:00:00Z"
        w.upsert(record)
        assert w.client.payloads[0]["trashed_at"] == "2026-08-30T12:00:00Z"

    def test_ohne_stempel_steht_der_schluessel_nicht_da(self):
        # Ein durchgereichtes None würde den Stempel löschen -- upsert
        # ersetzt das ganze Payload.
        w = writer_with("/mnt/photo")
        w.upsert(PhotoRecord(photo_id="a", file_path="/mnt/photo/Fotos/a.jpg"))
        assert "trashed_at" not in w.client.payloads[0]

    def test_steht_in_der_liste_der_geschuetzten_felder(self):
        assert "trashed_at" in IngestPipeline.PRESERVE_FIELDS

    def test_space_steht_bewusst_nicht_darin(self):
        # Es wird aus dem Pfad gerechnet. Würde es übernommen, behielte ein
        # verschobenes Foto seinen alten Bereich.
        assert "space" not in IngestPipeline.PRESERVE_FIELDS


class TestIndizes:
    def test_bereich_und_papierkorb_sind_dabei(self):
        felder = dict(QdrantWriter.PHOTO_INDEXES)
        assert felder["space"] == "KEYWORD"
        assert felder["trashed_at"] == "DATETIME"

    def test_bestehende_collection_bekommt_sie_nachtraeglich(self):
        # Vorher lief die Index-Schleife nur beim Anlegen: ein später
        # hinzugekommener Index erreichte eine bestehende Installation nie.
        w = writer_with("/mnt/photo")
        w._ensure_photo_indexes()
        assert "space" in w.client.indexes
        assert "trashed_at" in w.client.indexes
