#!/usr/bin/env python3
"""immo_heuristic.py — Four-tier classification on top of the email-classification plugin.

Komplementär zu domain_actionability.py (F.8 domain×action-Achse), nicht ablösend:
Tier-0–3 Immo-Spezialfälle + Portal-Marker; domain_actionability nutzt Plugin-
Class als Signal-Feature in der 6-Step-Pipeline.

Design source: state/prompt-o1-design-notes.md §G2 (Heuristik-Design) + Gate-2-signoff
refinements R1-R6 + CC-Update Paketzustellung Tier 0 (2026-05-16).

Public API:
    HeuristicResult         — dataclass, output shape (Prompt §5.1)
    classify_immo(...)      — main entry point

Logic outline:
    Tier 0: Paketzustellung (Logistiker domain alone OR Shopping domain + keyword).
            Deterministic, plugin-agnostic; suggested_action = move_paketzustellung.
    Tier 1: Portal sender pattern (from config/portals.yaml → fallback to hardcoded)
            triggers continuation into Tier 2.
    Tier 2: Out-of-scope check (price > CHF 1M, location whitelist, location detected
            but not in whitelist). Active when Tier-1 hit OR body contains Immo keywords.
    Tier 3: Privat-Immo detection via marker scoring (positive + anti markers).

Fallback: suggested_action = "keep", confidence = "high", reason = "keine Immo-Indikatoren".
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

import yaml

# In-repo reuse: extract_email_address + extract_domain (NOT life-mail, fair game).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sender_heuristic import extract_domain, extract_email_address  # noqa: E402

log = logging.getLogger("immo_heuristic")

from paths import IMMO_WHITELIST_YAML, PORTALS_YAML, REPO_ROOT  # noqa: E402

WHITELIST_PATH = IMMO_WHITELIST_YAML
PORTALS_PATH = PORTALS_YAML

# Fallback portal-domain list per Prompt §5.2 Tier 1.
FALLBACK_PORTAL_DOMAINS: tuple[str, ...] = (
    "homegate.ch",
    "immoscout24.ch",
    "newhome.ch",
    "comparis.ch",
    "immobilienscout24.de",
    "immowelt.de",
)

# 2026-06-05 Filter-Zentralisierung: PRICE_THRESHOLD + Block-Patterns
# leben in regelwerk.filters.hauskauf (thresholds.price_max + block_patterns).
# Lazy-load + module-level cache wegen classify_immo-Frequenz.
PRICE_THRESHOLD_FALLBACK = 500_000
_PRICE_THRESHOLD_CACHE: int | None = None
_BLOCK_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _get_price_threshold() -> int:
    global _PRICE_THRESHOLD_CACHE
    if _PRICE_THRESHOLD_CACHE is None:
        try:
            from domain_actionability import get_filter_config  # noqa: PLC0415 lazy
            _PRICE_THRESHOLD_CACHE = int(
                get_filter_config("hauskauf")["thresholds"]["price_max"]
            )
        except Exception:  # noqa: BLE001 — fallback ist explizit gewollt
            _PRICE_THRESHOLD_CACHE = PRICE_THRESHOLD_FALLBACK
    return _PRICE_THRESHOLD_CACHE


def _get_block_pattern_re(marker_name: str) -> re.Pattern[str] | None:
    """Lazy-compile + cache Block-Pattern-Regex aus
    regelwerk.filters.hauskauf.block_patterns[marker_name].

    Liefert None wenn Marker im regelwerk unbekannt (kein Filter aktiv).
    Pattern: \\b(token1|token2|...)\\b case-insensitive, tokens
    re.escape'd. Tokens-Reihenfolge: laengste zuerst (Backtracking-Schutz
    fuer alternation prefixes — z.B. 'zwangsversteigerungstermin' vor
    'zwangsversteigerung').
    """
    if marker_name in _BLOCK_PATTERN_CACHE:
        return _BLOCK_PATTERN_CACHE[marker_name]
    try:
        from domain_actionability import get_filter_config  # noqa: PLC0415 lazy
        tokens = (get_filter_config("hauskauf").get("block_patterns") or {}).get(marker_name) or []
    except Exception:  # noqa: BLE001
        tokens = []
    if not tokens:
        return None
    sorted_tokens = sorted(tokens, key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in sorted_tokens)
    compiled = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)
    _BLOCK_PATTERN_CACHE[marker_name] = compiled
    return compiled


# Bauteil-7 G1 (2026-06-09) Marker-Validation: Footer-Detektion via
# Phrasen-Anker. Fallback: letzte 15% des haystack. Footer-Start wird
# einmal pro Mail berechnet, dann pro Marker-Match auf in_footer
# geprueft. Phrasen sind case-insensitive substring-matches.
_FOOTER_PHRASES = (
    "mit freundlichen grüßen",
    "mit freundlichen gruessen",
    "mit besten grüßen",
    "mit besten gruessen",
    "beste grüße",
    "beste gruesse",
    "freundliche grüße",
    "freundliche gruesse",
    "viele grüße",
    "viele gruesse",
    "<hr",
    "abmelden",
    "unsubscribe",
    "impressum",
    "newsletter abbestellen",
)


def _compute_footer_start(haystack: str) -> int:
    """G1 Footer-Heuristik V1: Position im haystack, ab der Footer
    beginnt. Frueheste Footer-Phrase gewinnt. Fallback: letzte 15%."""
    if not haystack:
        return 0
    lower = haystack.lower()
    positions = [pos for pos in (lower.find(p) for p in _FOOTER_PHRASES) if pos >= 0]
    if positions:
        return min(positions)
    return int(len(haystack) * 0.85)


def _get_block_pattern_validation(marker_name: str) -> dict:
    """Liest block_patterns_validation[marker_name] aus regelwerk.
    Liefert {} bei fehlendem Eintrag (kein Disqualify)."""
    try:
        from domain_actionability import get_filter_config  # noqa: PLC0415 lazy
        validation = (get_filter_config("hauskauf").get("block_patterns_validation") or {})
    except Exception:  # noqa: BLE001
        return {}
    return validation.get(marker_name) or {}


# Bauteil-8 A2 (2026-06-09): Inserat-URL-Pattern pro Portal-Domain.
# Lazy-compiled regex-Cache analog _BLOCK_PATTERN_CACHE. Pattern leben
# in regelwerk.filters.inserat_url_patterns.<portal_domain>. Subdomain-
# Matches via endswith (z.B. 'mail.notification.homegate.ch' matched
# 'homegate.ch'-Eintrag).
_INSERAT_URL_PATTERN_CACHE: dict[str, list[re.Pattern[str]]] = {}


def _get_inserat_url_patterns(sender_domain: str) -> list[re.Pattern[str]]:
    """Liefert kompilierte Inserat-URL-Pattern fuer sender_domain.
    Empty list = kein Pattern konfiguriert (keine no_inserat_url-Pruefung).
    Pattern leben in regelwerk.filters.hauskauf.inserat_url_patterns."""
    if sender_domain in _INSERAT_URL_PATTERN_CACHE:
        return _INSERAT_URL_PATTERN_CACHE[sender_domain]
    try:
        from domain_actionability import get_filter_config  # noqa: PLC0415 lazy
        all_patterns = (get_filter_config("hauskauf").get("inserat_url_patterns") or {})
    except Exception:  # noqa: BLE001
        _INSERAT_URL_PATTERN_CACHE[sender_domain] = []
        return []
    # Match: sender_domain == key OR sender_domain endet auf '.' + key
    matched_keys = [
        k for k in all_patterns
        if sender_domain == k or sender_domain.endswith("." + k)
    ]
    if not matched_keys:
        _INSERAT_URL_PATTERN_CACHE[sender_domain] = []
        return []
    compiled: list[re.Pattern[str]] = []
    for key in matched_keys:
        for pat_str in (all_patterns[key] or []):
            try:
                compiled.append(re.compile(pat_str, re.IGNORECASE))
            except re.error:
                log.warning("invalid inserat_url_pattern %r for %s", pat_str, key)
    _INSERAT_URL_PATTERN_CACHE[sender_domain] = compiled
    return compiled


def _is_marker_disqualified(
    marker_name: str,
    subject: str,
    sender_email: str,
    match_pos: int,
    footer_start: int,
) -> bool:
    """G1 Marker-Validation: prueft disqualify_if-Regeln. True wenn
    Marker im aktuellen Kontext disqualifiziert wird (Footer-Match,
    Auto-Reply-Subject-Prefix, Newsletter-Sender-Pattern)."""
    rules = _get_block_pattern_validation(marker_name)
    disq = rules.get("disqualify_if") or []
    for rule in disq:
        if not isinstance(rule, dict):
            continue
        if rule.get("in_footer") and match_pos >= footer_start:
            return True
        prefixes = rule.get("in_subject_prefix")
        if prefixes and subject:
            subj_low = subject.lower()
            for p in prefixes:
                if subj_low.startswith(p.lower()):
                    return True
        sender_pat = rule.get("sender_pattern")
        if sender_pat and sender_email:
            try:
                if re.match(sender_pat, sender_email, re.IGNORECASE):
                    return True
            except re.error:
                continue
    return False


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass
class HeuristicResult:
    """Outcome of classify_immo. Matches Prompt §5.1 + CC-Update Paketzustellung."""

    suggested_action: Literal[
        "keep",
        "move_immo_portal",
        "move_immo_privat",
        "move_zu_pruefen",
        "move_paketzustellung",
    ]
    reason: str
    confidence: Literal["high", "medium", "low"]
    matched_markers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Portal-domain loading (Tier 1 cache)
# ---------------------------------------------------------------------------


_portal_domains: set[str] | None = None


def load_portal_domains() -> set[str]:
    """Parse council's portals.yaml for the canonical portal-domain list.

    Falls back to FALLBACK_PORTAL_DOMAINS if the file is unreadable. Cached.
    """
    global _portal_domains
    if _portal_domains is not None:
        return _portal_domains
    try:
        data = yaml.safe_load(PORTALS_PATH.read_text(encoding="utf-8"))
        portals = data.get("portals") or []
        domains = {p["domain"].lower() for p in portals if p.get("domain")}
        if domains:
            _portal_domains = domains
            return _portal_domains
        raise ValueError("portals.yaml has no domain entries")
    except (OSError, KeyError, ValueError, yaml.YAMLError) as e:
        log.warning(
            "Cannot read portal domains from %s (%s); falling back to hardcoded list.",
            PORTALS_PATH,
            e,
        )
        _portal_domains = set(FALLBACK_PORTAL_DOMAINS)
        return _portal_domains


# ---------------------------------------------------------------------------
# Whitelist loading (Tier 2 cache)
# ---------------------------------------------------------------------------


_whitelist_cache: tuple[set[str], dict[str, re.Pattern]] | None = None


def load_whitelist() -> tuple[set[str], dict[str, re.Pattern]]:
    """Load location whitelist from config/immo_whitelist.yaml.

    Returns (names, disambiguators_compiled). Cached on first call.
    """
    global _whitelist_cache
    if _whitelist_cache is not None:
        return _whitelist_cache
    data = yaml.safe_load(WHITELIST_PATH.read_text(encoding="utf-8"))
    names = set(data.get("ch_side") or []) | set(data.get("de_side") or [])
    raw_dis = data.get("disambiguators") or {}
    compiled = {k: re.compile(v, re.IGNORECASE) for k, v in raw_dis.items()}
    _whitelist_cache = (names, compiled)
    return _whitelist_cache


# ---------------------------------------------------------------------------
# Tier 0 — Paketzustellung loading + matcher (CC-Update 2026-05-16)
# ---------------------------------------------------------------------------


_paketzustellung_cache: tuple[set[str], set[str], re.Pattern] | None = None


def load_paketzustellung_config() -> tuple[set[str], set[str], re.Pattern]:
    """Load the paketzustellung: block from immo_whitelist.yaml.

    Returns (logistiker_set, shopping_set, keyword_pattern). Cached on first call.
    Keyword pattern is case-insensitive, word-boundary-anchored (D-U5).
    """
    global _paketzustellung_cache
    if _paketzustellung_cache is not None:
        return _paketzustellung_cache
    data = yaml.safe_load(WHITELIST_PATH.read_text(encoding="utf-8"))
    pz = data.get("paketzustellung") or {}
    logistiker = {d.lower() for d in (pz.get("logistiker_domains") or [])}
    shopping = {d.lower() for d in (pz.get("shopping_domains") or [])}
    kws_de = list(pz.get("shopping_keywords", {}).get("de") or [])
    kws_en = list(pz.get("shopping_keywords", {}).get("en") or [])
    # Sort longest-first so multi-word phrases ("out for delivery") win over
    # their substrings ("delivery") at the regex level.
    all_kws = sorted({k for k in (kws_de + kws_en) if k}, key=len, reverse=True)
    if all_kws:
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(k) for k in all_kws) + r")\b",
            re.IGNORECASE,
        )
    else:
        pattern = re.compile(r"(?!x)x")  # never matches
    _paketzustellung_cache = (logistiker, shopping, pattern)
    return _paketzustellung_cache


def _matches_paketzustellung(
    sender_field: str,
    subject: str,
    body: str,
) -> tuple[bool, str, list[str]]:
    """Return (matched, reason, markers) for the Tier-0 check.

    Logic:
      - Logistiker domain match → True, regardless of subject/body (D-U7)
      - Shopping domain + at least one keyword match in subject OR body → True (D-U6)
      - Otherwise False
    """
    logistiker, shopping, kw_pattern = load_paketzustellung_config()
    email = extract_email_address(sender_field)
    if not email:
        return False, "", []
    domain = extract_domain(email).lower()
    if not domain:
        return False, "", []
    if domain in logistiker:
        return (
            True,
            f"Logistik-Domain: {domain}",
            [f"paketzustellung:logistiker:{domain}"],
        )
    if domain in shopping:
        # Check subject first (cheaper), then body
        for source_name, text in (("subject", subject or ""), ("body", body or "")):
            m = kw_pattern.search(text)
            if m:
                kw = m.group(1)
                return (
                    True,
                    f"Shopping-Versand: {domain} + {kw}",
                    [
                        f"paketzustellung:shopping:{domain}",
                        f"paketzustellung:keyword:{kw}",
                    ],
                )
    return False, "", []


# ---------------------------------------------------------------------------
# Tier 2 — price detection
# ---------------------------------------------------------------------------

# Currency-before-amount and amount-before-currency variants.
_CURRENCY_PREFIX_RE = re.compile(
    r"(?P<curr>CHF|EUR|€|Fr\.?)"
    r"\s*"
    r"(?P<amount>\d{1,3}(?:[.\s']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,3})?)"
    r"(?:\s*(?P<scale>Mio\.?|Mrd\.?|Tsd\.?))?",
    re.IGNORECASE,
)

_CURRENCY_SUFFIX_RE = re.compile(
    r"(?P<amount>\d{1,3}(?:[.\s']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,3})?)"
    r"\s*"
    r"(?P<curr>CHF|EUR|€|Fr\.?)",
    re.IGNORECASE,
)

# R1: HINT list per Gate-2-signoff includes richtpreis + verhandlungsbasis.
_PRICE_HINT_RE = re.compile(
    r"(?i)\b("
    r"kaufpreis|verkaufspreis|verkauf|angebotspreis|preis|kostet|"
    r"investitionssumme|gesamtpreis|"
    r"richtpreis|verhandlungsbasis"  # R1
    r")\b"
    r"|für\s+(?:CHF|EUR|€)"
)

# R2: EXCLUDE list per Gate-2-signoff includes bruttomiete|nettomiete|tragbarkeit.
_EXCLUDE_NEIGHBORHOOD_RE = re.compile(
    r"(?i)\b("
    r"hypothek|nebenkosten|eigenkapital|mwst|miete|mietzins|kaution|"
    r"bruttomiete|nettomiete|tragbarkeit"  # R2
    r")\b"
)

_SCALE_MULTIPLIER = {
    "tsd": 1_000,
    "mio": 1_000_000,
    "mrd": 1_000_000_000,
}


def _parse_amount(amount: str, scale: str | None) -> int | None:
    """Normalise a numeric token + optional scale to an integer."""
    text = amount.strip()
    has_dot = "." in text
    has_comma = "," in text
    has_apos = "'" in text or "’" in text or " " in text
    try:
        if has_dot and has_comma:
            # DE format 1.500.000,00 → 1500000
            cleaned = text.replace(".", "").replace(",", ".")
            val = float(cleaned)
        elif has_dot and not has_comma:
            # Either DE thousands "1.500.000" or decimal "1.5"
            parts = text.split(".")
            if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
                cleaned = text.replace(".", "")
                val = float(cleaned)
            else:
                val = float(text)
        elif has_comma:
            cleaned = text.replace(",", ".")
            val = float(cleaned)
        elif has_apos:
            cleaned = re.sub(r"['’\s]", "", text)
            val = float(cleaned)
        else:
            val = float(text)
    except ValueError:
        return None
    if scale:
        key = scale.rstrip(".").lower()
        mult = _SCALE_MULTIPLIER.get(key)
        if mult:
            val *= mult
    return int(val)


@dataclass
class _PriceHit:
    amount: int
    currency: str
    span_start: int
    span_end: int


def _find_prices(body: str) -> list[_PriceHit]:
    """Find all currency-tagged amounts in the body."""
    hits: list[_PriceHit] = []
    seen_spans: set[tuple[int, int]] = set()

    def _curr_norm(raw: str) -> str:
        u = raw.upper().rstrip(".")
        if u == "€":
            return "EUR"
        if u in {"FR", "FR.", "CHF"}:
            return "CHF"
        return u  # CHF / EUR

    for regex in (_CURRENCY_PREFIX_RE, _CURRENCY_SUFFIX_RE):
        for m in regex.finditer(body):
            span = m.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            amount_str = m.group("amount")
            scale = m.groupdict().get("scale")
            amount = _parse_amount(amount_str, scale)
            if amount is None or amount < 10_000:
                # Below 10k: probably not a property price, skip.
                continue
            currency = _curr_norm(m.group("curr"))
            hits.append(_PriceHit(amount=amount, currency=currency,
                                  span_start=span[0], span_end=span[1]))
    return hits


def _select_primary_price(
    body: str,
    hits: list[_PriceHit],
    window: int = 30,
) -> _PriceHit | None:
    """Pick the most likely 'this is the listing price' hit.

    Hybrid strategy per §G2-A.5: prefer hits with a price-hint word in the
    LHS window; exclude hits in the EXCLUDE neighbourhood (Hypothek/Miete/...).
    Fall back to global max if no hint-matched hit survives.

    # LHS-only window per Gate-2-signoff-2026-05-16: only the text BEFORE the
    # amount matters. MWST after the price is intentionally outside scope.
    """
    if not hits:
        return None
    primary: list[_PriceHit] = []
    fallback: list[_PriceHit] = []
    for h in hits:
        lhs = body[max(0, h.span_start - window):h.span_start]
        if _EXCLUDE_NEIGHBORHOOD_RE.search(lhs):
            continue  # excluded entirely
        if _PRICE_HINT_RE.search(lhs):
            primary.append(h)
        else:
            fallback.append(h)
    candidates = primary or fallback
    if not candidates:
        return None
    return max(candidates, key=lambda h: h.amount)


# ---------------------------------------------------------------------------
# Tier 2 — location detection
# ---------------------------------------------------------------------------

_PREPOSITION_LOCATION_RE = re.compile(
    r"\b(?:in|nach|bei|aus|aus\s+der|aus\s+dem)\s+"
    r"([A-ZÄÖÜ][a-zäöüß-]+(?:\s+[A-ZÄÖÜ][a-zäöüß-]+){0,2})"
)


# 2026-05-25 Block 3 — PLZ-Extraktion für Distance-Anzeige im DetailPanel.
# Multi-Strategy: P1 "PLZ Ort", P2 "Ort, (PLZ)", P3 "PLZ alleine" (Fallback,
# CSV-validation). Footer-Skip: last 1/3 of body wird ignoriert (typischer
# Sender-Address-Bereich). First high-confidence match wins.
_PLZ_P1 = re.compile(
    r"\b(\d{4,5})\s+([A-ZÄÖÜ][a-zäöüß-]+(?:[\s-][A-ZÄÖÜ][a-zäöüß-]+){0,2})\b"
)
_PLZ_P2 = re.compile(
    r"\b([A-ZÄÖÜ][a-zäöüß-]+(?:[\s-][A-ZÄÖÜ][a-zäöüß-]+){0,2})[,\s]+\(?(\d{4,5})\)?"
)
_PLZ_P3 = re.compile(r"\b(\d{4,5})\b")


def extract_plz(body: str) -> Optional[dict]:
    """Extract first PLZ (DE 5-digit / CH 4-digit) from body.

    Strategy:
      1. Skip last 1/3 of body (sender-footer heuristic).
      2. Try P1 "PLZ Ort" → lookup PLZ in CSV → if city contains/matches → win.
      3. Try P2 "Ort, (PLZ)" → same validation.
      4. Try P3 "PLZ alone" → CSV-lookup → use city from CSV.
      5. Fall-through → None.

    Returns dict with {plz, city, country, lat, lng} or None.
    """
    from plz_lookup import lookup as _plz_lookup  # lazy import (avoid circular)

    if not body:
        return None

    # Footer-skip removed 2026-05-25 (post-Tranche-2-Test): die alte 2/3-Heuristik
    # hat bei mails mit short body + PLZ in last 1/3 zu False-Negatives geführt.
    # CSV-Validation ist starker false-positive-Filter — wir vertrauen sie und
    # nehmen first valid match anywhere im body. Worst case: sender-footer-PLZ
    # erscheint statt content-PLZ — akzeptabel + immer noch hilfreich.
    head = body

    # Try P1: "PLZ Ort"
    for m in _PLZ_P1.finditer(head):
        plz, city_hint = m.group(1), m.group(2)
        rec = _plz_lookup(plz)
        if not rec:
            continue
        # City-Hint passes if any token matches CSV city (case-insensitive,
        # allow prefix-Match (e.g. "Faro" für "Faro Centro" matched).
        if _city_matches(city_hint, rec["city"]):
            return _as_dict(rec)

    # Try P2: "Ort, (PLZ)"
    for m in _PLZ_P2.finditer(head):
        city_hint, plz = m.group(1), m.group(2)
        rec = _plz_lookup(plz)
        if not rec:
            continue
        if _city_matches(city_hint, rec["city"]):
            return _as_dict(rec)

    # Fallback P3: PLZ alone, take first valid CSV-match
    for m in _PLZ_P3.finditer(head):
        plz = m.group(1)
        rec = _plz_lookup(plz)
        if rec:
            return _as_dict(rec)

    return None


def _city_matches(hint: str, csv_city: str) -> bool:
    """Case-insensitive prefix/substring match between user-hint and CSV city."""
    if not hint or not csv_city:
        return False
    h = hint.lower().strip()
    c = csv_city.lower().strip()
    # Direct match
    if h == c:
        return True
    # CSV city contains hint (e.g. "Faro" in "Faro Centro")
    if h in c:
        return True
    # Hint contains CSV city (rare)
    if c in h:
        return True
    # First-token match (z.B. "Wutöschingen" vs "Horheim, Wutöschingen")
    first_hint = h.split()[0] if h.split() else ""
    first_csv = c.split()[0] if c.split() else ""
    return bool(first_hint) and first_hint == first_csv


def _as_dict(rec) -> dict:
    """Convert TypedDict to plain dict for serialization."""
    return {
        "plz": rec["plz"],
        "city": rec["city"],
        "country": rec["country"],
        "lat": rec["lat"],
        "lng": rec["lng"],
    }


def _detect_location(
    body: str,
    names: set[str],
    disambiguators: dict[str, re.Pattern],
) -> tuple[str, str | None, str]:
    """Return (status, name_or_detected, confidence).

    status ∈ {"whitelist_match", "ambiguous_match_failed",
              "nicht_in_whitelist", "no_location_detected"}
    """
    lower = body.lower()
    for name in names:
        name_lower = name.lower()
        idx = lower.find(name_lower)
        if idx < 0:
            continue
        window = body[max(0, idx - 60): idx + len(name) + 60]
        dis_re = disambiguators.get(name)
        if dis_re is None or dis_re.search(window):
            return "whitelist_match", name, "high"
        return "ambiguous_match_failed", name, "medium"
    m = _PREPOSITION_LOCATION_RE.search(body)
    if m:
        return "nicht_in_whitelist", m.group(1), "medium"
    return "no_location_detected", None, "low"


# ---------------------------------------------------------------------------
# Tier 3 — privat markers + anti-markers
# ---------------------------------------------------------------------------

# Generic, non-personal salutation patterns. Personal-addressing patterns
# (e.g. "Hallo <name>") are added at runtime from user_context.yaml's
# personal_addressing_names list via _get_salutation_re().
_BASE_SALUTATION_PATTERNS: tuple[str, ...] = (
    r"Sehr\s+geehrte[r]?\s+(?:Herr|Frau)",
    r"Guten\s+Tag\s+Herr",
)
_SALUTATION_RE_CACHE: re.Pattern[str] | None = None


def _get_salutation_re() -> re.Pattern[str]:
    """Build _SALUTATION_RE combining base patterns with user-context
    personal_addressing_names. Lazy + module-cached."""
    global _SALUTATION_RE_CACHE
    if _SALUTATION_RE_CACHE is not None:
        return _SALUTATION_RE_CACHE
    patterns: list[str] = list(_BASE_SALUTATION_PATTERNS)
    try:
        from domain_actionability import load_user_context  # noqa: PLC0415 lazy
        ctx = load_user_context()
        for name in ctx.get("personal_addressing_names") or []:
            if isinstance(name, str) and name.strip():
                escaped = re.escape(name.strip())
                patterns.append(rf"Hallo\s+{escaped}")
                patterns.append(rf"Lieber\s+{escaped}")
    except Exception:  # noqa: BLE001 — fallback: base patterns only
        pass
    _SALUTATION_RE_CACHE = re.compile(
        "|".join(f"(?:{p})" for p in patterns),
        re.IGNORECASE,
    )
    return _SALUTATION_RE_CACHE

_VIEWING_VOCAB = (
    "besichtigung",
    "besichtigungstermin",
    "vorbeischauen",
    "wohnungsbesichtigung",
    "besichtigen",
)

_PHONE_RE = re.compile(
    r"\+?41[\s.\-]?\d{2}[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}"
    r"|\+?49[\s.\-]?\d{2,4}[\s.\-]?\d{3,}"
    r"|\b0\d{2,4}[\s./\-]?\d{3,}\b"
)

_REAL_ESTATE_KEYWORDS = (
    "wohnung",
    "haus",
    "miete",
    "kaufpreis",
    "eigentumswohnung",
    "reihenhaus",
)

# R4: PERSONAL_DOMAINS — mainstream 16 + 4 architect-recommended adds.
PERSONAL_DOMAINS: set[str] = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.de",
    "ymail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "gmx.de",
    "gmx.ch",
    "gmx.net",
    "bluewin.ch",
    "hispeed.ch",
    "sunrise.ch",
    "tiscali.ch",
    "swissonline.ch",
    "web.de",
    "t-online.de",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    # R4 additions:
    "quickline.ch",
    "bluemail.ch",
    "freenet.de",
    "arcor.de",
}

_PERSONAL_LOCAL_RE = re.compile(r"^[a-z]+[._][a-z]+", re.IGNORECASE)

# R5: contextualised "unsubscribe" patterns to avoid false-positives on legit
# privat mails where 'unsubscribe' is discussed in body text.
_AUTOMAILER_PATTERNS = (
    re.compile(r"to\s+unsubscribe", re.IGNORECASE),
    re.compile(r"unsubscribe\s+link", re.IGNORECASE),
    re.compile(r"unsubscribe\s+from\s+this", re.IGNORECASE),
    re.compile(r"abmelden\s*:?\s*klick", re.IGNORECASE),
    re.compile(r"diese\s+e-?mail\s+wurde\s+generiert", re.IGNORECASE),
    re.compile(r"automatisch\s+versandt", re.IGNORECASE),
    re.compile(r"sie\s+erhalten\s+diese\s+nachricht", re.IGNORECASE),
    re.compile(r"bitte\s+antworten\s+sie\s+nicht\s+auf\s+diese\s+e-?mail", re.IGNORECASE),
    re.compile(r"this\s+email\s+was\s+automatically\s+generated", re.IGNORECASE),
    re.compile(r"do\s+not\s+reply", re.IGNORECASE),
)

# Inserat-Nr / Ref-Nr / Objekt-Nr / Bestell-Nr — anti-privat (portal signature).
_INSERAT_NR_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("anti_privat:inserat_nr",
     re.compile(r"Inserat[-_\s]?(?:Nr\.?|Nummer|Code|ID)\s*[:#]?\s*\d+", re.IGNORECASE)),
    ("anti_privat:ref_nr",
     re.compile(r"Ref\.?(?:erenz)?\s*[:#]?\s*[A-Z0-9-]{4,}", re.IGNORECASE)),
    ("anti_privat:objekt_nr",
     re.compile(r"Objekt[-_\s]?(?:Nr\.?|Nummer|Code|ID)\s*[:#]?\s*\d+", re.IGNORECASE)),
    ("anti_privat:bestell_nr",
     re.compile(r"Bestell[-_\s]?(?:Nr|Nummer)\s*[:#]?\s*\d+", re.IGNORECASE)),
)


def _has_personalised_address(sender_field: str) -> bool:
    addr = extract_email_address(sender_field)
    if "@" not in addr:
        return False
    local, _, domain = addr.partition("@")
    if domain not in PERSONAL_DOMAINS:
        return False
    return bool(_PERSONAL_LOCAL_RE.match(local))


def _tier3_score(
    sender_field: str,
    subject: str,
    body: str,
) -> tuple[int, int, list[str]]:
    """Return (privat_score, anti_score, matched_markers).

    +1 markers: salutation, viewing-vocab, phone-number, real-estate-keyword,
                personalised-from-address.
    −1 markers: automailer-disclaimer, inserat-nr / ref-nr / objekt-nr / bestell-nr.
    """
    haystack = f"{subject}\n{body}"
    haystack_lower = haystack.lower()
    privat = 0
    anti = 0
    markers: list[str] = []

    if _get_salutation_re().search(haystack):
        privat += 1
        markers.append("privat:salutation")

    if any(kw in haystack_lower for kw in _VIEWING_VOCAB):
        privat += 1
        markers.append("privat:viewing_vocab")

    if _PHONE_RE.search(haystack):
        privat += 1
        markers.append("privat:phone")

    if any(kw in haystack_lower for kw in _REAL_ESTATE_KEYWORDS):
        privat += 1
        markers.append("privat:realestate_keyword")

    if _has_personalised_address(sender_field):
        privat += 1
        markers.append("privat:personal_address")

    if any(p.search(haystack) for p in _AUTOMAILER_PATTERNS):
        anti += 1
        markers.append("anti_privat:automailer")

    for tag, regex in _INSERAT_NR_RES:
        if regex.search(haystack):
            anti += 1
            markers.append(tag)

    return privat, anti, markers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_IMMO_KEYWORD_RE = re.compile(
    r"(?i)\b(wohnung|haus|immobilie|eigentum|miete|kaufpreis|reihenhaus|"
    r"eigentumswohnung|grundst[uü]ck|mehrfamilienhaus|einfamilienhaus)\b"
)


def classify_immo(
    sender: str,
    subject: str,
    body: str,
    plugin_value: str,
    plugin_confidence: float,
) -> HeuristicResult:
    """Four-tier classification (Tier 0 paketzustellung → 1/2/3 Immo). Returns HeuristicResult."""

    # ----- Tier 0: Paketzustellung (deterministic, plugin-agnostic per D-U2) -----
    pz_matched, pz_reason, pz_markers = _matches_paketzustellung(sender, subject, body)
    if pz_matched:
        return HeuristicResult(
            suggested_action="move_paketzustellung",
            reason=pz_reason,
            confidence="high",
            matched_markers=pz_markers,
        )

    portal_domains = load_portal_domains()
    whitelist_names, whitelist_dis = load_whitelist()
    markers: list[str] = []

    # 2026-05-25 Block 3 — PLZ-Extraktion für Distance-Anzeige im DetailPanel.
    # Markers wandern bei allen classification-outcomes mit; folio liest die
    # Marker und berechnet/zeigt Distance zur home_plz client-side.
    plz_match = extract_plz(body)
    if plz_match:
        markers.append(f"plz:{plz_match['plz']}")
        markers.append(f"plz_city:{plz_match['city']}")
        markers.append(f"plz_country:{plz_match['country']}")
        markers.append(f"plz_coords:{plz_match['lat']:.4f},{plz_match['lng']:.4f}")

    # ----- Tier 1: portal sender match -----
    sender_email = extract_email_address(sender)
    sender_domain = extract_domain(sender_email).lower() if sender_email else ""
    is_portal = any(
        sender_domain == p or sender_domain.endswith("." + p)
        for p in portal_domains
    )
    if is_portal:
        markers.append(f"tier1:portal_domain:{sender_domain}")
        # Bauteil-8 A2 (2026-06-09): Portal-Sender braucht Inserat-URL im
        # Body. Wenn kein /expose/, /Expose/, /kaufen/<id> etc. Pattern
        # matched → Marker 'no_inserat_url'. Domain-Drift zu 'werbung'
        # macht domain_actionability._apply_no_inserat_url_drift.
        # Wenn fuer dieses Portal keine Pattern in regelwerk → kein Check
        # (silent skip, kein no_inserat_url-Marker — verhindert
        # False-Negative wenn neues Portal noch unkonfiguriert ist).
        url_patterns = _get_inserat_url_patterns(sender_domain)
        if url_patterns:
            has_inserat_url = any(p.search(body or "") for p in url_patterns)
            if not has_inserat_url:
                markers.append("no_inserat_url")

    haystack = f"{subject}\n{body}"
    has_immo_kw = bool(_IMMO_KEYWORD_RE.search(haystack))

    # 2026-06-05: tier1-Blocker-Marker (projektiert/Zwangsversteigerung/PoA).
    # Reine Marker-Schreibung — actionability-Override macht
    # domain_actionability._apply_tier1_blocker_filter.
    # Patterns aus regelwerk.filters.hauskauf.block_patterns (zentralisiert
    # 2026-06-05). Bei fehlendem Marker im regelwerk: kein Block (no-op).
    # Bauteil-7 G1 (2026-06-09): Validation pro Match — Footer-Boilerplate,
    # Auto-Reply-Subject-Prefix oder Newsletter-Sender disqualifizieren den
    # Marker (Regeln aus block_patterns_validation). Footer-Start einmal
    # pro Mail berechnet.
    _footer_start = _compute_footer_start(haystack)
    for marker_name in ("projektiert", "zwangsversteigerung", "price_on_request"):
        # regelwerk-key: 'preis_auf_anfrage' (deutsch); intern als
        # tier1:price_on_request:true (Backwards-Compat mit TIER1_BLOCKER_MARKERS).
        regelwerk_key = "preis_auf_anfrage" if marker_name == "price_on_request" else marker_name
        pat = _get_block_pattern_re(regelwerk_key)
        if not pat:
            continue
        m = pat.search(haystack)
        if not m:
            continue
        if _is_marker_disqualified(
            regelwerk_key, subject, sender_email, m.start(), _footer_start
        ):
            markers.append(f"disqualified:{marker_name}:context")
            continue
        markers.append(f"tier1:{marker_name}:true")

    # ----- Tier 2 trigger -----
    tier2_active = is_portal or has_immo_kw

    if tier2_active:
        # 2.0) Portal-Sender + 'Abweichende(r) Lage|Preis' marker → archive silent.
        # immowelt et al. push 'Alternative offers' with bodies starting in
        # "Abweichende Lage: ..." or "Abweichender Preis: ..." followed by
        # listings outside the user's scope. These are noise regardless of which
        # location/price values appear later, so route directly to portal-archive
        # without falling through to whitelist/non-whitelist branches that
        # could mis-classify as zu_pruefen.
        # (User-Feedback Tranche 1+2, 2026-05-22)
        if is_portal:
            alt_match = re.search(
                r"\babweichende[rs]?\s+(lage|preis)\b", body, re.IGNORECASE
            )
            if alt_match:
                variant = alt_match.group(1).lower()
                markers.append(f"tier2:portal_alternative_offer:abweichende_{variant}")
                return HeuristicResult(
                    suggested_action="move_immo_portal",
                    reason=f"Portal-Sender + Alternative-Angebot ('Abweichende{'r' if variant == 'preis' else ''} {variant.capitalize()}')",
                    confidence="high",
                    matched_markers=markers,
                )

        # 2a) Price out-of-scope check
        price_hits = _find_prices(body)
        primary = _select_primary_price(body, price_hits)
        if primary:
            _threshold = _get_price_threshold()
            if primary.amount > _threshold:
                markers.append(f"tier2:price_over_threshold:{primary.amount}_{primary.currency}")
                return HeuristicResult(
                    suggested_action="move_zu_pruefen",
                    reason=f"Preis {primary.currency} {primary.amount:,} > {_threshold:,}",
                    confidence="high",
                    matched_markers=markers,
                )

        # 2b) Location check
        loc_status, loc_value, loc_conf = _detect_location(
            body, whitelist_names, whitelist_dis
        )
        if loc_status == "whitelist_match":
            markers.append(f"tier2:location_whitelist:{loc_value}")
            if is_portal:
                return HeuristicResult(
                    suggested_action="move_immo_portal",
                    reason=f"Portal-Sender + Ort in Whitelist ({loc_value})",
                    confidence="high",
                    matched_markers=markers,
                )
            # Not portal but Immo-keyword and known location → still scope hit
            # Fall through to Tier 3 to decide privat vs zu_pruefen
        elif loc_status == "ambiguous_match_failed":
            markers.append(f"tier2:ambiguous_match_failed:{loc_value}")
            return HeuristicResult(
                suggested_action="move_zu_pruefen",
                reason=f"Ortsname '{loc_value}' im Body, Disambiguator nicht gefunden",
                confidence="medium",
                matched_markers=markers,
            )
        elif loc_status == "nicht_in_whitelist":
            markers.append(f"tier2:location_outside:{loc_value}")
            return HeuristicResult(
                suggested_action="move_zu_pruefen",
                reason=f"Ort nicht in Whitelist: {loc_value}",
                confidence="medium",
                matched_markers=markers,
            )
        else:
            # no_location_detected
            if not primary:
                markers.append("tier2:no_price_no_location")
                if is_portal:
                    return HeuristicResult(
                        suggested_action="move_zu_pruefen",
                        reason="Portal-Sender aber keine Preis-/Ortsangabe erkannt",
                        confidence="low",
                        matched_markers=markers,
                    )

    # ----- Tier 3: privat-Immo detection -----
    # Only run if body suggests Immo content (keyword check) AND not already
    # decided as portal-match-with-whitelist (high-conf return above).
    privat = anti = 0
    privat_markers: list[str] = []
    if has_immo_kw:
        privat, anti, privat_markers = _tier3_score(sender, subject, body)
        markers.extend(privat_markers)

    net = privat - anti

    if net >= 2:
        return HeuristicResult(
            suggested_action="move_immo_privat",
            reason=f"Privat-Marker netto={net} (privat={privat}, anti={anti})",
            confidence="high",
            matched_markers=markers,
        )

    if net == 1:
        # 1 net marker. Plugin-alignment changes the verdict.
        if plugin_value == "geschaeftspost" and plugin_confidence > 0.7:
            return HeuristicResult(
                suggested_action="move_immo_privat",
                reason="1 net marker; plugin agrees geschaeftspost — LLM-backup deferred",
                confidence="low",
                matched_markers=markers,
            )
        if plugin_value in ("privat", "geschaeftspost"):
            return HeuristicResult(
                suggested_action="move_zu_pruefen",
                reason=f"1 net marker but plugin={plugin_value} disagrees",
                confidence="low",
                matched_markers=markers,
            )
        return HeuristicResult(
            suggested_action="move_zu_pruefen",
            reason="1 net marker, ambiguous",
            confidence="low",
            matched_markers=markers,
        )

    # R6: net == 0 AND (privat >= 1 AND anti >= 1) → kontradiktorisch → zu_pruefen
    if net == 0 and privat >= 1 and anti >= 1:
        return HeuristicResult(
            suggested_action="move_zu_pruefen",
            reason="kontradiktorische Marker (privat- und anti-privat-Marker)",
            confidence="low",
            matched_markers=markers,
        )

    # If we made it here AND we earlier matched portal+whitelist, return that.
    portal_whitelist = any(m.startswith("tier2:location_whitelist") for m in markers) and is_portal
    if portal_whitelist:
        # Already returned earlier — defensive double-check.
        return HeuristicResult(
            suggested_action="move_immo_portal",
            reason="Portal-Sender + Whitelist-Ort",
            confidence="high",
            matched_markers=markers,
        )

    # Default fall-through
    return HeuristicResult(
        suggested_action="keep",
        reason="keine Immo-Indikatoren",
        confidence="high",
        matched_markers=markers,
    )


# ---------------------------------------------------------------------------
# CLI for ad-hoc inspection
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="Run classify_immo on stdin or args.")
    ap.add_argument("--sender", required=True)
    ap.add_argument("--subject", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--plugin-value", default="unklar")
    ap.add_argument("--plugin-confidence", type=float, default=0.5)
    args = ap.parse_args()
    result = classify_immo(
        sender=args.sender,
        subject=args.subject,
        body=args.body,
        plugin_value=args.plugin_value,
        plugin_confidence=args.plugin_confidence,
    )
    print(_json.dumps(
        {
            "suggested_action": result.suggested_action,
            "reason": result.reason,
            "confidence": result.confidence,
            "matched_markers": result.matched_markers,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
