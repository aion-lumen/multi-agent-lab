#!/usr/bin/env python3
"""lead_heuristic.py — deterministic field extraction for freelance/job leads.

Extracts structured fields (rolle, quelle, deadline, satz, ort, link) from a
lead mail so the emitter can build interchange frontmatter WITHOUT any LLM.
Regex-only, tolerant: missing fields yield "" (deadline → None). Never raises.

Mirrors the regex-helper style of immo_heuristic.py (word-boundary anchors,
longest-first alternation). No network, no side effects.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

# --- Field patterns (best-effort; tune once real portal formats are known) ---

# Rate/Satz: "80 €/h", "800 EUR / Tag", "Tagessatz: 700", "Stundensatz 95 CHF"
_SATZ_RE = re.compile(
    r"(?:(?:tages|stunden)satz\s*[:\-]?\s*)?"
    r"(?P<amount>\d{1,4}(?:[.,]\d{1,2})?)\s*"
    r"(?P<curr>€|eur|chf|fr\.?)\s*"
    r"(?:/\s*|\s+pro\s+)?(?P<unit>h|std|stunde|tag|day|hour)?",
    re.IGNORECASE,
)

# Deadline: "bis 15.08.2026", "Bewerbungsfrist 15.08.2026", "Deadline: 2026-08-15",
# "Frist 15. August 2026" (numeric forms only — month-name parsing kept simple).
_DEADLINE_RE = re.compile(
    r"(?:bewerbungs?frist|deadline|frist|bis(?:\s+zum)?)\s*[:\-]?\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4})",
    re.IGNORECASE,
)

# Location/Ort: "Ort: Zürich", "Standort München", "Remote", "vor Ort in Berlin".
_ORT_RE = re.compile(
    r"(?:ort|standort|location|einsatzort)\s*[:\-]?\s*(?P<ort>[A-Za-zÄÖÜäöüß .\-]{2,40})",
    re.IGNORECASE,
)
_REMOTE_RE = re.compile(r"\b(remote|homeoffice|100\s*%\s*remote|full\s*remote)\b", re.IGNORECASE)

# Role/Rolle: "Projekt: Senior Python Developer", "Rolle: DevOps Engineer",
# "Position als Data Engineer".
_ROLLE_RE = re.compile(
    r"(?:projekt|rolle|position|gesucht|stelle)\b\s*(?:als|:|\-)\s*"
    r"(?P<rolle>[A-Za-zÄÖÜäöüß()/+#. \-]{3,60})",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s<>\")]+", re.IGNORECASE)

_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}


@dataclass
class LeadExtract:
    rolle: str = ""
    quelle: str = ""
    deadline: Optional[str] = None  # ISO yyyy-mm-dd or None
    satz: str = ""
    ort: str = ""
    link: str = ""
    dedup_key: str = ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" \t\r\n.,;:-")


def _normalize_date(raw: str) -> Optional[str]:
    """Parse dd.mm.yyyy / dd.mm.yy / yyyy-mm-dd → ISO yyyy-mm-dd. None on failure."""
    raw = raw.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return raw
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _extract_deadline(text: str) -> Optional[str]:
    m = _DEADLINE_RE.search(text)
    if m:
        iso = _normalize_date(m.group("date"))
        if iso:
            return iso
    # Month-name form: "15. August 2026"
    m2 = re.search(
        r"(\d{1,2})\.?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", text, re.IGNORECASE
    )
    if m2:
        d, mo, y = int(m2.group(1)), _MONTHS[m2.group(2).lower()], int(m2.group(3))
        if 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _extract_satz(text: str) -> str:
    m = _SATZ_RE.search(text)
    if not m:
        return ""
    curr = m.group("curr").upper().rstrip(".")
    curr = {"€": "EUR", "FR": "CHF"}.get(curr, curr)
    unit = (m.group("unit") or "").lower()
    unit_norm = {"std": "h", "stunde": "h", "hour": "h", "day": "Tag", "tag": "Tag"}.get(unit, unit)
    amount = m.group("amount")
    return f"{amount} {curr}" + (f"/{unit_norm}" if unit_norm else "")


def _extract_ort(text: str) -> str:
    if _REMOTE_RE.search(text):
        return "Remote"
    m = _ORT_RE.search(text)
    return _clean(m.group("ort")) if m else ""


def _extract_rolle(subject: str, body: str) -> str:
    for src in (subject, body):
        m = _ROLLE_RE.search(src or "")
        if m:
            r = _clean(m.group("rolle"))
            if r:
                return r
    # Fallback: subject minus a leading "Projekt/Lead/…:" prefix.
    subj = _clean(re.sub(r"^\s*(re|fwd|aw)\s*:", "", subject or "", flags=re.IGNORECASE))
    subj = _clean(re.sub(r"^[^:]{0,30}:\s*", "", subj))
    return subj[:60]


def _quelle_from_sender(sender: str) -> str:
    m = re.search(r"@([\w.\-]+)", sender or "")
    if not m:
        return _clean(sender)[:40]
    domain = m.group(1).lower()
    # Portal name = registrable label (e.g. freelancermap.de → freelancermap).
    label = domain.split(".")[0] if "." in domain else domain
    return label or domain


def compute_dedup_key(rolle: str, ort: str, satz: str) -> str:
    """Deterministic, cross-portal: same role+location+rate → same key."""
    norm = "|".join(re.sub(r"\s+", " ", (x or "").strip().lower()) for x in (rolle, ort, satz))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def extract_lead(sender: str, subject: str, body: str) -> LeadExtract:
    """Extract lead fields from a mail. Tolerant — never raises."""
    sender = sender or ""
    subject = subject or ""
    body = body or ""
    text = f"{subject}\n{body}"

    rolle = _extract_rolle(subject, body)
    quelle = _quelle_from_sender(sender)
    deadline = _extract_deadline(text)
    satz = _extract_satz(text)
    ort = _extract_ort(text)
    link_m = _URL_RE.search(body)
    link = link_m.group(0) if link_m else ""

    return LeadExtract(
        rolle=rolle,
        quelle=quelle,
        deadline=deadline,
        satz=satz,
        ort=ort,
        link=link,
        dedup_key=compute_dedup_key(rolle, ort, satz),
    )
