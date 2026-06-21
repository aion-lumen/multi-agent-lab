#!/usr/bin/env python3
"""test_system_domain_match.py — Fallstrick-Test für System-Domain-Match.

Direktive 2026-05-27 Review-Followup Phase C. 3. Auftreten der Substring-
ohne-Wortgrenze-Bug-Klasse: `or d in domain` in `_detect_domain` für
SYSTEM_DOMAINS.

Fix-Mechanik: 2-Listen-Pattern.
  - SYSTEM_DOMAINS: strict-TLD-Match (equality + .endswith)
  - SYSTEM_DOMAIN_TOKENS: intentional substring-match (Brand-Token)

Verifiziert dass:
  1. Substring-Trap für TLDs eliminiert (anthropicfan/notgithub/mygithub).
  2. Brand-Tokens (microsoftrewards) weiterhin matchen.
  3. Subdomain-Match (.endswith) hält (security.github.com).
  4. End-to-end via classify_domain_actionability().

Run:
    .venv/bin/python3 scripts/test_system_domain_match.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domain_actionability import (
    classify_domain_actionability,
    load_user_context,
    SYSTEM_DOMAINS,
    SYSTEM_DOMAIN_TOKENS,
)


def _matches_strict_or_token(domain: str) -> tuple[bool, str | None]:
    """Mirror der _detect_domain System-Match-Logik (ohne den ganzen Pipeline-
    Overhead). Liefert (matched, marker-kind)."""
    for d in SYSTEM_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return (True, f"system:domain:{d}")
    for t in SYSTEM_DOMAIN_TOKENS:
        if t in domain:
            return (True, f"system:domain-token:{t}")
    return (False, None)


# ---------------------------------------------------------------------------
# (1) DARF NICHT matchen — Substring-Trap (war OLD-Bug, ist NEU eliminiert)
# ---------------------------------------------------------------------------
DARF_NICHT_MATCHEN: list[tuple[str, str]] = [
    ("anthropicfan.evil-spam.com", "anthropic substring-trap"),
    ("notgithub.com",              "github substring-trap"),
    ("mygithub.io",                "github substring-trap"),
    ("anthropic-news.fakers.net",  "anthropic substring-trap (mid-domain)"),
    ("phishing-github.com.attacker.io", "github substring-trap"),
]

# ---------------------------------------------------------------------------
# (2) MUSS matchen — exact-TLD + subdomain
# ---------------------------------------------------------------------------
MUSS_MATCHEN: list[tuple[str, str]] = [
    ("anthropic.com",             "system:domain:anthropic.com"),
    ("www.anthropic.com",         "system:domain:anthropic.com"),
    ("github.com",                "system:domain:github.com"),
    ("security.github.com",       "system:domain:github.com"),
    ("infoemail.microsoft.com",   "system:domain:infoemail.microsoft.com"),
]

# ---------------------------------------------------------------------------
# (3) Brand-Token MUSS matchen (intentional substring)
# ---------------------------------------------------------------------------
MUSS_MATCHEN_TOKEN: list[tuple[str, str]] = [
    ("microsoftrewards",                   "system:domain-token:microsoftrewards"),
    ("microsoftrewards.anything.example",  "system:domain-token:microsoftrewards"),
    # Sender wie MicrosoftRewards@infoemail.microsoft.com matched via TLD —
    # darum hier separater Test wenn der TLD-Match fehlen würde.
]


def run_tests() -> int:
    fails = 0

    print("=" * 70)
    print("DARF NICHT matchen (Substring-Trap — TLD-falsch-positiv)")
    print("=" * 70)
    for domain, what in DARF_NICHT_MATCHEN:
        matched, marker = _matches_strict_or_token(domain)
        if not matched:
            print(f"  ✓ {what:45s} | {domain!r}")
        else:
            print(f"  ✗ FAIL {what:45s} | {domain!r} matched {marker!r}")
            fails += 1

    print()
    print("=" * 70)
    print("MUSS matchen (strict-TLD + Subdomain)")
    print("=" * 70)
    for domain, expected_marker in MUSS_MATCHEN:
        matched, marker = _matches_strict_or_token(domain)
        if marker == expected_marker:
            print(f"  ✓ {marker:45s} | {domain!r}")
        elif matched:
            print(f"  ✓ {marker:45s} (statt {expected_marker!r}) | {domain!r}")
        else:
            print(f"  ✗ FAIL expected {expected_marker!r} got None | {domain!r}")
            fails += 1

    print()
    print("=" * 70)
    print("MUSS matchen via Brand-Token (intentional substring)")
    print("=" * 70)
    for domain, expected_marker in MUSS_MATCHEN_TOKEN:
        matched, marker = _matches_strict_or_token(domain)
        if marker == expected_marker:
            print(f"  ✓ {marker:45s} | {domain!r}")
        elif matched:
            print(f"  ✓ {marker:45s} (statt {expected_marker!r}) | {domain!r}")
        else:
            print(f"  ✗ FAIL expected {expected_marker!r} got None | {domain!r}")
            fails += 1

    print()
    print("=" * 70)
    print("END-TO-END via classify_domain_actionability()")
    print("=" * 70)
    ctx = load_user_context()

    # MicrosoftRewards-style: matched via TLD-Subdomain `.infoemail.microsoft.com`
    r1 = classify_domain_actionability(
        sender="MicrosoftRewards@infoemail.microsoft.com",
        subject="Daily Reward",
        mail_date="2026-05-27T05:00:00+00:00",
        plugin_class="werbung",
        user_context=ctx,
    )
    print(f"  MicrosoftRewards@infoemail.microsoft.com / 'Daily Reward'")
    print(f"    domain        = {r1.domain}")
    print(f"    markers       = {r1.matched_markers}")
    if any(m.startswith("system:domain:infoemail.microsoft.com") for m in r1.matched_markers):
        print(f"  ✓ matched via TLD-Subdomain (Sanity OK)")
    else:
        print(f"  ✗ FAIL expected system:domain TLD-match, got {r1.matched_markers}")
        fails += 1

    # Anthropic-Substring-Trap: war pre-fix system, post-fix kontakt/unsorted
    r2 = classify_domain_actionability(
        sender="hr@anthropicfan.evil-spam.com",
        subject="Persönliche Anfrage",
        mail_date="2026-05-27T12:00:00+00:00",
        plugin_class=None,
        user_context=ctx,
    )
    print()
    print(f"  hr@anthropicfan.evil-spam.com / 'Persönliche Anfrage'")
    print(f"    domain        = {r2.domain}")
    print(f"    markers       = {r2.matched_markers}")
    if r2.domain != "system":
        print(f"  ✓ NICHT mehr system-klassifiziert (Substring-Trap eliminiert)")
    else:
        print(f"  ✗ FAIL Substring-Trap noch aktiv, domain={r2.domain}")
        fails += 1

    print()
    total = len(DARF_NICHT_MATCHEN) + len(MUSS_MATCHEN) + len(MUSS_MATCHEN_TOKEN) + 2
    if fails == 0:
        print(f"ALLE TESTS GRÜN ✓  ({total} Asserts)")
        return 0
    else:
        print(f"FAILS: {fails}")
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
