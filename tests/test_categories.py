"""Tests for config-driven categories (pilot + default)."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config"


def test_default_categories_load():
    sys_path = REPO_ROOT / "scripts"
    import sys
    sys.path.insert(0, str(sys_path))
    from categories_loader import load_categories  # noqa: PLC0415

    cfg = load_categories(CONFIG / "categories.yaml")
    keys = cfg.keys()
    assert "immo" in keys
    assert "unsorted" in keys
    assert cfg.fallback_domain == "unsorted"


def test_pilot_praxis_categories_load():
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from categories_loader import load_categories  # noqa: PLC0415

    cfg = load_categories(CONFIG / "categories.pilot-praxis.yaml")
    assert cfg.has("therapieplatz-anfrage")
    assert cfg.has("sonstiges")
    assert cfg.fallback_domain == "sonstiges"


def test_pilot_domain_detection(monkeypatch):
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import categories_loader as cl  # noqa: PLC0415
    from domain_actionability import classify_domain_actionability  # noqa: PLC0415

    pilot_path = CONFIG / "categories.pilot-praxis.yaml"
    monkeypatch.setenv("CATEGORIES_YAML", str(pilot_path))
    cl._CACHE = {}

    r1 = classify_domain_actionability(
        "anna.muster@example.invalid",
        "Anfrage Therapieplatz — Erstgespräch",
        "2026-07-07T08:00:00+00:00",
    )
    assert r1.domain == "therapieplatz-anfrage"

    r2 = classify_domain_actionability(
        "abrechnung@css.ch",
        "Abrechnung Q2/2026",
        "2026-07-07T08:00:00+00:00",
    )
    assert r2.domain == "organisatorisches-abrechnung"

    r3 = classify_domain_actionability(
        "newsletter@praxis-tools.invalid",
        "Newsletter: 20% Rabatt",
        "2026-07-07T08:00:00+00:00",
    )
    assert r3.domain == "sonstiges"

    cl._CACHE = {}


def test_lens_prompt_includes_pilot_domains():
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from categories_loader import build_lens_prompt_template, load_categories  # noqa: PLC0415

    tpl = build_lens_prompt_template(load_categories(CONFIG / "categories.pilot-praxis.yaml"))
    assert "therapieplatz-anfrage" in tpl
    assert "domain=immo" not in tpl  # no immo rules in pilot set
