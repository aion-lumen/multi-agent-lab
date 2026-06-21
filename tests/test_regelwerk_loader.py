"""Regelwerk YAML structure validation."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_regelwerk_yaml_loads_and_has_voice_consensus():
    import yaml

    path = REPO / "config" / "regelwerk.yaml"
    assert path.exists(), "regelwerk.yaml must exist in repo"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "voice_consensus" in data
    voices = data["voice_consensus"].get("voices") or []
    assert len(voices) >= 3
    ids = {v["id"] for v in voices}
    assert "gemma-control" in ids
    assert "qwen-validator" in ids


def test_load_regelwerk_from_domain_actionability():
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    from domain_actionability import load_regelwerk, validate_regelwerk_against_context, load_user_context

    rw = load_regelwerk()
    assert rw.get("action_definitions")
    validate_regelwerk_against_context(rw, load_user_context())
