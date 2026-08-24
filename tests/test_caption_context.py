from ingest.captioner import build_caption_prompt, format_context, grounded_text


def _ctx():
    return {
        "folder_name": "Griechenland 2015",
        "date": "2015-01-01",
        "date_source": "folder",
        "file_ctime": "2015-07-20T10:00:00+00:00",
        "filename": "IMG_0042.jpg",
        "sequence": 42,
        "location": "Griechenland 2015",
        "face_count": 2,
        "people_assigned": [],
        "people_album": ["Jonas", "Max"],
        "clip_tags": ["strand", "palme"],
    }


def test_context_has_album_not_just_pixels():
    block = format_context(_ctx())
    assert "Griechenland 2015" in block
    assert "Sequenz 42" in block
    assert "Jonas" in block
    assert "Erkannte Gesichter: 2" in block
    assert "Datei erstellt" in block


def test_prompt_forbids_invented_names():
    prompt = build_caption_prompt(_ctx())
    assert "KONTEXT:" in prompt
    assert "erfinde keine namen" in prompt.lower()


def test_grounded_text_for_qdrant():
    text = grounded_text(_ctx(), "Zwei Maenner vor Palmen.")
    assert "Ordner: Griechenland 2015" in text
    assert "Sequenz: 42" in text
    assert "Personen: Jonas, Max" in text
    assert "Zwei Maenner vor Palmen." in text


def test_assigned_names_beat_album_in_grounded_text():
    ctx = _ctx()
    ctx["people_assigned"] = ["Mareike"]
    text = grounded_text(ctx, None)
    assert "Personen: Mareike" in text
    assert "Jonas" not in text
