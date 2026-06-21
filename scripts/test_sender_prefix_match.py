#!/usr/bin/env python3
"""test_sender_prefix_match.py — Fallstrick-Test für Sender-Prefix-Match.

Direktive 2026-05-27 Sender-Prefix-Fix.
Verifiziert dass:
  1. Die Substring-Trap-Klasse (info in linkedin-info, team in teamleader,
     news in businessnews, service in subservice etc.) eliminiert ist.
  2. Echte Treffer (exact + startswith-mit-Trennzeichen + segment >= 5)
     weiterhin matchen.
  3. End-to-end via `classify_domain_actionability()`: linkedin-info@-Sender
     fällt nicht mehr in werbung/bulk-Klasse durch.

Run:
    ~/Projects/aion-lumen/multi-agent/.venv/bin/python3 \\
        ~/Projects/aion-lumen/multi-agent/scripts/test_sender_prefix_match.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domain_actionability import (
    _prefix_matches_any,
    classify_domain_actionability,
    load_user_context,
    BULK_SENDER_PREFIXES,
    WERBUNG_SENDER_PREFIXES,
)


# ---------------------------------------------------------------------------
# (1) DARF NICHT matchen — Substring-Trap
# ---------------------------------------------------------------------------
DARF_NICHT_MATCHEN: list[tuple[str, tuple[str, ...], str]] = [
    # Bulk-prefix-Trap (kurze Tokens info/team/news/service als Wort-Mitte)
    ("linkedin-info",  BULK_SENDER_PREFIXES, "info in linkedin-info"),
    ("teamleader",     BULK_SENDER_PREFIXES, "team in teamleader"),
    ("businessnews",   BULK_SENDER_PREFIXES, "news in businessnews"),
    ("subservice",     BULK_SENDER_PREFIXES, "service in subservice"),
    ("myteam",         BULK_SENDER_PREFIXES, "team in myteam"),
    ("newsfeed",       BULK_SENDER_PREFIXES, "news in newsfeed"),
    # Werbung-prefix-Trap (newsletter ist ≥5 aber „news" in WERBUNG_SENDER_PREFIXES ist nicht drin —
    # nur prüfen dass keine fragmente fälschlich matchen)
    ("promotions-info", WERBUNG_SENDER_PREFIXES, "promotions startswith → matched ist erwünscht"),
]

# ---------------------------------------------------------------------------
# (2) MUSS matchen — exact + delimiter + segment≥5
# ---------------------------------------------------------------------------
MUSS_MATCHEN: list[tuple[str, tuple[str, ...], str]] = [
    # Bulk — exact
    ("noreply",         BULK_SENDER_PREFIXES, "noreply"),
    ("no-reply",        BULK_SENDER_PREFIXES, "no-reply"),
    ("donotreply",      BULK_SENDER_PREFIXES, "donotreply"),
    ("notifications",   BULK_SENDER_PREFIXES, "notifications"),
    ("info",            BULK_SENDER_PREFIXES, "info"),
    ("support",         BULK_SENDER_PREFIXES, "support"),
    # Bulk — startswith + delimiter
    ("noreply-system",  BULK_SENDER_PREFIXES, "noreply"),
    ("support.team",    BULK_SENDER_PREFIXES, "support"),
    ("info_bot",        BULK_SENDER_PREFIXES, "info"),
    # Bulk — segment ≥ 5 chars
    ("acme-notifications", BULK_SENDER_PREFIXES, "notifications"),
    # Werbung — exact
    ("newsletter",      WERBUNG_SENDER_PREFIXES, "newsletter"),
    ("marketing",       WERBUNG_SENDER_PREFIXES, "marketing"),
    ("promo",           WERBUNG_SENDER_PREFIXES, "promo"),
    ("promotions",      WERBUNG_SENDER_PREFIXES, "promotions"),
    ("deals",           WERBUNG_SENDER_PREFIXES, "deals"),
    # Werbung — startswith + delimiter
    ("newsletter-amazon", WERBUNG_SENDER_PREFIXES, "newsletter"),
    ("marketing.brand",   WERBUNG_SENDER_PREFIXES, "marketing"),
    # Werbung — segment ≥ 5 chars
    ("acme-newsletter", WERBUNG_SENDER_PREFIXES, "newsletter"),
    ("brand-marketing", WERBUNG_SENDER_PREFIXES, "marketing"),
]


def run_tests() -> int:
    fails = 0

    print("=" * 70)
    print("DARF NICHT matchen (Substring-Trap)")
    print("=" * 70)
    for prefix, tokens, what in DARF_NICHT_MATCHEN:
        # Skip the special promotions-info case — that's expected to match
        # via startswith('promotions-'); included only to document the semantics.
        if "matched ist erwünscht" in what:
            continue
        hit = _prefix_matches_any(prefix, tokens)
        if hit is None:
            print(f"  ✓ {what:40s} | {prefix!r}")
        else:
            print(f"  ✗ FAIL {what:40s} | {prefix!r} matched {hit!r}")
            fails += 1

    print()
    print("=" * 70)
    print("MUSS matchen (exact + startswith+delimiter + segment≥5)")
    print("=" * 70)
    for prefix, tokens, expected_hit in MUSS_MATCHEN:
        hit = _prefix_matches_any(prefix, tokens)
        if hit == expected_hit:
            print(f"  ✓ {hit:18s} | {prefix!r}")
        elif hit is not None:
            print(f"  ✓ {hit:18s} (statt {expected_hit!r}) | {prefix!r}")
        else:
            print(f"  ✗ FAIL expected {expected_hit!r:20s} got None | {prefix!r}")
            fails += 1

    print()
    print("=" * 70)
    print("END-TO-END via classify_domain_actionability()")
    print("=" * 70)
    ctx = load_user_context()

    # Vorher-bug-Case: linkedin-info@-Sender mit neutralem Subject. Mit OLD-Code
    # würde `"info" in "linkedin-info"` matchen → bulk-klassifiziert. Mit NEU
    # sollte es als kontakt durchgehen (nicht-bulk-prefix).
    r = classify_domain_actionability(
        sender="linkedin-info@example.com",
        subject="Hi, persönliche Nachricht für dich",
        mail_date="2026-05-27T12:00:00+00:00",
        plugin_class=None,
        user_context=ctx,
    )
    print(f"  linkedin-info@example.com / 'Hi, persönliche Nachricht'")
    print(f"    domain        = {r.domain}")
    print(f"    actionability = {r.actionability}")
    print(f"    markers       = {r.matched_markers}")
    if r.domain == "kontakt":
        print(f"  ✓ linkedin-info@ ist jetzt kontakt (war: bulk fall-through → unsorted, vor Fix)")
    else:
        print(f"  ✗ FAIL linkedin-info@ erwartet kontakt, bekommen {r.domain}")
        fails += 1

    # Sanity: noreply@ bleibt bulk-fall-through (= unsorted).
    r2 = classify_domain_actionability(
        sender="noreply@somecorp.example",
        subject="Generischer Subject",
        mail_date="2026-05-27T12:00:00+00:00",
        plugin_class=None,
        user_context=ctx,
    )
    print()
    print(f"  noreply@somecorp.example / 'Generischer Subject'")
    print(f"    domain        = {r2.domain}")
    print(f"    markers       = {r2.matched_markers}")
    if r2.domain == "unsorted" and any(m == "unsorted:fallback" for m in r2.matched_markers):
        print(f"  ✓ noreply@ bleibt bulk-fall-through → unsorted (Sanity OK)")
    else:
        print(f"  ✗ FAIL noreply@ erwartet unsorted-fallback, bekommen {r2.domain}")
        fails += 1

    # Sanity: acme-newsletter@ matched Werbung via segment-match.
    r3 = classify_domain_actionability(
        sender="acme-newsletter@brand.example",
        subject="Unsere Mai-News",
        mail_date="2026-05-27T12:00:00+00:00",
        plugin_class=None,
        user_context=ctx,
    )
    print()
    print(f"  acme-newsletter@brand.example / 'Unsere Mai-News'")
    print(f"    domain        = {r3.domain}")
    print(f"    markers       = {r3.matched_markers}")
    if r3.domain == "werbung":
        print(f"  ✓ acme-newsletter@ matched Werbung via segment-match (newsletter ≥ 5)")
    else:
        print(f"  ✗ FAIL acme-newsletter@ erwartet werbung, bekommen {r3.domain}")
        fails += 1

    print()
    total = (
        sum(1 for _, _, w in DARF_NICHT_MATCHEN if "matched ist erwünscht" not in w)
        + len(MUSS_MATCHEN) + 3  # 3 end-to-end
    )
    if fails == 0:
        print(f"ALLE TESTS GRÜN ✓  ({total} Asserts)")
        return 0
    else:
        print(f"FAILS: {fails}")
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
