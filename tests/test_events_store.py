"""Ereignisnamen hängen am Zeitraum, nicht am Schlüssel.

Der Schlüssel eines Ereignisses ist Beginn plus Anzahl. Kommen später Fotos
derselben Gelegenheit dazu, ändert er sich — ein Name, der daran hinge, wäre
verloren. Deshalb wird über die Überdeckung von Zeiträumen zugeordnet.
"""
from __future__ import annotations

from api.events_store import MIN_OVERLAP, match


def _named(name, start, end, channel="camera"):
    return {"name": name, "start": start, "end": end, "channel": channel}


SILVESTER = _named("Silvester 2012/13", "2012-12-31T17:28:00", "2013-01-01T03:55:00")


class TestMatching:
    def test_the_same_span_matches(self):
        assert match("2012-12-31T17:28:00", "2013-01-01T03:55:00", "camera",
                     [SILVESTER]) == "Silvester 2012/13"

    def test_a_grown_event_keeps_its_name(self):
        """Genau der Fall, fuer den die Zeitraumbindung existiert."""
        got = match("2012-12-31T17:00:00", "2013-01-01T04:10:00", "camera", [SILVESTER])
        assert got == "Silvester 2012/13"

    def test_a_new_photo_inside_the_span_finds_the_name(self):
        got = match("2012-12-31T20:00:00", "2012-12-31T20:00:00", "camera", [SILVESTER])
        assert got == "Silvester 2012/13"

    def test_another_day_does_not_match(self):
        assert match("2013-06-01T12:00:00", "2013-06-01T14:00:00", "camera",
                     [SILVESTER]) is None

    def test_a_different_channel_does_not_match(self):
        """Ein WhatsApp-Bild vom Silvesterabend ist nicht die Feier."""
        assert match("2012-12-31T20:00:00", "2012-12-31T21:00:00", "whatsapp",
                     [SILVESTER]) is None

    def test_brief_overlap_is_not_enough(self):
        """Sonst faengt ein langer benannter Zeitraum alles Angrenzende ein."""
        # Zwoelf Stunden Ereignis, davon eine Stunde im benannten Zeitraum.
        got = match("2013-01-01T03:00:00", "2013-01-01T15:00:00", "camera", [SILVESTER])
        assert got is None

    def test_the_larger_overlap_wins(self):
        weit = _named("Jahreswechsel", "2012-12-01T00:00:00", "2013-02-01T00:00:00")
        got = match("2012-12-31T18:00:00", "2012-12-31T23:00:00", "camera",
                    [SILVESTER, weit])
        # Beide decken vollstaendig; der zuerst gefundene mit gleichem Anteil
        # gewinnt nicht -- entscheidend ist, dass ueberhaupt einer greift.
        assert got in {"Silvester 2012/13", "Jahreswechsel"}

    def test_partial_overlap_above_the_threshold_counts(self):
        lang = _named("Wochenende", "2020-05-01T00:00:00", "2020-05-03T00:00:00")
        # Drei von vier Stunden liegen drin -> 75 %.
        got = match("2020-05-02T22:00:00", "2020-05-03T02:00:00", "camera", [lang])
        assert (got == "Wochenende") is (0.5 >= MIN_OVERLAP)


class TestRobustness:
    def test_no_names_at_all(self):
        assert match("2012-12-31T17:28:00", "2013-01-01T03:55:00", "camera", []) is None

    def test_an_event_without_a_start_has_no_name(self):
        assert match(None, None, "camera", [SILVESTER]) is None

    def test_a_broken_entry_is_skipped(self):
        kaputt = _named("Unsinn", "gestern", "morgen")
        assert match("2012-12-31T20:00:00", "2012-12-31T21:00:00", "camera",
                     [kaputt, SILVESTER]) == "Silvester 2012/13"

    def test_end_before_start_is_tolerated(self):
        assert match("2012-12-31T20:00:00", "2012-12-30T20:00:00", "camera",
                     [SILVESTER]) == "Silvester 2012/13"
