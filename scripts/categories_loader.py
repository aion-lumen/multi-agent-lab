#!/usr/bin/env python3
"""Load triage category definitions from config/categories.yaml.

Categories drive domain detection (domain_actionability), validator prompts,
and pilot-specific domain sets (e.g. categories.pilot-praxis.yaml).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("categories_loader")

_CACHE: dict[str, "CategoriesConfig"] = {}


@dataclass
class CategoryDef:
    key: str
    label: str
    description: str
    sender_domains: tuple[str, ...] = ()
    subject_keywords: tuple[str, ...] = ()
    priority_subject_keywords: tuple[str, ...] = ()
    sender_prefixes: tuple[str, ...] = ()
    domain_tokens: tuple[str, ...] = ()
    # Recipient/To addresses that force this category (highest-confidence signal,
    # e.g. a dedicated alias like freelance@mirhamed.ch). Matched before sender heuristics.
    recipient_aliases: tuple[str, ...] = ()
    default_actionability: str = "actionable"
    priority_boost: str | None = None
    match_non_bulk_sender: bool = False
    is_fallback: bool = False


@dataclass
class CategoriesConfig:
    schema_version: str
    fallback_domain: str
    categories: list[CategoryDef] = field(default_factory=list)

    def keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.categories)

    def has(self, key: str) -> bool:
        return key in self.keys()

    def get(self, key: str) -> CategoryDef | None:
        for c in self.categories:
            if c.key == key:
                return c
        return None


def _tuple_list(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(x) for x in raw if x)
    return (str(raw),)


def _parse_category(raw: dict) -> CategoryDef:
    return CategoryDef(
        key=str(raw["key"]),
        label=str(raw.get("label") or raw["key"]),
        description=str(raw.get("description") or raw.get("label") or raw["key"]),
        sender_domains=_tuple_list(raw.get("sender_domains")),
        subject_keywords=_tuple_list(raw.get("subject_keywords")),
        priority_subject_keywords=_tuple_list(raw.get("priority_subject_keywords")),
        sender_prefixes=_tuple_list(raw.get("sender_prefixes")),
        domain_tokens=_tuple_list(raw.get("domain_tokens")),
        recipient_aliases=_tuple_list(raw.get("recipient_aliases")),
        default_actionability=str(raw.get("default_actionability") or "actionable"),
        priority_boost=raw.get("priority_boost"),
        match_non_bulk_sender=bool(raw.get("match_non_bulk_sender")),
        is_fallback=bool(raw.get("is_fallback")),
    )


def _default_categories_config() -> CategoriesConfig:
    """Minimal built-in fallback when categories.yaml is missing."""
    return CategoriesConfig(
        schema_version="v1",
        fallback_domain="unsorted",
        categories=[
            CategoryDef("unsorted", "Unsortiert", "Kein klares Match",
                        is_fallback=True, default_actionability="actionable"),
        ],
    )


def _resolve_categories_path(path: Path | None) -> Path:
    if path is not None:
        return path
    from paths import CONFIG_DIR  # noqa: PLC0415
    return Path(os.environ.get("CATEGORIES_YAML", str(CONFIG_DIR / "categories.yaml")))


def load_categories(path: Path | None = None) -> CategoriesConfig:
    """Load categories YAML; fallback to built-in default set."""
    import yaml  # local import

    resolved = _resolve_categories_path(path)
    cache_key = str(resolved.resolve())
    if path is None and cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        if not resolved.exists():
            log.info("categories config missing at %s — using built-in default", resolved)
            cfg = _default_categories_config()
            if path is None:
                _CACHE[cache_key] = cfg
            return cfg
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        cats_raw = raw.get("categories") or []
        categories = [_parse_category(c) for c in cats_raw if isinstance(c, dict) and c.get("key")]
        if not categories:
            log.warning("categories config empty — using built-in default")
            cfg = _default_categories_config()
        else:
            cfg = CategoriesConfig(
                schema_version=str(raw.get("schema_version") or "v1"),
                fallback_domain=str(raw.get("fallback_domain") or categories[-1].key),
                categories=categories,
            )
        if path is None:
            _CACHE[cache_key] = cfg
        return cfg
    except Exception as e:  # noqa: BLE001
        log.warning("categories load failed (%s) — using built-in default", e)
        return _default_categories_config()


def get_domain_keys(path: Path | None = None) -> tuple[str, ...]:
    return load_categories(path).keys()


def has_category(key: str, path: Path | None = None) -> bool:
    return load_categories(path).has(key)


def build_lens_domain_section(cfg: CategoriesConfig | None = None) -> str:
    cfg = cfg or load_categories()
    lines = ["Domain (genau EINE):"]
    for c in cfg.categories:
        if c.is_fallback:
            continue
        lines.append(f"  - {c.key:<24} ({c.description})")
    fb = cfg.get(cfg.fallback_domain)
    fb_desc = fb.description if fb else "kein klares Match"
    lines.append(f"  - {cfg.fallback_domain:<24} ({fb_desc})")
    return "\n".join(lines)


IMMO_SUBSTANCE_RULES = """\
WICHTIG — Substanz-Definition für domain=immo (Bauteil 8):
Eine Mail ist nur dann domain=immo, wenn sie sich auf EIN KONKRETES
OBJEKT bezieht — erkennbar an mindestens ZWEI der folgenden Stammdaten:
  - Adresse oder PLZ
  - Preis (€/CHF)
  - qm (Wohnfläche)
  - Inserat-URL mit /expose/, /Expose/, /Detail/ oder ähnlichem Pattern
Ratgeber-Artikel, Portal-Newsletter ("Gemeinderatgeber", "Markt-Übersicht"),
Marketing-Mails ohne konkretes Objekt → domain=werbung, NICHT immo.
Themen-Relevanz allein reicht NICHT. "Gemeinderatgeber Homegate" ist
immo-themen-relevant aber kein immo, weil kein konkretes Objekt dahinter.

Beispiele:
  - Positiv (immo):  "1 neue Immobilie: Reihenhaus, Rua das Oliveiras 18,
    8100 Loulé, 4 Zimmer, 120 qm, 450.000 EUR. /expose/123456"
    → domain=immo, actionability=actionable
  - Negativ (werbung): "Portal-Marktbericht: Preise im Algarve steigen.
    Lesen Sie unsere Analyse." → domain=werbung, actionability=archive-silent
"""


def build_lens_prompt_template(cfg: CategoriesConfig | None = None) -> str:
    """Return LENS_PROMPT with dynamic domain list (+ immo rules when applicable)."""
    cfg = cfg or load_categories()
    domain_section = build_lens_domain_section(cfg)
    immo_block = IMMO_SUBSTANCE_RULES if cfg.has("immo") else ""
    werbung_key = "werbung" if cfg.has("werbung") else cfg.fallback_domain
    return f"""\
Du bist ein blinder Klassifikator für E-Mail-Triage. Du arbeitest unabhängig —
es gibt keine vorherigen Urteile, kein Plugin-Hint, keine Heuristik-Vorgabe.
Klassifiziere die E-Mail nur aus dem, was du selbst siehst, auf 2 Achsen:
domain × actionability.

{domain_section}

{immo_block}
Actionability (genau EINE — Definitionen aus zentralem Regelwerk):
{{actionability_block}}

User-Context (relevant für Priorisierung):
{{user_context_block}}

E-Mail:
  Sender:  {{sender}}
  Subject: {{subject}}
  Body (erste 1000 Zeichen):
{{body}}

Achte besonders auf:
  - Mails von Job/Immo-Portalen sind oft `{werbung_key}`/`archive-silent` AUSSER User hat
    aktive Priorität (z.B. hauskauf → immo bleibt `actionable`).
  - Paketzustellung-Mails sind IMMER `actionable` (User muss Lieferung wahrnehmen).
  - Private Personen ohne Bulk-Sender-Prefix sind `kontakt` + `actionable` (falls Kategorie existiert).
  - Newsletter/Marketing/Promo-Mails sind `{werbung_key}` + `archive-silent` (default),
    AUSSER sie sind zeitkritisch (Sale läuft ab in 24h → actionable).

Antworte AUSSCHLIESSLICH als JSON:
{{{{"domain": "<domain>", "actionability": "<actionability>", "confidence": <0.0-1.0>, "reasoning": "<max 200 Zeichen>"}}}}
"""
