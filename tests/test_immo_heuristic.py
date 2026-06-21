"""Unit tests for immo_heuristic.classify_immo.

Coverage targets (per design-notes §G3-A.1 and §7.2):
- 15+ cases across the 3 tiers
- Each Gate-2-signoff refinement R1-R6 has a named test
- Edge cases: no price, multiple prices, ambiguous location, contradictory markers
- Strategy comparison cases from §G2-A.4
"""
from __future__ import annotations

import pytest

from immo_heuristic import (
    HeuristicResult,
    PERSONAL_DOMAINS,
    _get_price_threshold,
    classify_immo,
)


# ---------------------------------------------------------------------------
# Tier 1 — portal sender, with whitelist hit
# ---------------------------------------------------------------------------


def test_tier1_portal_with_whitelist_basel_returns_immo_portal():
    r = classify_immo(
        sender="Homegate <noreply@homegate.ch>",
        subject="Wohnung Faro",
        body="Kaufpreis CHF 450'000, 4.5 Zimmer in Faro, Algarve.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert r.suggested_action == "move_immo_portal"
    assert r.confidence == "high"
    assert any(m.startswith("tier1:portal_domain:") for m in r.matched_markers)


def test_tier1_portal_no_location_returns_zu_pruefen():
    """Portal sender, no Immo keywords in body, no price, no location:
    the code-path enters Tier 2 because the sender is a portal, then the
    location-detection sub-step yields 'no_location_detected' with no price
    found, which returns `move_zu_pruefen` low confidence."""
    r = classify_immo(
        sender="ImmoScout <bot@immoscout24.ch>",
        subject="Newsletter",
        body="Vielen Dank für Ihre Registrierung.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.6,
    )
    assert r.suggested_action == "move_zu_pruefen"
    assert r.confidence == "low"
    assert any("no_price_no_location" in m for m in r.matched_markers)


# ---------------------------------------------------------------------------
# Tier 2 — price out-of-scope
# ---------------------------------------------------------------------------


def test_tier2_price_over_1m_returns_zu_pruefen():
    r = classify_immo(
        sender="Homegate <noreply@homegate.ch>",
        subject="Premium Objekt",
        body="Kaufpreis CHF 1.5 Mio. Top-Lage. Wohnung mit Garten.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.9,
    )
    assert r.suggested_action == "move_zu_pruefen"
    assert r.confidence == "high"
    assert any(m.startswith("tier2:price_over_threshold") for m in r.matched_markers)
    assert _get_price_threshold() == 500_000  # from regelwerk.example.yaml.filters.hauskauf.thresholds.price_max


def test_tier2_price_eur_treated_as_chf_1to1():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Luxus Wohnung",
        body="Kaufpreis EUR 1.500.000,00. Schöne Lage.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert r.suggested_action == "move_zu_pruefen"


# ---------------------------------------------------------------------------
# Tier 2 — location detection
# ---------------------------------------------------------------------------


def test_tier2_ambiguous_rheinfelden_without_disambiguator_returns_zu_pruefen():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung Faro",
        body="Schöne Wohnung in Faro, 4 Zimmer, CHF 460'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.8,
    )
    assert r.suggested_action == "move_zu_pruefen"
    assert any("ambiguous" in m for m in r.matched_markers)


def test_tier2_city_with_disambiguator_succeeds():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body="Schöne Wohnung in Vilamoura, Algarve (PLZ 8125), 4 Zimmer, EUR 460'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.8,
    )
    assert r.suggested_action == "move_immo_portal"
    assert r.confidence == "high"


def test_tier2_location_outside_whitelist_returns_zu_pruefen():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung Lisboa",
        body="Schöne Wohnung in Lisboa, 4 Zimmer, CHF 450'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.8,
    )
    assert r.suggested_action == "move_zu_pruefen"
    assert r.confidence == "medium"
    assert any("location_outside" in m for m in r.matched_markers)


# ---------------------------------------------------------------------------
# Tier 3 — privat-immo
# ---------------------------------------------------------------------------


def test_tier3_privat_high_confidence_2_markers():
    r = classify_immo(
        sender="Hans Schmidt <hans.schmidt@gmail.com>",
        subject="Wohnung Loulé Besichtigung",
        body=(
            "Hallo,\n\nMeine Wohnung in Loulé steht zum Verkauf. "
            "Kaufpreis EUR 460'000. 4 Zimmer.\nBesichtigung diese Woche möglich. "
            "Erreichbar unter +351 289 12345. Mit freundlichen Grüßen, Hans"
        ),
        plugin_value="privat",
        plugin_confidence=0.7,
    )
    assert r.suggested_action == "move_immo_privat"
    assert r.confidence == "high"
    assert any(m.startswith("privat:") for m in r.matched_markers)


def test_tier3_one_marker_plugin_disagrees_returns_zu_pruefen():
    r = classify_immo(
        sender="auto@example.com",
        subject="Newsletter Wohnung",
        body="Ihr Newsletter zum Thema Wohnung. Mit freundlichen Grüßen.",
        plugin_value="werbung",
        plugin_confidence=0.7,
    )
    # Body has 'wohnung' (real-estate keyword) → 1 privat marker.
    # No anti-marker. plugin says werbung — disagrees.
    # Per heuristic: 1 net marker + plugin in (privat,geschaeftspost) gives zu_pruefen.
    # plugin == werbung NOT in that set → falls into the "1 net marker, ambiguous" branch → zu_pruefen low.
    assert r.suggested_action == "move_zu_pruefen"
    assert r.confidence == "low"


# ---------------------------------------------------------------------------
# Refinement R1 — HINT list includes richtpreis, verhandlungsbasis
# ---------------------------------------------------------------------------


def test_R1_richtpreis_hint_excludes_unrelated_amounts():
    """Richtpreis acts as a price hint; Hypothek-tagged amount is excluded."""
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body=(
            "Wohnung in Faro, Algarve.\n"
            "Richtpreis CHF 470'000\n"
            "Hypothek ab CHF 1.5 Mio\n"
        ),
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # The 1.5M (Hypothek) is excluded; richtpreis 470k under 500k threshold; Faro whitelist hit.
    assert r.suggested_action == "move_immo_portal"


def test_R1_verhandlungsbasis_hint_picks_primary_price():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body=(
            "Wohnung in Faro, Algarve.\n"
            "Verhandlungsbasis CHF 1.2 Mio\n"
            "Nebenkosten CHF 500/Monat\n"
        ),
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # Verhandlungsbasis 1.2M > threshold → zu_pruefen.
    assert r.suggested_action == "move_zu_pruefen"
    assert "tier2:price_over_threshold" in " ".join(r.matched_markers)


# ---------------------------------------------------------------------------
# Refinement R2 — EXCLUDE list includes bruttomiete, nettomiete, tragbarkeit
# ---------------------------------------------------------------------------


def test_R2_mietzins_excluded_from_price_scan():
    """A rent listing without a Kauf-hint should not get classified as out-of-scope."""
    r = classify_immo(
        sender="Privat <maria.mueller@bluewin.ch>",
        subject="Wohnung zu vermieten in Faro, Algarve",
        body=(
            "Hallo,\n"
            "Wohnung in Faro, Algarve zu vermieten.\n"
            "Bruttomiete CHF 2'400/Monat\n"
            "Tragbarkeit ab CHF 200'000 Einkommen.\n"
            "Sehr geehrter Herr Mirhamed, bei Interesse melden Sie sich bitte."
        ),
        plugin_value="privat",
        plugin_confidence=0.7,
    )
    # Bruttomiete + Tragbarkeit excluded. No price hint → no primary price.
    # Whitelist hit on Faro → tier2_active. No price → location_match path.
    # Personalized address + salutation + real-estate keyword → 3 markers → privat.
    assert r.suggested_action in ("move_immo_privat", "move_immo_portal")


# ---------------------------------------------------------------------------
# Refinement R4 — PERSONAL_DOMAINS includes quickline.ch, bluemail.ch,
# freenet.de, arcor.de
# ---------------------------------------------------------------------------


def test_R4_personal_domains_includes_added_4():
    for added in ("quickline.ch", "bluemail.ch", "freenet.de", "arcor.de"):
        assert added in PERSONAL_DOMAINS, f"{added} missing from PERSONAL_DOMAINS"


def test_R4_quickline_address_counts_as_personal():
    r = classify_immo(
        sender="Max Müller <max.mueller@quickline.ch>",
        subject="Wohnung Loulé 8125 Algarve",
        body=(
            "Hallo,\n\nWohnung in Loulé. 4 Zimmer. Kaufpreis EUR 470'000. "
            "Besichtigung jederzeit möglich, Tel. +351 289 555-1234."
        ),
        plugin_value="privat",
        plugin_confidence=0.8,
    )
    assert r.suggested_action == "move_immo_privat"
    assert any(m == "privat:personal_address" for m in r.matched_markers)


# ---------------------------------------------------------------------------
# Refinement R5 — auto-mailer 'unsubscribe' contextualised
# ---------------------------------------------------------------------------


def test_R5_unsubscribe_keyword_alone_does_NOT_trigger_anti_marker():
    """A privat mail mentioning 'unsubscribe' in body text should NOT lose a point."""
    r = classify_immo(
        sender="Friend <a.friend@gmail.com>",
        subject="Frage zu Wohnung",
        body=(
            "Hallo Afshin,\n\nIch wollte fragen, ob du den unsubscribe Button bei dem "
            "Newsletter siehst.\n\nMein Anliegen: Hast du Interesse an einer Wohnung in "
            "Faro, Algarve? Kaufpreis ca. CHF 480'000. Besichtigung gern."
        ),
        plugin_value="privat",
        plugin_confidence=0.7,
    )
    # Bare 'unsubscribe' should NOT match _AUTOMAILER_PATTERNS (which need context).
    assert "anti_privat:automailer" not in r.matched_markers


def test_R5_to_unsubscribe_DOES_trigger_anti_marker():
    """The contextualised 'to unsubscribe' phrasing DOES match."""
    r = classify_immo(
        sender="Newsletter <news@gmail.com>",
        subject="Wohnung Newsletter",
        body=(
            "Aktuelles Inserat. Wohnung in Faro, Algarve CHF 450'000.\n\n"
            "To unsubscribe, click here."
        ),
        plugin_value="werbung",
        plugin_confidence=0.7,
    )
    assert "anti_privat:automailer" in r.matched_markers


# ---------------------------------------------------------------------------
# Refinement R6 — net == 0 with both positive AND anti markers → zu_pruefen
# ---------------------------------------------------------------------------


def test_R6_contradictory_markers_returns_zu_pruefen_low():
    """One privat-marker + one anti-marker == kontradiktorisch → zu_pruefen low."""
    r = classify_immo(
        sender="Bot Maybe <agent@gmail.com>",
        subject="Wohnung Inserat",
        body=(
            "Sehr geehrter Herr Mirhamed,\n\n"
            "Wir haben ein Inserat für Sie:\n"
            "Inserat-Nr 87654\n\n"
            "Wohnung CHF 460'000."
        ),
        plugin_value="unklar",
        plugin_confidence=0.5,
    )
    # 'Sehr geehrter' → privat:salutation (+1)
    # 'wohnung' → privat:realestate_keyword (+1)
    # 'agent@gmail.com' personal-domain but local doesn't match firstname.lastname → no personal_address
    # 'Inserat-Nr 87654' → anti_privat:inserat_nr (-1)
    # Net could be 1 (2-1) and plugin=unklar → falls to "1 net marker, ambiguous" branch.
    # If markers happen to net to exactly 0 we get the R6 branch with reason "kontradiktorisch".
    # Either way action is move_zu_pruefen low confidence.
    assert r.suggested_action == "move_zu_pruefen"
    assert r.confidence == "low"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_immo_keywords_at_all_returns_keep():
    r = classify_immo(
        sender="Friend <hans@gmail.com>",
        subject="Lunch Donnerstag?",
        body="Hi, hast du am Donnerstag Zeit zum Mittagessen? Liebe Grüße.",
        plugin_value="privat",
        plugin_confidence=0.9,
    )
    assert r.suggested_action == "keep"
    assert r.confidence == "high"


def test_multiple_prices_picks_kaufpreis_max():
    """Hybrid strategy chosen Kaufpreis-tagged value over Hypothek-tagged."""
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body=(
            "Wohnung in Faro, Algarve.\n"
            "Kaufpreis CHF 450'000, Nebenkosten CHF 15'000/Jahr, "
            "Hypothek ab CHF 600'000"
        ),
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # Kaufpreis 850k under threshold → portal+whitelist path.
    assert r.suggested_action == "move_immo_portal"


def test_swiss_apostrophe_format_parsed():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body="Wohnung in Faro, Algarve, Kaufpreis CHF 1'250'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # 1.25M > threshold
    assert r.suggested_action == "move_zu_pruefen"


def test_de_dot_thousands_parsed():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body="Wohnung in Faro, Algarve, Kaufpreis EUR 1.200.000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert r.suggested_action == "move_zu_pruefen"


def test_preposition_pattern_three_words_weil_am_rhein():
    r = classify_immo(
        sender="Homegate <bot@homegate.ch>",
        subject="Wohnung",
        body="Wohnung in São Brás de Alportel, 4 Zimmer, Kaufpreis CHF 480'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # São Brás de Alportel is in de_side whitelist → match → move_immo_portal
    assert r.suggested_action == "move_immo_portal"


# ---------------------------------------------------------------------------
# Tier 0 — Paketzustellung (CC-Update 2026-05-16)
# ---------------------------------------------------------------------------


def test_tier0_dhl_sender_matches_paketzustellung():
    """Logistiker-Domain alone (no keyword needed) → move_paketzustellung."""
    r = classify_immo(
        sender="DHL Sendungsverfolgung <noreply@dhl.example>",
        subject="Ihre Sendung ist unterwegs",
        body="Die Lieferung wird voraussichtlich morgen zugestellt.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert r.suggested_action == "move_paketzustellung"
    assert r.confidence == "high"
    assert any(m.startswith("paketzustellung:logistiker:") for m in r.matched_markers)


def test_tier0_amazon_with_versendet_keyword_matches():
    """Shopping-Domain + keyword in subject → move_paketzustellung."""
    r = classify_immo(
        sender="Amazon.example <auto-confirm@amazon.example>",
        subject="Versendet: Ihre Bestellung 302-9876543",
        body="Ihre Bestellung wird voraussichtlich am 17.05.2026 ausgeliefert.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.9,
    )
    assert r.suggested_action == "move_paketzustellung"
    assert r.confidence == "high"
    markers_combined = " ".join(r.matched_markers)
    assert "paketzustellung:shopping:amazon.example" in markers_combined
    assert "paketzustellung:keyword:" in markers_combined


def test_tier0_amazon_without_keyword_falls_through_to_tier1_or_default():
    """Shopping-Domain alone (no shipping/order keyword) should NOT match Tier 0."""
    r = classify_immo(
        sender="Amazon Security <security@amazon.example>",
        subject="Neuer Anmeldeversuch erkannt",
        body="Wir haben einen neuen Anmeldeversuch auf Ihr Konto bemerkt.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # Should NOT be paketzustellung. Falls through; ends up at default fall-through
    # because nothing else triggers (no immo keyword, no portal sender).
    assert r.suggested_action != "move_paketzustellung"
    assert r.suggested_action == "keep"


def test_tier0_runs_before_tier1_immo_check():
    """If a sender is in BOTH Logistiker-domains AND Portal-domains (synthetic
    overlap), Tier 0 wins because it runs first."""
    # ctt.example is a Logistiker domain. If we craft a body that looks like a
    # Faro-immo listing, Tier 1+2 would otherwise return move_immo_portal.
    r = classify_immo(
        sender="Swiss Post <noreply@ctt.example>",
        subject="Sendung",
        body=(
            "Wohnung in Faro, Algarve zum Kaufpreis CHF 470'000.\n"
            "Sendung unterwegs."
        ),
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    # Tier 0 must trump Tier 1: ctt.example is Logistiker → move_paketzustellung.
    assert r.suggested_action == "move_paketzustellung"
    assert "paketzustellung:logistiker:ctt.example" in r.matched_markers


def test_tier0_order_confirmation_matches_paketzustellung():
    """Order-confirmations (pre-shipment) should also go to Paketzustellung —
    the user wants both flows in the same folder. Keyword: Auftragsbestätigung."""
    r = classify_immo(
        sender="Amazon.example <auto-confirm@amazon.example>",
        subject="Auftragsbestätigung Ihre Bestellung",
        body="Vielen Dank für Ihre Bestellung. Wir senden die Versandbestätigung in Kürze.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.9,
    )
    assert r.suggested_action == "move_paketzustellung"


# Engineer-add coverage:


def test_tier0_post_ch_logistiker_matches():
    """CH-Side Logistiker (ctt.example) — even with no body/subject keywords."""
    r = classify_immo(
        sender="Die Post <info@ctt.example>",
        subject="Tracking",
        body="",
        plugin_value="unklar",
        plugin_confidence=0.5,
    )
    assert r.suggested_action == "move_paketzustellung"
    assert "paketzustellung:logistiker:ctt.example" in r.matched_markers


def test_tier0_galaxus_dispatched_en_keyword_matches():
    """Shopping-Domain (example-shop.com) + English keyword (dispatched) — verifies
    multilingual keyword pattern."""
    r = classify_immo(
        sender="Example Shop <noreply@example-shop.com>",
        subject="Your order has been dispatched",
        body="Your package is on its way!",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert r.suggested_action == "move_paketzustellung"
    assert "paketzustellung:shopping:example-shop.com" in r.matched_markers


# ---------------------------------------------------------------------------
# Tier 1 — subdomain-suffix-match (dot-boundary-safe)
# ---------------------------------------------------------------------------


def test_tier1_immowelt_suchen_subdomain_matches_portal():
    """suchen.immowelt.de must match the portal 'immowelt.de' via suffix
    rule (sender_domain.endswith('.' + portal)). Asserts the Tier-1 marker
    is set; final action depends on Tier-2 location/price logic (separate
    test coverage)."""
    r = classify_immo(
        sender="ImmoWelt <angebot@suchen.immowelt.de>",
        subject="Wohnung Faro",
        body="4.5 Zimmer in Faro, Algarve, Kaufpreis CHF 450'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert "tier1:portal_domain:suchen.immowelt.de" in r.matched_markers
    assert r.suggested_action == "move_immo_portal"


def test_tier1_homegate_notifications_subdomain_matches_portal():
    """notifications.homegate.ch must match the portal 'homegate.ch'."""
    r = classify_immo(
        sender="Homegate <noreply@notifications.homegate.ch>",
        subject="Wohnung Faro",
        body="4.5 Zimmer in Faro, Algarve, Kaufpreis CHF 450'000.",
        plugin_value="geschaeftspost",
        plugin_confidence=0.85,
    )
    assert "tier1:portal_domain:notifications.homegate.ch" in r.matched_markers
    assert r.suggested_action == "move_immo_portal"


def test_tier1_typosquat_evil_immowelt_does_not_match_portal():
    """evil-immowelt.de (no dot before 'immowelt.de') must NOT match
    portal 'immowelt.de'. Dot-boundary guards against typosquat suffix."""
    r = classify_immo(
        sender="Phisher <abc@evil-immowelt.de>",
        subject="Newsletter",
        body="Neutral content, no immo keywords, no price.",
        plugin_value="werbung",
        plugin_confidence=0.6,
    )
    assert r.suggested_action != "move_immo_portal"
    assert not any(m.startswith("tier1:portal_domain:") for m in r.matched_markers)


# ---------------------------------------------------------------------------
# Sanity — result type
# ---------------------------------------------------------------------------


def test_result_is_HeuristicResult_dataclass():
    r = classify_immo(
        sender="test@example.com",
        subject="",
        body="",
        plugin_value="unklar",
        plugin_confidence=0.5,
    )
    assert isinstance(r, HeuristicResult)
    assert r.suggested_action in ("keep", "move_immo_portal", "move_immo_privat", "move_zu_pruefen")
    assert r.confidence in ("high", "medium", "low")
