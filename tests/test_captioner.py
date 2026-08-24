from ingest.captioner import _parse_json


def test_parse_json_object():
    data = _parse_json('{"caption_de": "Hallo", "scene_tags": ["strand"]}')
    assert data["caption_de"] == "Hallo"


def test_parse_json_fenced():
    raw = 'Hier:\n```json\n{"caption_de": "x", "scene_tags": []}\n```'
    data = _parse_json(raw)
    assert data is not None
    assert data["caption_de"] == "x"


def test_parse_json_invalid():
    assert _parse_json("kein json") is None
