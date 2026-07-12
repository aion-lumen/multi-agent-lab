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


# ---- P0.4c: transactional auto-senders WITHOUT List-Unsubscribe --------------
@pytest.mark.parametrize("addr", [
    "versandbestaetigung@amazon.de", "bestellbestaetigung@amazon.de",
    "order-update@amazon.de", "shipment-tracking@amazon.de",
    "myscout@immobilienscout24.de", "jobs-listings@linkedin.com",
])
def test_transactional_prefix_is_auto(addr):
    # no List-Unsubscribe header, but a transactional sender local-part → auto
    assert _auto(MailSignals(from_addr=addr, subject="Bestellt / Angebot")) is True


@pytest.mark.parametrize("addr", [
    "jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com",
    "messages-noreply@example.com",
])
def test_noreply_substring_is_auto(addr):
    # "noreply" mid-local-part (not a prefix) → still auto
    assert _auto(MailSignals(from_addr=addr, subject="x")) is True


@pytest.mark.parametrize("addr", [
    "samuel.grauer@remax.de", "joerg.thalmann@remax.de",
    "adriana.russotto@century21.de", "gabriele.hofmann@century21.de",
])
def test_agent_replies_stay_personal(addr):
    # HARD criterion (Afschin): real 1:1 agent replies (personal-name local-part,
    # no auto header, not addressed-with-salutation) must NOT be auto → keep INBOX.
    sig = MailSignals(from_addr=addr, to_addr="mirhamed@yahoo.de",
                      subject="Re: Ihre Anfrage - Exposé")
    assert _auto(sig) is False


def test_service_prefix_NOT_auto():
    # deliberately excluded: 'service' could be a 1:1 support reply → conservative
    assert _auto(MailSignals(from_addr="service@paypal.de", subject="x")) is False
