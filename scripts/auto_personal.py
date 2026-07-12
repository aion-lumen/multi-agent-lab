"""auto_personal — decide whether a mail is AUTO-GENERATED (→ silent move to its
domain folder) or PERSONAL (→ stays in INBOX, never moved).

P0.3–P0.5 Yahoo-Struktur-Move (2026-07-12). The central move rule:
  - auto-generated  → silent into the domain folder (kept UNREAD, visible + in
    the daily report — a misclassified personal mail is never silently lost)
  - personal (directly addressed to the user, not automated) → INBOX, no move,
    domain-independent (a personal mail of domain `kontakt` also stays)

Detection uses MULTIPLE signals, not just noreply (directive P0.4c names them):
  STRONG AUTO   : Auto-Submitted (RFC 3834, any value except "no"),
                  Precedence: bulk|list|junk, List-Unsubscribe / List-Id present
                  (RFC 2076/8058 — the mail belongs to a list/bulk stream),
                  bulk/marketing sender local-part (noreply, newsletter, …).
  STRONG PERSONAL: To contains the user's own address AND a personal salutation
                  ("Hallo <name>", "Lieber <name>", "Sehr geehrter Herr <name>")
                  AND none of the strong-auto list/bulk signals.

Decision (conservative — only MOVE when confident it is auto):
  personal-signal present            → personal (INBOX)
  else any strong-auto signal        → auto (move)
  else (no clear signal)             → personal (INBOX)   # never move on a guess

Pure/stateless: all inputs are passed in (headers + config), so it is fully unit
-testable offline without IMAP or a DB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Sender local-part prefixes that mark an automated/bulk/service sender (exact or
# startswith). Mirrors BULK_SENDER_PREFIXES in domain_actionability.py.
_AUTO_SENDER_PREFIXES = (
    "notifications", "notification", "news", "newsletter", "newsletters",
    "marketing", "deals", "promo", "promotions", "updates", "mailer",
    "postmaster", "bounce", "bounces",
    # P0.4c (2026-07-12): transactional auto-senders that carry NO List-Unsubscribe
    # (order/shipping confirmations, listing scouts). Narrow, transaction-only
    # patterns — deliberately NOT "service"/"info" (could hit a 1:1 support reply).
    "versandbestaetigung", "bestellbestaetigung", "order-update",
    "shipment-tracking", "myscout", "jobs-listings",
)

# Substrings that mark an automated sender even mid-local-part (e.g.
# jobalerts-noreply, messages-noreply). Safe: no personal local-part contains these.
_AUTO_SENDER_SUBSTRINGS = ("noreply", "no-reply", "donotreply", "do-not-reply", "no_reply")

_SALUTATION_BASE = (
    r"sehr\s+geehrte[r]?\s+(?:herr|frau)",
    r"guten\s+(?:morgen|tag|abend)",
)


@dataclass
class MailSignals:
    """Everything the decision needs. Empty string = header absent."""
    from_addr: str = ""
    to_addr: str = ""
    subject: str = ""
    body_excerpt: str = ""
    list_id: str = ""
    auto_submitted: str = ""
    precedence: str = ""
    list_unsubscribe: str = ""


@dataclass
class PersonalContext:
    """User identity for the personal-address check."""
    user_addresses: tuple[str, ...] = ()          # e.g. ("mirhamed@yahoo.de",)
    personal_addressing_names: tuple[str, ...] = ()  # e.g. ("Afschin", "Afshin")


def _local_part(addr: str) -> str:
    return (addr or "").strip().lower().split("@", 1)[0]


def _has_auto_header(sig: MailSignals) -> list[str]:
    reasons: list[str] = []
    if sig.auto_submitted and sig.auto_submitted.strip().lower() != "no":
        reasons.append(f"auto-submitted:{sig.auto_submitted.strip().lower()}")
    if sig.precedence and sig.precedence.strip().lower() in {"bulk", "list", "junk"}:
        reasons.append(f"precedence:{sig.precedence.strip().lower()}")
    if sig.list_unsubscribe.strip():
        reasons.append("list-unsubscribe")
    if sig.list_id.strip():
        reasons.append("list-id")
    lp = _local_part(sig.from_addr)
    if lp and (any(lp == p or lp.startswith(p) for p in _AUTO_SENDER_PREFIXES)
               or any(s in lp for s in _AUTO_SENDER_SUBSTRINGS)):
        reasons.append(f"sender-prefix:{lp}")
    return reasons


def _salutation_re(ctx: PersonalContext) -> re.Pattern[str]:
    parts = list(_SALUTATION_BASE)
    for name in ctx.personal_addressing_names:
        n = re.escape(name.strip())
        if not n:
            continue
        parts.append(rf"hallo\s+{n}")
        parts.append(rf"liebe[r]?\s+{n}")
        parts.append(rf"hi\s+{n}")
    return re.compile("|".join(parts), re.IGNORECASE)


def _is_personal(sig: MailSignals, ctx: PersonalContext, auto_reasons: list[str]) -> list[str]:
    """Strong-personal check. Returns reasons if personal, else []."""
    # List/bulk headers beat a salutation (marketing personalisation is still auto).
    list_bulk = {r for r in auto_reasons if r.startswith(("list-", "precedence:", "auto-submitted:"))}
    if list_bulk:
        return []
    to = (sig.to_addr or "").strip().lower()
    to_is_user = any(u.strip().lower() in to for u in ctx.user_addresses if u.strip())
    if not to_is_user:
        return []
    hay = f"{sig.subject}\n{sig.body_excerpt}"
    if _salutation_re(ctx).search(hay):
        return ["to-user+salutation"]
    return []


@dataclass
class AutoDecision:
    is_auto: bool
    reasons: list[str] = field(default_factory=list)


def classify_auto_personal(sig: MailSignals, ctx: PersonalContext) -> AutoDecision:
    """True(is_auto) ⇒ move to domain folder; False ⇒ keep in INBOX."""
    auto_reasons = _has_auto_header(sig)
    personal = _is_personal(sig, ctx, auto_reasons)
    if personal:
        return AutoDecision(False, ["personal:" + personal[0]])
    if auto_reasons:
        return AutoDecision(True, auto_reasons)
    return AutoDecision(False, ["no-signal:keep-inbox"])  # conservative: never move on a guess


def is_auto_generated(sig: MailSignals, ctx: PersonalContext) -> bool:
    return classify_auto_personal(sig, ctx).is_auto
