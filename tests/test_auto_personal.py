"""auto_personal — auto-generated vs personal decision (P0.3–P0.5 move rule).

Focus incl. the Kontakt case: a personal mail (also of domain kontakt) must stay
in INBOX; an automated mail must be detected as such via multiple signals.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from auto_personal import (  # noqa: E402
    MailSignals, PersonalContext, classify_auto_personal, is_auto_generated,
)

CTX = PersonalContext(
    user_addresses=("mirhamed@yahoo.de",),
    personal_addressing_names=("Afschin", "Afshin"),
)


def _auto(sig: MailSignals) -> bool:
    return is_auto_generated(sig, CTX)


# ---- STRONG AUTO signals → move ---------------------------------------------
def test_auto_submitted_header():
    assert _auto(MailSignals(from_addr="x@shop.de", auto_submitted="auto-generated")) is True

def test_precedence_bulk():
    assert _auto(MailSignals(from_addr="x@shop.de", precedence="bulk")) is True

def test_list_unsubscribe_present():
    assert _auto(MailSignals(from_addr="hi@brand.de", list_unsubscribe="<mailto:u@brand.de>")) is True

def test_list_id_present():
    assert _auto(MailSignals(from_addr="x@list.de", list_id="<news.brand.de>")) is True

def test_noreply_sender_prefix():
    assert _auto(MailSignals(from_addr="noreply@immobilienscout24.de", subject="1 Angebot")) is True

def test_newsletter_sender_prefix():
    assert _auto(MailSignals(from_addr="newsletter@zalando.de")) is True


# ---- STRONG PERSONAL → stays in INBOX ---------------------------------------
def test_personal_to_user_with_salutation():
    sig = MailSignals(
        from_addr="freund@gmail.com", to_addr="mirhamed@yahoo.de",
        subject="Treffen?", body_excerpt="Hallo Afschin, wollen wir uns treffen?",
    )
    assert _auto(sig) is False

def test_personal_kontakt_stays_inbox():
    # domain would be 'kontakt' — the decision is domain-independent; personal → INBOX
    sig = MailSignals(
        from_addr="kollege@firma.de", to_addr="mirhamed@yahoo.de",
        subject="Rückfrage", body_excerpt="Lieber Afshin, kurze Frage zum Projekt.",
    )
    assert _auto(sig) is False

def test_formal_salutation_to_user():
    sig = MailSignals(
        from_addr="sachbearbeiter@amt.de", to_addr="mirhamed@yahoo.de",
        body_excerpt="Sehr geehrter Herr Mirhamed, anbei Ihr Schreiben.",
    )
    assert _auto(sig) is False


# ---- list/bulk beats a personalised salutation ------------------------------
def test_newsletter_with_greeting_is_still_auto():
    sig = MailSignals(
        from_addr="hallo@brand.de", to_addr="mirhamed@yahoo.de",
        subject="Deine Angebote", body_excerpt="Hallo Afschin, hier deine Deals!",
        list_unsubscribe="<mailto:u@brand.de>",
    )
    assert _auto(sig) is True


# ---- ambiguous → conservative: keep in INBOX (never move on a guess) ---------
def test_ambiguous_no_signal_keeps_inbox():
    sig = MailSignals(from_addr="person@firma.de", to_addr="mirhamed@yahoo.de",
                      subject="Angebot", body_excerpt="Anbei unser Angebot.")
    d = classify_auto_personal(sig, CTX)
    assert d.is_auto is False
    assert d.reasons == ["no-signal:keep-inbox"]

def test_salutation_but_not_to_user_needs_other_signal():
    # greeting present but addressed elsewhere + no auto header → conservative INBOX
    sig = MailSignals(from_addr="x@firma.de", to_addr="andere@example.com",
                      body_excerpt="Hallo Afschin")
    assert _auto(sig) is False
