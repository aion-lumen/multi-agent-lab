#!/usr/bin/env python3
"""
sender_heuristic.py - Pre-classification by sender (Static + Dynamic).

Used by Librarian-Worker and production_worker (Phase 3.5b) before any
LLM call. Returns a category from Schema v2 if the sender matches a
static or dynamic rule, otherwise None.

Static rules:
- STATIC_SERVICE_DOMAINS: known service platforms -> 'geschaeftspost'
- MARKETING_DOMAIN_PATTERNS: subdomain regex -> 'werbung'

Dynamic rules:
- state/sender-heuristics.json (output of build_sender_db.py)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MARKETING_PATTERNS_JSON, SENDER_HEURISTICS_JSON  # noqa: E402

HEURISTICS_FILE = SENDER_HEURISTICS_JSON
MARKETING_PATTERNS_FILE = MARKETING_PATTERNS_JSON

STATIC_SERVICE_DOMAINS: set[str] = {
    "amazon.de", "amazon.com",
    "ebay.de", "ebay.com",
    "paypal.com", "paypal.de",
    "stripe.com", "klarna.com", "klarna.de",
    "yahoo.com", "yahoo.de",
    "google.com", "googlemail.com",
    "apple.com", "icloud.com",
    "microsoft.com", "outlook.com",
    "github.com", "gitlab.com",
    "bitwarden.com",
    "dhl.de", "dpd.com", "dpd.de",
    "hermes-germany.de", "post.ch", "deutschepost.de",
    "uber.com", "airbnb.com", "booking.com",
    "netflix.com",
    "deutschebahn.com", "bahn.de",
    "swisspass.ch", "sbb.ch",
    "mywingo.ch", "salt.ch", "sunrise.ch",
    "telekom.de", "vodafone.de",
}

MARKETING_DOMAIN_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p) for p in (
        r"^newsletter[\.@]",
        r"^marketing[\.@]",
        r"^deals[\.@]",
        r"^promo[\.@]",
        r"^offers[\.@]",
        r"^news[\.@]",
        r"^info-deals[\.@]",
        r"^angebote[\.@]",
    )
)


def load_dynamic_heuristics() -> dict:
    """Load dynamic heuristics file. Tolerant against missing file."""
    if not HEURISTICS_FILE.exists():
        return {"private_senders": [], "service_senders": [],
                "marketing_senders": []}
    try:
        return json.loads(HEURISTICS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"private_senders": [], "service_senders": [],
                "marketing_senders": []}


def extract_email_address(sender_field: str) -> str:
    """From 'Name <email@domain.com>' get 'email@domain.com' (lowercased)."""
    if not sender_field:
        return ""
    m = re.search(r"<([^>]+)>", sender_field)
    if m:
        return m.group(1).strip().lower()
    return sender_field.strip().lower()


def extract_domain(email: str) -> str:
    if "@" in email:
        return email.split("@", 1)[1]
    return email


def heuristic_classify(sender_field: str) -> tuple[str | None, str]:
    """
    Returns (category, reason).
    category in {'werbung', 'geschaeftspost', 'privat'} or None.
    None means: no static/dynamic rule matched - pass to LLM.
    """
    email = extract_email_address(sender_field)
    if not email:
        return None, "empty-sender"
    domain = extract_domain(email)

    # 1. Static marketing patterns on subdomain
    for pattern in MARKETING_DOMAIN_PATTERNS:
        if pattern.search(email):
            return "werbung", f"static-marketing-pattern: {pattern.pattern}"

    dynamic = load_dynamic_heuristics()

    # 2. Dynamic marketing senders
    for entry in dynamic.get("marketing_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es) or domain in es:
            return "werbung", f"dynamic-marketing-match: {es}"

    # 3. Dynamic private senders
    for entry in dynamic.get("private_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es):
            return "privat", f"dynamic-private-match: {es}"

    # 4. Static service domains
    if domain in STATIC_SERVICE_DOMAINS:
        return "geschaeftspost", f"static-service-domain: {domain}"

    # 5. Dynamic service senders
    for entry in dynamic.get("service_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es) or domain in es:
            return "geschaeftspost", f"dynamic-service-match: {es}"

    return None, "no-heuristic-match"


# === Schema v2.1 additions: subject-aware classification ============

def load_marketing_patterns() -> dict:
    """Load state/marketing-patterns.json. Tolerant against missing file."""
    if not MARKETING_PATTERNS_FILE.exists():
        return {"subject_patterns": [], "emoji_patterns": []}
    try:
        return json.loads(MARKETING_PATTERNS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"subject_patterns": [], "emoji_patterns": []}


def subject_marketing_score(subject: str) -> tuple[int, list[str]]:
    """
    Returns (score, matched_patterns).
    Score is number of subject_patterns + emoji_patterns found in subject.
    """
    if not subject:
        return 0, []
    sub_lower = subject.lower()
    patterns = load_marketing_patterns()
    matched: list[str] = []
    for p in patterns.get("subject_patterns", []):
        if p and p.lower() in sub_lower:
            matched.append(p)
    for e in patterns.get("emoji_patterns", []):
        if e and e in subject:
            matched.append(e)
    return len(matched), matched


def heuristic_classify_v2(
    sender_field: str, subject: str = ""
) -> tuple[str | None, str, bool]:
    """
    Schema v2.1 heuristic: subject-aware classification.

    Returns (value, reason, route_to_llm).
    - value in {'werbung', 'geschaeftspost', 'privat'} or None
    - route_to_llm=True signals: heuristic abstains, executor+validator
      should classify.

    Order:
      1. Static marketing subdomain patterns -> werbung
      2. Dynamic marketing_senders          -> werbung
      3. Dynamic private_senders            -> privat
      4. Dynamic ambiguous_senders (NEW):
         - subject_marketing_score >= 1 -> werbung
         - else                        -> route_to_llm
      5. Static service domains:
         - subject_marketing_score >= 2 -> werbung (override)
         - else                        -> geschaeftspost
      6. Dynamic service_senders (analog to 5)
      7. No match -> route_to_llm
    """
    email = extract_email_address(sender_field)
    if not email:
        return None, "empty-sender", True
    domain = extract_domain(email)

    # 1. Static marketing patterns on subdomain
    for pattern in MARKETING_DOMAIN_PATTERNS:
        if pattern.search(email):
            return "werbung", \
                f"v2-static-marketing-pattern: {pattern.pattern}", False

    dynamic = load_dynamic_heuristics()
    score, matched = subject_marketing_score(subject)

    # 2. Dynamic marketing senders
    for entry in dynamic.get("marketing_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es) or domain in es:
            return "werbung", \
                f"v2-dynamic-marketing-match: {es}", False

    # 3. Dynamic private senders
    for entry in dynamic.get("private_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es):
            return "privat", f"v2-dynamic-private-match: {es}", False

    # 4. Dynamic ambiguous senders (Schema v2.1)
    for entry in dynamic.get("ambiguous_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es) or domain in es:
            if score >= 1:
                return "werbung", (
                    f"v2-ambiguous-marketing: sender={es} "
                    f"subject_matches={matched}"), False
            return None, (
                f"v2-ambiguous-route-to-llm: sender={es} "
                f"subject_score=0"), True

    # 5. Static service domains
    if domain in STATIC_SERVICE_DOMAINS:
        if score >= 2:
            return "werbung", (
                f"v2-service-domain-marketing-override: domain={domain} "
                f"subject_matches={matched}"), False
        return "geschaeftspost", \
            f"v2-static-service-domain: {domain}", False

    # 6. Dynamic service senders
    for entry in dynamic.get("service_senders", []):
        es = (entry.get("sender") or "").lower()
        if not es:
            continue
        if email == extract_email_address(es) or domain in es:
            if score >= 2:
                return "werbung", (
                    f"v2-dynamic-service-marketing-override: sender={es} "
                    f"subject_matches={matched}"), False
            return "geschaeftspost", \
                f"v2-dynamic-service-match: {es}", False

    # 7. No match -> LLM
    return None, "v2-no-heuristic-match", True


if __name__ == "__main__":
    # Self-test for both v1 and v2
    cases = [
        ("Amazon <noreply@amazon.de>", "Bestellt: 'Kabel'"),
        ("Amazon <noreply@amazon.de>",
         "10% Rabatt auf Sale - jetzt kaufen 🛍️"),
        ("newsletter@booking.com", "Some deal"),
        ("Marketing <deals@booking.com>", "Sale Sale Sale"),
        ("John Doe <jdoe@example.org>", "Hallo"),
        ("Polymarket <noreply@polymarket.com>",
         "Here's how you can copy smart money 🐳"),
        ("Polymarket <noreply@polymarket.com>",
         "Trade confirmation #12345"),
        ("Der Immobilienfotograf <andreas@derimmobilienfotograf.net>",
         "Rechnungsadresse"),
        ("no-reply@unknown-domain.xyz", "Hello"),
    ]
    print("== heuristic_classify (v1) ==")
    for s, _ in cases:
        cat, reason = heuristic_classify(s)
        print(f"  {s:<55} -> {cat or '<None>':<18} ({reason})")
    print()
    print("== heuristic_classify_v2 (subject-aware) ==")
    for s, sub in cases:
        cat, reason, route = heuristic_classify_v2(s, sub)
        print(f"  {s:<55} | {sub[:30]:<30}")
        print(f"    -> {cat or '<None>':<18} route_to_llm={route} "
              f"({reason})")
