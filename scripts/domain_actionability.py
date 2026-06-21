#!/usr/bin/env python3
"""domain_actionability.py — F.8 Worker Classification (2-Axes: Domain × Actionability).

Komplementär zu immo_heuristic.py (Tier-0–3 Immo-Spezialfälle), nicht ablösend:
Option-C-Hybrid — Plugin-Class als Signal-Feature; immo_heuristic liefert weiter
Portal-/Paketzustellungs-Marker parallel.

Replaces immo_heuristic.classify_immo per Option C Hybrid (Plugin stays invariant,
Plugin-Class fed as signal-feature).

6-Step Pipeline:
  1. Sender-Priority-Override (highest precedence; user_context.yaml sender_priorities)
  2. Domain-Detection (sender-pattern + subject-keywords)
  3. Plugin-Class-Refinement (werbung/geschaeftspost/privat/spam/unklar as signal)
  4. Initial-Actionability (per-domain default)
  5. Time-Decay-Apply (mail_date + user_context.time_decay → downgrade)
  6. Active-Priorities-Boost (hauskauf/jobsuche → upgrade archive→actionable)

Public API:
    classify_domain_actionability(sender, subject, mail_date,
                                  plugin_class, user_context) -> ClassificationResult
"""
from __future__ import annotations

import fnmatch
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sender_heuristic import extract_domain, extract_email_address  # noqa: E402

log = logging.getLogger("domain_actionability")

Domain = Literal["immo", "job", "shopping", "finance", "kontakt", "werbung", "system", "unsorted"]
# Bauteil-7 G5 (2026-06-09): neuer Wert 'auto_reply' fuer Makler-Auto-
# Replies (Widerrufsbelehrungen, Maklerauftrag-Bestätigungen, Termin-
# Eingangs-Bestätigungen). Domain bleibt typischerweise immo oder
# kontakt, aber Mail soll keine User-Aktion triggern und Council-Ingest
# skipt sie. Folio types.ts ist mit-gefixt + 'uebernommen'-Drift aus
# Bauteil 2.7 ebenfalls.
Actionability = Literal["actionable", "archive", "archive-silent", "uebernommen", "auto_reply"]


# Bauteil-7 G5 (2026-06-09) Auto-Reply-Detection. Dupliziert aus
# council/src/immoscout_body_parser.py:30-52 (kein Cross-Repo-Import).
# Pattern erweitert um Bauteil-7-Befund: 'maklerauftrag',
# 'ihr interesse am objekt'.
_AUTO_REPLY_SUBJECT_PATTERNS = (
    "bestätigung",
    "ihre anfrage",
    "widerrufsbelehrung",
    "kontaktaufnahme",
    "anfrage weitergeleitet",
    "maklerauftrag",
    "ihr interesse am objekt",
)
_AUTO_REPLY_BODY_PATTERNS = (
    "ihre kontaktaufnahme zum anbieter",
    "sie haben kontakt mit dem anbieter",
    "auftraggeber/-in (interessent",
    "widerrufsbelehrung sorgfältig durch",
)


def _detect_auto_reply(subject: str | None, body: str | None) -> bool:
    """True wenn Subject- oder Body-Header-Pattern auf Auto-Reply matched.
    Pragmatische Heuristik V1: Subject-Substring ODER Body-Head-Substring
    (erste 500 Zeichen). Wenn V1 zu viele False-Positives produziert →
    Folge-Direktive 7b mit LLM-Klassifikation."""
    subj_lower = (subject or "").lower()
    if any(p in subj_lower for p in _AUTO_REPLY_SUBJECT_PATTERNS):
        return True
    body_head = (body or "")[:500].lower()
    if any(p in body_head for p in _AUTO_REPLY_BODY_PATTERNS):
        return True
    return False


@dataclass
class ClassificationResult:
    """F.8 output. Replaces HeuristicResult."""
    domain: Domain
    actionability: Actionability
    reason: str
    confidence: Literal["high", "medium", "low"]
    matched_markers: list[str] = field(default_factory=list)
    plugin_class_hint: str | None = None


# --- Default-Context (fallback wenn user_context.yaml fehlt) ---
DEFAULT_CONTEXT: dict = {
    "active_priorities": [],
    "life_status": {},
    # Personal addressing names — used by immo_heuristic._get_salutation_re()
    # to detect "Hallo <name>" / "Lieber <name>" salutations as a +1 privat-
    # marker. Empty list = only generic "Sehr geehrter Herr/Frau" patterns.
    "personal_addressing_names": [],
    "time_decay": {
        "immo": {"actionable_within_days": 90, "archive_within_days": 180},
        "job": {"actionable_within_days": 90, "archive_within_days": 180},
        "kontakt": {"actionable_within_days": 30, "archive_within_days": 365},
        "shopping": {"actionable_within_days": 14, "archive_within_days": 60},
        "finance": {"actionable_within_days": 60, "archive_within_days": 2555},
        "werbung": {"actionable_within_days": 3, "archive_within_days": 7},
        "system": {"actionable_within_days": 7, "archive_within_days": 30},
        "unsorted": {"actionable_within_days": 30, "archive_within_days": 180},
    },
    "sender_priorities": {"always_actionable": [], "always_archive_silent": []},
}

# --- Domain-Detection-Patterns (hardcoded baseline; extensible via user_context) ---
IMMO_DOMAINS = (
    "immowelt.de", "immoscout24.ch", "immobilienscout24.de",
    "homegate.ch", "newhome.ch", "comparis.ch",
    "engelvoelkers.com", "century21.de", "century21.com",
)
JOB_DOMAINS = (
    "linkedin.com", "indeed.com", "indeed.de",
    "xing.com", "stepstone.de",
)
# Subject-Keywords werden strict via \b...\b matched (Direktive 2026-05-27 Job-Substring-Fix).
# Plural/wichtige Komposita müssen explicit gelistet werden — `\w*`-Suffix-Trap bewusst
# vermieden (würde z.B. „stellen" + „Stellungnahme" beide matchen).
IMMO_SUBJECT_KEYWORDS = (
    "immobilie", "immobilien",
    "wohnung kaufen", "haus kaufen",
)
JOB_SUBJECT_KEYWORDS = (
    "bewerbung", "bewerbungen",
    "karriere", "karrieren",
    "vacancy", "vacancies",
    "job alert", "job alerts",
    "stelle", "stellen",
    "stellenangebot", "stellenangebote",
    "stellenanzeige", "stellenanzeigen",
    "stellenausschreibung",
)
SHOPPING_DOMAINS = (
    "amazon.de", "amazon.com", "amazon.co.uk",
    "easycosmetic.de", "zalando.de", "zalando.com",
    "dhl.de", "dpd.com", "ups.com",
    "migros.ch", "digitec.ch",
)
PAKETZUSTELLUNG_KEYWORDS = (
    "paket", "pakete",
    "zustellung", "zustellungen",
    "lieferung", "lieferungen", "lieferbestätigung",
    "geliefert",
    "versand", "versandbestätigung", "versandbenachrichtigung",
    "tracking",
    "sendung", "sendungen", "sendungsverfolgung",
)
FINANCE_DOMAINS = (
    "paypal.com", "paypal.de",
    "sparkasse.de", "postfinance.ch", "ubs.com", "credit-suisse.com",
    "vodafone.de",  # rechnungen
    "bafu.admin.ch", "steueramt.ch",
)
FINANCE_SUBJECT_KEYWORDS = (
    "rechnung", "rechnungen",
    "quittung", "quittungen",
    "steuer", "steuern",
    "steuererklärung", "steuerberatung",
    "versicherung", "versicherungen",
    "abonnement", "abonnements",
    "invoice", "invoices",
)
SYSTEM_DOMAINS = (
    "anthropic.com",
    "infoemail.microsoft.com",
    "github.com",  # security alerts
)
# Brand-Tokens: intentional substring-match auf domain. Review-Followup C
# 2026-05-27: separate Liste statt `or d in domain` Mischung in SYSTEM_DOMAINS
# (drittes Auftreten der Substring-Klasse — Brand-Match ist hier *gewollt*,
# wird darum explizit ausgewiesen).
SYSTEM_DOMAIN_TOKENS = (
    "microsoftrewards",  # MicrosoftRewards@infoemail.microsoft.com etc.
)
SYSTEM_SUBJECT_KEYWORDS = ("security alert", "verification", "two-factor", "password reset")

# F.8.5 — Werbung-Domain (Marketing, Newsletter, Promo, Aktionen).
# Bekannte Werbung-Versender (Newsletter-Service-Provider + grosse Brands mit
# eigenen Marketing-Subdomains). Sender-prefix-Patterns wie newsletter@/deals@
# zusätzlich via BULK_SENDER_PREFIXES + sender_priorities aus user_context.yaml.
WERBUNG_DOMAINS = (
    "substack.com",
    "e.heise.de",                # Heise-Newsletter
    "newsletter.zalando.de",
    "email.amazon.de", "email.amazon.com",
    "mail.beehiiv.com",
    "mailchimpapp.com",
    "list-manage.com",          # Mailchimp-Hosts
    "sendinblue.com", "brevo.com",
)
WERBUNG_SUBJECT_KEYWORDS = (
    "newsletter", "newsletters",
    "sale", "sales",
    "rabatt", "rabatte",
    "aktion", "aktionen",
    "angebot", "angebote",
    "promo", "promos",
    "% off", "% rabatt",
    "exklusiv",
)
WERBUNG_SENDER_PREFIXES = (
    "newsletter", "marketing", "deals", "promo", "promotions",
)

# Bulk-sender prefixes that mean "company/system" (not private kontakt).
# F.8.5: werbung-prefixes weg, sie matchen jetzt eigen via WERBUNG_SENDER_PREFIXES.
BULK_SENDER_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications", "info", "support", "service", "hello",
    "team", "news", "updates",
)


def _norm_sender(sender: str) -> tuple[str, str]:
    """Extract (email, domain) from a 'Name <email>'-style sender string."""
    email = extract_email_address(sender) or sender.lower()
    domain = extract_domain(email)
    return email.lower(), domain.lower() if domain else ""


def _glob_match(pattern: str, value: str) -> bool:
    return fnmatch.fnmatchcase(value.lower(), pattern.lower())


def _sender_priority_match(
    email: str, rules: list[dict]
) -> tuple[Domain, str] | None:
    """Return (domain, matched-pattern) if any rule matches, else None."""
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        pattern = rule.get("sender")
        domain = rule.get("domain")
        if not pattern or not domain:
            continue
        if _glob_match(pattern, email):
            return (domain, pattern)
    return None


def _subject_matches_any(subject_lower: str, keywords: tuple[str, ...]) -> str | None:
    """Strict word-boundary match auf subject_lower. Matched nur exakte Tokens
    (\\b davor und \\b danach). Direktive 2026-05-27 Job-Substring-Fix:
    ersetzt den substring-match `any(k in subj_lower for k in ...)` der
    `\"stelle\"` in `\"zuzustellen\"` matched hat.

    Multi-word-Keywords wie `\"job alert\"` funktionieren weil das Space dazwischen
    selbst auch eine Wortgrenze ist. Plural/wichtige Komposita müssen explicit in
    der Keyword-Liste stehen — bewusst KEIN `\\w*`-Suffix (sonst würde z.B.
    `\\bsteuer\\w*\\b` `\"Steuerung\"` fälschlich matchen).
    """
    for kw in keywords:
        if re.search(rf"\b{re.escape(kw)}\b", subject_lower):
            return kw
    return None


def _prefix_matches_any(prefix: str, tokens: tuple[str, ...]) -> str | None:
    """Strict sender-prefix-match. Direktive 2026-05-27 Sender-Prefix-Fix:
    ersetzt `prefix.startswith(p) OR p in prefix` der `\"info\"` in
    `\"linkedin-info\"` matched hat (3. Auftreten der Substring-ohne-
    Wortgrenze-Bug-Klasse).

    Match-Mechanik 3-stufig:
      1. Exact-Match: prefix == token (z.B. `noreply@`)
      2. Strict-Startswith mit Trennzeichen: prefix.startswith(token +
         \"-\"|\".\"|\"_\") (z.B. `noreply-system@`)
      3. Segment-Match nur für Tokens >= 5 Zeichen: re.split([-._]),
         token in segments (z.B. `acme-newsletter@` matched `newsletter`)

    Begründung 5-Zeichen-Schwelle: kurze Tokens (info/team/news, 4 chars)
    als Segments zu breit → `linkedin-info@` würde sonst fälschlich gegen
    `info` matchen. Lange Tokens (newsletter/marketing/notifications) sind
    spezifisch genug für Segment-Match.
    """
    if not prefix:
        return None
    for tok in tokens:
        if prefix == tok:
            return tok
        if (prefix.startswith(tok + "-")
            or prefix.startswith(tok + ".")
            or prefix.startswith(tok + "_")):
            return tok
        if len(tok) >= 5:
            segments = re.split(r"[-._]", prefix)
            if tok in segments:
                return tok
    return None


def _detect_domain(email: str, domain: str, subject: str) -> tuple[Domain, list[str]]:
    """Sender + subject heuristic. Returns (domain, matched_markers)."""
    markers: list[str] = []
    subj_lower = subject.lower()

    # Immo
    for d in IMMO_DOMAINS:
        if domain == d or domain.endswith("." + d):
            markers.append(f"immo:domain:{d}")
            return ("immo", markers)
    if _subject_matches_any(subj_lower, IMMO_SUBJECT_KEYWORDS):
        markers.append("immo:subject")
        return ("immo", markers)

    # Job
    for d in JOB_DOMAINS:
        if domain == d or domain.endswith("." + d):
            markers.append(f"job:domain:{d}")
            return ("job", markers)
    if _subject_matches_any(subj_lower, JOB_SUBJECT_KEYWORDS):
        markers.append("job:subject")
        return ("job", markers)

    # Shopping (paketzustellung first, dann generic-shopping)
    if _subject_matches_any(subj_lower, PAKETZUSTELLUNG_KEYWORDS):
        markers.append("shopping:paketzustellung-subject")
        return ("shopping", markers)
    for d in SHOPPING_DOMAINS:
        if domain == d or domain.endswith("." + d):
            markers.append(f"shopping:domain:{d}")
            return ("shopping", markers)

    # Finance
    for d in FINANCE_DOMAINS:
        if domain == d or domain.endswith("." + d):
            markers.append(f"finance:domain:{d}")
            return ("finance", markers)
    if _subject_matches_any(subj_lower, FINANCE_SUBJECT_KEYWORDS):
        markers.append("finance:subject")
        return ("finance", markers)

    # System — strict TLD-Match (equality + .endswith) + separate
    # Brand-Token-Liste (intentional substring-match, explizit ausgewiesen).
    for d in SYSTEM_DOMAINS:
        if domain == d or domain.endswith("." + d):
            markers.append(f"system:domain:{d}")
            return ("system", markers)
    for t in SYSTEM_DOMAIN_TOKENS:
        if t in domain:
            markers.append(f"system:domain-token:{t}")
            return ("system", markers)
    if _subject_matches_any(subj_lower, SYSTEM_SUBJECT_KEYWORDS):
        markers.append("system:subject")
        return ("system", markers)

    # F.8.5 — Werbung (Marketing, Newsletter). Geprüft vor kontakt damit
    # noreply@brand-Marketing nicht als bulk-system-Sender fehlklassifiziert wird.
    for d in WERBUNG_DOMAINS:
        if domain == d or domain.endswith("." + d):
            markers.append(f"werbung:domain:{d}")
            return ("werbung", markers)
    prefix = email.split("@", 1)[0] if "@" in email else email
    if _prefix_matches_any(prefix, WERBUNG_SENDER_PREFIXES) is not None:
        markers.append(f"werbung:prefix:{prefix[:20]}")
        return ("werbung", markers)
    if _subject_matches_any(subj_lower, WERBUNG_SUBJECT_KEYWORDS):
        markers.append("werbung:subject")
        return ("werbung", markers)

    # Kontakt: non-bulk-sender (private person). F.8.5: renamed from correspondence.
    is_bulk = _prefix_matches_any(prefix, BULK_SENDER_PREFIXES) is not None
    if not is_bulk:
        markers.append("kontakt:non-bulk-prefix")
        return ("kontakt", markers)

    # Default fallback
    markers.append("unsorted:fallback")
    return ("unsorted", markers)


def _refine_with_plugin_class(
    domain: Domain,
    actionability: Actionability,
    plugin_class: str | None,
    markers: list[str],
) -> tuple[Domain, Actionability]:
    """Step 3 — Plugin-Class als Signal-Feature."""
    if not plugin_class:
        return (domain, actionability)
    pc = plugin_class.lower()
    if pc == "spam":
        markers.append("plugin:spam→unsorted+silent")
        return ("unsorted", "archive-silent")
    if pc == "unklar":
        markers.append("plugin:unklar→force-review")
        return (domain, "actionable")
    if pc == "privat" and domain == "unsorted":
        markers.append("plugin:privat→kontakt")
        return ("kontakt", actionability)
    if pc == "werbung":
        # F.8.5: Plugin-werbung mappt jetzt explizit auf werbung-Domain (statt
        # nur silent zu setzen). Force archive-silent als default.
        markers.append("plugin:werbung→werbung-domain+silent")
        return ("werbung", "archive-silent")
    # geschaeftspost: kein structural change, evtl. confidence boost
    return (domain, actionability)


def _initial_actionability(domain: Domain, markers: list[str]) -> Actionability:
    """Step 4 — per-domain default actionability."""
    if any(m.startswith("shopping:paketzustellung") for m in markers):
        return "actionable"  # paketzustellung wird IMMER actionable
    if domain in ("immo", "job", "finance", "kontakt"):
        return "actionable"
    if domain == "werbung":
        return "archive-silent"  # F.8.5: Werbung defaultet silent
    if domain in ("shopping", "system"):
        return "archive"
    return "actionable"  # unsorted force-review


def _parse_mail_date(raw: str | None) -> datetime | None:
    """Parse mail_date (ISO oder RFC2822) → aware datetime."""
    if not raw:
        return None
    try:
        # ISO-Format first
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        pass
    try:
        # RFC2822 fallback
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _apply_time_decay(
    domain: Domain,
    actionability: Actionability,
    mail_date: str | None,
    time_decay_config: dict,
) -> tuple[Actionability, list[str]]:
    """Step 5 — Time-Decay-Downgrade.

    Bauteil-7 G4 (2026-06-09): Freshness-Preempt VOR der existing
    decay-Logik. filters.hauskauf.freshness_max_days (uniform fuer
    alle Domains in V1) ueberschreibt die strengere Schwelle aus
    DEFAULT_CONTEXT.time_decay (immo/job:90d). Marker
    expired:freshness:<days> + actionability='archive-silent'.
    Spam-Auto-Uebernahme greift dadurch gar nicht erst zu.
    """
    markers: list[str] = []
    dt = _parse_mail_date(mail_date)
    if dt is None:
        return (actionability, markers)
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    # Bauteil-7 G4: Freshness-Preempt aus regelwerk (uniform-default).
    # Wenn fehlt → kein Preempt, decay-Logik wirkt wie bisher.
    try:
        fresh_max = get_filter_config("hauskauf").get("freshness_max_days")
    except Exception:  # noqa: BLE001
        fresh_max = None
    if fresh_max and age_days > fresh_max:
        markers.append(f"expired:freshness:{int(age_days)}d")
        return ("archive-silent", markers)
    cfg = time_decay_config.get(domain, {})
    archive_within = cfg.get("archive_within_days", 365)
    actionable_within = cfg.get("actionable_within_days", 30)
    if age_days > archive_within:
        markers.append(f"decay:>{archive_within}d→silent")
        return ("archive-silent", markers)
    if age_days > actionable_within:
        markers.append(f"decay:>{actionable_within}d→archive")
        return ("archive", markers)
    return (actionability, markers)


def _apply_priority_boost(
    domain: Domain,
    actionability: Actionability,
    active_priorities: list[str],
    markers: list[str],
) -> Actionability:
    """Step 6 — Aktive-Lebenssituation overridet time-decay-Archive."""
    domain_priority_map = {
        "immo": "hauskauf",
        "job": "jobsuche",
    }
    priority_key = domain_priority_map.get(domain)
    if priority_key and priority_key in (active_priorities or []):
        if actionability == "archive":
            markers.append(f"boost:{priority_key}→actionable")
            return "actionable"
        # archive-silent bleibt — User hat das explizit gewollt
    return actionability


# 2026-05-28 Aufräum-Iteration Wunsch 3: PLZ-Country-Filter.
# Bei aktiver hauskauf-Priority + domain=immo + Mail-PLZ-Country in BLOCKED-Liste
# (z.B. FR/IT/AT bei CH-Suche) → archive-silent + Marker out_of_country:<code>.
# DE bleibt aktiv weil DE-Grenzgebiet via Distanz-Filter geregelt wird, nicht Country.
HAUSKAUF_BLOCKED_COUNTRIES = ("FR", "IT", "AT")


# 2026-06-05: tier1-Blocker fuer projektierte Objekte + Zwangsversteigerungen.
# Bei Marker-Treffer: override actionability → archive-silent unabhaengig von
# Country, hauskauf-Priority oder anderen Steps. Marker werden bereits in
# immo_heuristic.py geschrieben (regex auf body/subject).
TIER1_BLOCKER_MARKERS = (
    "tier1:projektiert:true",
    "tier1:zwangsversteigerung:true",
    "tier1:price_on_request:true",
)


def _apply_tier1_blocker_filter(
    domain: Domain,
    actionability: Actionability,
    heuristic_markers: list[str] | None,
    markers: list[str],
) -> Actionability:
    """Step 8 — tier1-Blocker. Wenn projektiert/Zwangsversteigerung in
    heuristic_markers steht: override → archive-silent. Reduziert Mail-
    Verschmutzung durch Anbieter-Sprache + Zwangsversteigerungslisten.

    Bauteil-7 G1 (2026-06-09): Filter wirkt jetzt domain-agnostisch.
    Vorher: `if domain != "immo": return actionability` — hat Marker in
    kontakt/werbung/unsorted-Mails (Mail 756 prototypisch) durchrutschen
    lassen. Marker-Validation (immo_heuristic.py) sorgt jetzt dafuer,
    dass tier1:* nur bei substanziellem Match gesetzt wird; wenn er
    gesetzt ist, gilt er ueber alle Domains."""
    if not heuristic_markers:
        return actionability
    for blocker in TIER1_BLOCKER_MARKERS:
        if blocker in heuristic_markers:
            markers.append(blocker.replace("tier1:", "blocked_by:"))
            return "archive-silent"
    return actionability


# 2026-06-05 Korridor-Filter (Step 9, nach Afshin-Spec).
# Lazy-cached regelwerk damit nicht jeder classify-Call die YAML neu parsed.
_REGELWERK_CACHE: dict | None = None


def _get_cached_regelwerk() -> dict:
    global _REGELWERK_CACHE
    if _REGELWERK_CACHE is None:
        _REGELWERK_CACHE = load_regelwerk()
    return _REGELWERK_CACHE


# 2026-06-05 Filter-Zentralisierung: domain-spezifische Filter-Defs
# (block_patterns, korridor_whitelist, thresholds) leben unter
# regelwerk.filters.{domain}. Konsumenten rufen get_filter_config(domain).
def get_filter_config(domain: str) -> dict:
    """Liefert filters.{domain}-Subdict aus cached regelwerk.
    Fallback: leeres dict (Filter no-op)."""
    return ((_get_cached_regelwerk().get("filters") or {}).get(domain) or {})


def _extract_marker_value(markers: list[str], prefix: str) -> str | None:
    """Holt 'plz:8000' → '8000'. Liefert None bei nicht-gefunden."""
    for m in markers:
        if m.startswith(prefix):
            return m[len(prefix):]
    return None


def _apply_corridor_filter(
    domain: Domain,
    actionability: Actionability,
    active_priorities: list[str],
    heuristic_markers: list[str] | None,
    corridor: dict,
    excludes: list[int],
    markers: list[str],
) -> Actionability:
    """Step 9 — PLZ-Korridor-Filter. Override final_action wenn PLZ nicht
    im konfigurierten geografischen Korridor (siehe regelwerk.korridor_*).
    Excludes ueberschreiben Membership."""
    if domain != "immo":
        return actionability
    if "hauskauf" not in (active_priorities or []):
        return actionability
    if not heuristic_markers:
        return actionability
    plz_str = _extract_marker_value(heuristic_markers, "plz:")
    country = _extract_marker_value(heuristic_markers, "plz_country:")
    if plz_str is None or country is None:
        return actionability  # fallback_unknown_plz greift separat
    try:
        plz_int = int(plz_str)
    except ValueError:
        return actionability

    # Excludes haben Vorrang
    if plz_int in (excludes or []):
        markers.append(f"out_of_corridor:{plz_int}")
        return "archive-silent"

    if country == "DE":
        de_list = (corridor.get("de_side") or [])
        if plz_int in de_list:
            return actionability
        markers.append(f"out_of_corridor:{plz_int}")
        return "archive-silent"

    if country == "CH":
        ch = corridor.get("ch_side") or {}
        ranges = ch.get("ranges") or []
        extras = ch.get("extras") or []
        for entry in ranges:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                low, high = entry
                if low <= plz_int <= high:
                    return actionability
        if plz_int in extras:
            return actionability
        markers.append(f"out_of_corridor:{plz_int}")
        return "archive-silent"

    # FR/IT/AT etc. werden von Step 7 (plz_country_filter) behandelt.
    return actionability


def _apply_plz_country_filter(
    domain: Domain,
    actionability: Actionability,
    active_priorities: list[str],
    heuristic_markers: list[str] | None,
    blocked_countries: tuple[str, ...],
    markers: list[str],
) -> Actionability:
    """Step 7 — PLZ-Country-Filter. Override final_action für hauskauf-Suche
    auf Mails aus BLOCKED-Ländern. Markers werden mit out_of_country:<code>
    ergänzt (für UI-Anzeige + Audit). Konfigurierbar via user_context.yaml
    hauskauf_blocked_countries."""
    if domain != "immo":
        return actionability
    if "hauskauf" not in (active_priorities or []):
        return actionability
    if not heuristic_markers:
        return actionability
    country = next(
        (m[len("plz_country:"):] for m in heuristic_markers if m.startswith("plz_country:")),
        None,
    )
    if country is None:
        return actionability
    if country in blocked_countries:
        markers.append(f"out_of_country:{country}")
        return "archive-silent"
    return actionability


def classify_domain_actionability(
    sender: str,
    subject: str,
    mail_date: str | None,
    plugin_class: str | None = None,
    user_context: dict | None = None,
    heuristic_markers: list[str] | None = None,
    body: str | None = None,
) -> ClassificationResult:
    """F.8 7-Step Pipeline. Replaces classify_immo.

    Review-followup B.7 2026-05-27: `body` parameter removed (unused in this
    pipeline — body-based heuristics would need a separate explicit hook).

    2026-05-28 Aufräum-Iteration Wunsch 3: optional `heuristic_markers` für
    Step 7 (PLZ-Country-Filter). Default None → bestehende Tests laufen weiter.

    Bauteil-7 G5 (2026-06-09): optional `body` für Auto-Reply-Detection
    (Step 2.5). Default None → existing callers ohne Auto-Reply-Check.
    """
    ctx = user_context or DEFAULT_CONTEXT
    email, domain_part = _norm_sender(sender)
    matched_markers: list[str] = []

    # Step 1: Sender-Priority-Override
    sp = ctx.get("sender_priorities", {}) or {}
    override = _sender_priority_match(email, sp.get("always_archive_silent", []))
    if override:
        domain_chosen, pattern = override
        matched_markers.append(f"override:silent:{pattern}")
        return ClassificationResult(
            domain=domain_chosen,
            actionability="archive-silent",
            reason=f"sender_priority always_archive_silent: {pattern}",
            confidence="high",
            matched_markers=matched_markers,
            plugin_class_hint=plugin_class,
        )
    override = _sender_priority_match(email, sp.get("always_actionable", []))
    if override:
        domain_chosen, pattern = override
        matched_markers.append(f"override:actionable:{pattern}")
        return ClassificationResult(
            domain=domain_chosen,
            actionability="actionable",
            reason=f"sender_priority always_actionable: {pattern}",
            confidence="high",
            matched_markers=matched_markers,
            plugin_class_hint=plugin_class,
        )

    # Step 2: Domain-Detection
    detected_domain, detect_markers = _detect_domain(email, domain_part, subject)
    matched_markers.extend(detect_markers)

    # Bauteil-8 A2 (2026-06-09) Step 2.4: no_inserat_url Domain-Drift.
    # Heuristik (immo_heuristic) hat 'no_inserat_url' gesetzt wenn Portal-
    # Sender matched aber keine Inserat-URL im Body steht. Pragmatischer
    # harter Override: Domain shift auf 'werbung' — Portal-Newsletter
    # ohne konkretes Inserat ist per Architektur-Definition werbung,
    # nicht immo. Plus Marker 'domain_drift:portal_without_inserat'
    # fuer Telemetrie.
    if heuristic_markers and "no_inserat_url" in heuristic_markers:
        if any(m.startswith("tier1:portal_domain:") for m in heuristic_markers):
            matched_markers.append("domain_drift:portal_without_inserat")
            detected_domain = "werbung"

    # Bauteil-7 G5 (2026-06-09) Step 2.5: Auto-Reply-Detection.
    # Pragmatisch nach Domain-Detection: Domain bleibt (typischerweise
    # immo/kontakt), aber actionability='auto_reply' triggert Council-
    # Ingest-Skip + Mail-Tab-Versteck. Early-Return, keine weiteren
    # Steps (Time-Decay/Priority/Korridor irrelevant fuer Auto-Replies).
    #
    # Bauteil-8 A4 (2026-06-09): Domain-Gate. Sanicare-Bestellbestätigung
    # ("Bestätigung" im Subject) wurde von G5 als auto_reply gefangen
    # obwohl domain=unsorted/shopping ist. Nur Mails mit detected
    # domain=immo werden als Auto-Reply klassifiziert — andere folgen
    # der normalen Klassifikations-Pipeline.
    if detected_domain == "immo" and _detect_auto_reply(subject, body):
        matched_markers.append("auto_reply:detected")
        return ClassificationResult(
            domain=detected_domain,
            actionability="auto_reply",
            reason="Auto-Reply-Pattern (Subject oder Body-Header)",
            confidence="high",
            matched_markers=matched_markers,
            plugin_class_hint=plugin_class,
        )

    # Step 4: Initial-Actionability (vor Plugin-Refinement)
    initial = _initial_actionability(detected_domain, matched_markers)

    # Step 3: Plugin-Class-Refinement (kann domain + actionability ändern)
    refined_domain, refined_action = _refine_with_plugin_class(
        detected_domain, initial, plugin_class, matched_markers
    )

    # Step 5: Time-Decay-Apply
    decayed_action, decay_markers = _apply_time_decay(
        refined_domain, refined_action, mail_date, ctx.get("time_decay", {})
    )
    matched_markers.extend(decay_markers)

    # Step 6: Active-Priorities-Boost
    final_action = _apply_priority_boost(
        refined_domain, decayed_action,
        ctx.get("active_priorities", []),
        matched_markers,
    )

    # Step 7: PLZ-Country-Filter (2026-05-28 Wunsch 3)
    final_action = _apply_plz_country_filter(
        refined_domain, final_action,
        ctx.get("active_priorities", []),
        heuristic_markers,
        tuple(ctx.get("hauskauf_blocked_countries", HAUSKAUF_BLOCKED_COUNTRIES)),
        matched_markers,
    )

    # Step 8 — tier1-Blocker (projektiert / Zwangsversteigerung).
    final_action = _apply_tier1_blocker_filter(
        refined_domain,
        final_action,
        heuristic_markers,
        matched_markers,
    )

    # Step 9 — PLZ-Korridor-Filter (2026-06-05 Afshin-Spec).
    # 2026-06-05 Zentralisierung: Korridor-Daten aus filters.hauskauf
    # statt priority_relevance.hauskauf.plz_corridor.
    hauskauf_filters = get_filter_config("hauskauf")
    final_action = _apply_corridor_filter(
        refined_domain,
        final_action,
        ctx.get("active_priorities", []),
        heuristic_markers,
        hauskauf_filters.get("korridor_whitelist") or {},
        hauskauf_filters.get("korridor_excludes") or [],
        matched_markers,
    )

    # Confidence-Inference: high if explicit-pattern matched, medium for plugin-driven, low for fallback
    if any("override:" in m or ":domain:" in m for m in matched_markers):
        confidence: Literal["high", "medium", "low"] = "high"
    elif plugin_class and plugin_class != "unklar":
        confidence = "medium"
    elif "fallback" in " ".join(matched_markers):
        confidence = "low"
    else:
        confidence = "medium"

    reason_parts = [f"domain={refined_domain}", f"actionability={final_action}"]
    if matched_markers:
        reason_parts.append(matched_markers[0])
    reason = " · ".join(reason_parts)

    return ClassificationResult(
        domain=refined_domain,
        actionability=final_action,
        reason=reason,
        confidence=confidence,
        matched_markers=matched_markers,
        plugin_class_hint=plugin_class,
    )


def load_user_context(path: Path | None = None) -> dict:
    """Load user_context.yaml; fallback to DEFAULT_CONTEXT on missing/error."""
    import yaml  # local import (only needed at runtime)
    if path is None:
        from paths import USER_CONTEXT_YAML  # noqa: PLC0415
        path = USER_CONTEXT_YAML
    try:
        if not path.exists():
            log.info("user_context.yaml missing — falling back to DEFAULT_CONTEXT")
            return DEFAULT_CONTEXT
        ctx = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # Merge with DEFAULT_CONTEXT for missing keys
        merged = {**DEFAULT_CONTEXT, **ctx}
        # Deep-merge time_decay
        merged["time_decay"] = {**DEFAULT_CONTEXT["time_decay"], **(ctx.get("time_decay") or {})}
        sp = ctx.get("sender_priorities") or {}
        merged["sender_priorities"] = {
            "always_actionable": sp.get("always_actionable", []) or [],
            "always_archive_silent": sp.get("always_archive_silent", []) or [],
        }
        return merged
    except Exception as e:  # noqa: BLE001
        log.warning("user_context.yaml load failed: %s — falling back to DEFAULT_CONTEXT", e)
        return DEFAULT_CONTEXT


# ---------------------------------------------------------------------------
# Regelwerk (system rules) — separate from user_context (personal config).
# Direktive 2026-05-26: zentrale Regelwerk-Quelle für Heuristik + Validator + UI.
# ---------------------------------------------------------------------------

DEFAULT_REGELWERK: dict = {
    "schema_version": "v1",
    "mode": "manual",
    "action_definitions": {
        "actionable": {
            "label": "Aktionable",
            "description": "Erfordert Handlung/Entscheidung.",
            "requires_decision": True,
        },
        "archive": {
            "label": "Archiv",
            "description": "Zur Kenntnis/Referenz, sichtbar bei Suche.",
            "requires_decision": False,
        },
        "archive-silent": {
            "label": "Archiv (stumm)",
            "description": "Nie wieder anschauen.",
            "requires_decision": False,
        },
    },
    "priority_relevance": {},
    "filters": {},  # 2026-06-05: zentralisierte Filter-Defs (block_patterns,
                    # thresholds, korridor_whitelist). Per domain unter
                    # filters.{domain}, gelesen via get_filter_config(domain).
    "council": {},  # 2026-06-05: Council-Konfig (consensus_top_n).
                    # Konsumenten in Folio cross-Repo (multi-agent selber
                    # liest heute nicht aus dieser Section).
    "voice_consensus": {
        # Reihenfolge per Direktive 2026-05-26 §2.3: Lens 1=gemma, 2=qwen3.6, 3=qwen-thinking.
        # `enabled` default True (backward-compat für yaml ohne das Feld).
        "voices": [
            {
                "id": "heuristic",
                "role": "deterministic",
                "lm_studio_model": None,
                "response_strip": "none",
                "enabled": True,
            },
            {
                "id": "gemma-control",
                "role": "control_llm",
                "lm_studio_model": "gemma-4-26b-a4b-it-mlx",
                "response_strip": "code_fence",
                "enabled": True,
            },
            {
                "id": "qwen35b-lens",
                "role": "control_llm",
                "lm_studio_model": "qwen3.6-35b-a3b-ud-mlx",
                "response_strip": "code_fence",
                "enabled": True,
            },
            {
                "id": "qwen-validator",
                "role": "primary_llm",
                "lm_studio_model": "qwen3-30b-a3b-thinking-2507",
                "response_strip": "think",
                "enabled": True,
            },
        ],
        "strictness": "strict",
        "protection_clause": {"on_disagreement": "route_to_actionable_always"},
    },
}


class RegelwerkValidationError(Exception):
    """Raised when regelwerk.yaml violates structural invariants (e.g.
    Cross-Reference user_context.active_priorities ↔ regelwerk.priority_relevance)."""


def load_regelwerk(path: Path | None = None) -> dict:
    """Load regelwerk.yaml; fallback to DEFAULT_REGELWERK on missing/error.

    Strict structural fallback: missing file or YAML parse error returns
    DEFAULT_REGELWERK with a warning. Cross-Reference validation against
    user_context happens separately in validate_regelwerk_against_context().
    """
    import yaml  # local import
    if path is None:
        from paths import REGELWERK_YAML  # noqa: PLC0415
        path = REGELWERK_YAML
    try:
        if not path.exists():
            log.info("regelwerk.yaml missing — falling back to DEFAULT_REGELWERK")
            return DEFAULT_REGELWERK
        rw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged = {**DEFAULT_REGELWERK, **rw}
        # Deep-merge for nested dicts so partial files still work.
        merged["action_definitions"] = {
            **DEFAULT_REGELWERK["action_definitions"],
            **(rw.get("action_definitions") or {}),
        }
        merged["priority_relevance"] = rw.get("priority_relevance") or {}
        merged["filters"] = rw.get("filters") or {}
        merged["council"] = rw.get("council") or {}
        merged["voice_consensus"] = {
            **DEFAULT_REGELWERK["voice_consensus"],
            **(rw.get("voice_consensus") or {}),
        }
        return merged
    except Exception as e:  # noqa: BLE001
        log.warning("regelwerk.yaml load failed: %s — falling back to DEFAULT_REGELWERK", e)
        return DEFAULT_REGELWERK


def validate_regelwerk_against_context(regelwerk: dict, user_context: dict) -> None:
    """Enforce Cross-Reference: every user_context.active_priorities key must
    exist as a key in regelwerk.priority_relevance. Raises on mismatch.

    This is the loader-pflicht described in the Direktive: a misconfiguration
    here can silently mis-classify Suchabos, so we fail loud at startup."""
    active = list(user_context.get("active_priorities") or [])
    defined = set((regelwerk.get("priority_relevance") or {}).keys())
    missing = [p for p in active if p not in defined]
    if missing:
        raise RegelwerkValidationError(
            f"active_priorities {missing} not defined in regelwerk.priority_relevance "
            f"(defined: {sorted(defined)})"
        )


if __name__ == "__main__":
    # Unit-Smoke
    ctx = load_user_context()
    test_cases = [
        ("info@immowelt.de", "Vielen Dank für deine Anfrage", "2026-05-15T10:00:00+00:00", None),
        ("order-update@amazon.de", "Geliefert: UGREEN USB", "2026-05-17T12:00:00+00:00", "geschaeftspost"),
        ("rechnung@vodafone.de", "Ihre Rechnung", "2026-05-10T08:00:00+00:00", "geschaeftspost"),
        ("jobalerts-noreply@linkedin.com", "5 New Jobs", "2026-05-17T06:00:00+00:00", "werbung"),
        ("friend@gmail.com", "Hi, lass uns mal sprechen", "2026-05-17T10:00:00+00:00", "privat"),
        ("MicrosoftRewards@infoemail.microsoft.com", "Daily Reward", "2026-05-17T05:00:00+00:00", "werbung"),
        ("unknown@unknown-domain.com", "Some random subject", "2026-05-17T09:00:00+00:00", None),
    ]
    for sender, subject, mail_date, plugin in test_cases:
        r = classify_domain_actionability(sender, subject, mail_date, plugin, ctx)
        print(f"  {sender:55s} → {r.domain:15s} {r.actionability:18s} ({r.confidence}) {r.reason[:60]}")

    # 2026-05-28 Wunsch 3: PLZ-Country-Filter test cases.
    # Forciert active_priority=hauskauf damit der Filter aktiv ist (DEFAULT_CONTEXT hat [] —
    # ohne explicit aktivieren würde der Filter immer no-op zurückgeben).
    print("\n=== PLZ-Country-Filter (2026-05-28 Wunsch 3) ===")
    plz_ctx = {**ctx, "active_priorities": list(ctx.get("active_priorities", [])) + ["hauskauf"]}
    plz_test_cases = [
        # (label, sender, subject, mail_date, markers, expected_action, expected_marker)
        ("Bartenheim FR", "info@immowelt.de", "Haus in Bartenheim",
         "2026-05-26T10:00:00+00:00",
         ["plz:68870", "plz_city:Bartenheim", "plz_country:FR", "plz_coords:47.6231,7.4978"],
         "archive-silent", "out_of_country:FR"),
        ("Basel CH", "info@immowelt.de", "Wohnung Basel",
         "2026-05-26T10:00:00+00:00",
         ["plz:4051", "plz_city:Basel", "plz_country:CH", "plz_coords:47.5596,7.5886"],
         "actionable", None),
        ("Loulé PT", "info@immo-portal.example", "Haus Loulé",
         "2026-05-26T10:00:00+00:00",
         ["plz:8100", "plz_city:Loulé", "plz_country:PT", "plz_coords:37.1387,-8.0245"],
         "actionable", None),
    ]
    for label, sender, subject, mail_date, markers, expected_action, expected_marker in plz_test_cases:
        r = classify_domain_actionability(
            sender, subject, mail_date, None, plz_ctx, heuristic_markers=markers
        )
        ok_action = (r.actionability == expected_action)
        ok_marker = (expected_marker is None) or (expected_marker in r.matched_markers)
        status = "✓" if (ok_action and ok_marker) else "✗"
        print(f"  {status} {label:18s} → {r.actionability:18s} expected={expected_action} marker={expected_marker}")
        if not (ok_action and ok_marker):
            print(f"      actual markers: {r.matched_markers}")

    # 2026-06-05: PLZ-Korridor-Filter test cases (Step 9).
    print("\n=== PLZ-Korridor-Filter (2026-06-05 Step 9) ===")
    corridor_test_cases = [
        ("Loulé PT (im Korridor)", ["plz:8100", "plz_country:PT"], "actionable", None),
        ("Lagos PT (W-Grenze)", ["plz:8600", "plz_country:PT"], "actionable", None),
        ("Tavira PT (O-Grenze)", ["plz:8800", "plz_country:PT"], "actionable", None),
        ("Lisboa PT (zu weit)", ["plz:1000", "plz_country:PT"], "archive-silent", "out_of_corridor:1000"),
        ("Secondary range entry", ["plz:9050", "plz_country:PT"], "actionable", None),
        ("Excluded within secondary range", ["plz:9020", "plz_country:PT"], "archive-silent", "out_of_corridor:9020"),
        ("Off-region PT", ["plz:2000", "plz_country:PT"], "archive-silent", "out_of_corridor:2000"),
    ]
    for label, markers, expected_action, expected_marker in corridor_test_cases:
        r = classify_domain_actionability(
            "info@immowelt.de", f"Haus in {label}", "2026-06-05T10:00:00+00:00",
            None, plz_ctx, heuristic_markers=markers,
        )
        ok_action = (r.actionability == expected_action)
        ok_marker = (expected_marker is None) or (expected_marker in (r.matched_markers or []))
        status = "✓" if (ok_action and ok_marker) else "✗"
        print(f"  {status} {label:30s} → {r.actionability:18s} expected={expected_action} marker={expected_marker}")
        if not (ok_action and ok_marker):
            print(f"      actual markers: {r.matched_markers}")

    # 2026-06-05: tier1-Blocker test cases (projektiert / Zwangsversteigerung).
    print("\n=== tier1-Blocker-Filter (2026-06-05 projektiert/zwangsversteigerung) ===")
    blocker_test_cases = [
        ("Projektiert", "info@immowelt.de", "Neubau-Projekt Basel",
         ["plz:4051", "plz_country:CH", "tier1:projektiert:true"],
         "archive-silent", "blocked_by:projektiert:true"),
        ("Zwangsversteigerung", "info@immo-portal.example", "Versteigerungstermin Loulé",
         ["plz:8100", "plz_country:PT", "tier1:zwangsversteigerung:true"],
         "archive-silent", "blocked_by:zwangsversteigerung:true"),
        ("Preis auf Anfrage", "info@immowelt.de", "Villa Basel",
         ["plz:4051", "plz_country:CH", "tier1:price_on_request:true"],
         "archive-silent", "blocked_by:price_on_request:true"),
        ("Regulär CH (kein Block)", "info@immowelt.de", "Wohnung Basel",
         ["plz:4051", "plz_country:CH"],
         "actionable", None),
    ]
    for label, sender, subject, markers, expected_action, expected_marker in blocker_test_cases:
        r = classify_domain_actionability(
            sender, subject, "2026-06-05T10:00:00+00:00", None, plz_ctx,
            heuristic_markers=markers,
        )
        ok_action = (r.actionability == expected_action)
        ok_marker = (expected_marker is None) or (expected_marker in (r.matched_markers or []))
        status = "✓" if (ok_action and ok_marker) else "✗"
        print(f"  {status} {label:22s} → {r.actionability:18s} expected={expected_action} marker={expected_marker}")
        if not (ok_action and ok_marker):
            print(f"      actual markers: {r.matched_markers}")
