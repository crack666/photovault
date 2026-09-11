import pytest

from ingest.captioner import _parse_json, build_caption_prompt, unwrap_caption


@pytest.fixture(autouse=True)
def _ohne_litellm(monkeypatch):
    monkeypatch.delenv("LITELLM_URL", raising=False)
    monkeypatch.delenv("PHOTOVAULT_EMBED_URL", raising=False)


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


def test_unwrap_plain_sentence():
    assert unwrap_caption("Ein Hund im Schnee.") == "Ein Hund im Schnee."


def test_unwrap_valid_json_blob():
    raw = '{"caption_de": "Ein Screenshot einer WhatsApp-Nachricht.", "scene_tags": ["screenshot"]}'
    assert unwrap_caption(raw) == "Ein Screenshot einer WhatsApp-Nachricht."


def test_unwrap_truncated_json():
    raw = (
        '{"caption_de": "Ein Screenshot einer WhatsApp-Nachricht vom 10. November 2024, '
        'die eine Zahlungsaufforderung für eine Ferienmiete in Griechenland (Sounio)'
    )
    assert "Sounio" in unwrap_caption(raw)
    assert not unwrap_caption(raw).startswith("{")


def test_unwrap_nested_json_string():
    inner = '{"caption_de": "Zwei Personen im Garten.", "scene_tags": []}'
    outer = '{"caption_de": ' + _json_string(inner) + '}'
    assert unwrap_caption(outer) == "Zwei Personen im Garten."


def test_unwrap_json_without_caption_is_empty():
    assert unwrap_caption('{"scene_tags": ["screenshot"]}') == ""


def test_unwrap_empty():
    assert unwrap_caption(None) == ""
    assert unwrap_caption("  ") == ""


def test_caption_structured_recovers_truncated_json(monkeypatch):
    from ingest.captioner import Captioner

    truncated = (
        '{"caption_de": "Ein Screenshot einer WhatsApp-Nachricht vom 10. November 2024, '
        'die eine Zahlungsaufforderung für Sounio enthält.'
    )

    def fake_post(url, payload, timeout=180):
        return {"message": {"content": truncated}}

    monkeypatch.setattr("ingest.captioner.post_json", fake_post)
    result = Captioner().caption_structured("/gibt/es/nicht.jpg", {}, image_b64="AAAA")
    assert result is not None
    assert result["caption_de"].startswith("Ein Screenshot")
    assert "Sounio" in result["caption_de"]
    assert not result["caption_de"].startswith("{")


def test_video_prompt_asks_for_a_clip_not_a_still():
    text = build_caption_prompt({"kind": "video", "folder_name": "HandyPics"})
    assert "Video" in text
    assert "Einzelbilder" in text
    assert "Analysiere das Foto." not in text


def _json_string(value: str) -> str:
    import json
    return json.dumps(value)
