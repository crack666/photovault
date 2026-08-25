from tools.privacy_check import hook_script


def test_hook_finds_python3_when_python_is_missing():
    text = hook_script("--staged")
    assert "command -v python3" in text
    assert '"$PY" -m tools.privacy_check --staged' in text
    assert text.startswith("#!/bin/sh")
