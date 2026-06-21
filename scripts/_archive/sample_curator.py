#!/usr/bin/env python3
"""
sample_curator.py - Pre-classify last ~500 Yahoo mails for pilot sampling.

1. Read accounts.toml from life-mail repo
2. Open Yahoo IMAPSession (life-mail's mail_fetcher)
3. Fetch last N envelopes from INBOX
4. For each: ask life-agent (Ollama) for category suggestion
5. Write CSV with: uid, sender, subject, date, body_excerpt,
   length_chars, language, suggested_category

Output: state/pilot-sample-candidates.csv

Constraints:
- Read-only on Yahoo IMAP (no flag changes)
- No Hermes interaction
- Resilient to per-mail Ollama errors (mark category=error, continue)
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests

LIFE_MAIL = Path.home() / "Projects" / "life-mail"
sys.path.insert(0, str(LIFE_MAIL / "scripts"))
from mail_fetcher import IMAPSession  # noqa: E402

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]


STATE_DIR = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"
OUT_CSV = STATE_DIR / "pilot-sample-candidates.csv"
ACCOUNTS_TOML = LIFE_MAIL / "accounts.toml"
ACCOUNT = "yahoo"
N_LAST = 500

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODEL = "life-agent:latest"
PROMPT_CLASSIFY = """Klassifiziere diese Email in EINE Kategorie. Antworte NUR mit dem Kategorie-Wort, nichts sonst.

Kategorien:
werbung | newsletter_business | geschaeftspost | privat | spam | unklar

Sender: {sender}
Subject: {subject}
Erste 200 Zeichen Body:
{excerpt}

Kategorie:"""

VALID_CATEGORIES = {"werbung", "newsletter_business", "geschaeftspost",
                    "privat", "spam", "unklar"}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("curator")


def detect_language(text: str) -> str:
    """Best-effort lang heuristic; not load-bearing."""
    t = text.lower()
    de_markers = (" der ", " die ", " und ", " ist ", " nicht ", "ihr ", " für ")
    en_markers = (" the ", " and ", " is ", " not ", " your ", " for ")
    fr_markers = (" le ", " la ", " et ", " est ", " votre ", " pour ")
    de = sum(m in t for m in de_markers)
    en = sum(m in t for m in en_markers)
    fr = sum(m in t for m in fr_markers)
    if not (de or en or fr):
        return "unknown"
    return max(("de", de), ("en", en), ("fr", fr), key=lambda kv: kv[1])[0]


def classify_quick(sender: str, subject: str, excerpt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user",
                      "content": PROMPT_CLASSIFY.format(
                          sender=sender, subject=subject,
                          excerpt=excerpt[:200])}],
        "max_tokens": 20,
        "temperature": 0.1,
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip().lower()
    except Exception as e:
        log.warning("Ollama error: %s", e)
        return "error"
    # Take first matching token
    for word in re.findall(r"[a-zaeoeueszA-Z_]+", text):
        if word in VALID_CATEGORIES:
            return word
    return "unklar"


def load_account(name: str) -> dict:
    with open(ACCOUNTS_TOML, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["accounts"][name]


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    acct = load_account(ACCOUNT)
    log.info("Account: %s @ %s", acct["login"], acct["host"])

    with IMAPSession(host=acct["host"], port=acct["port"],
                     login=acct["login"], bw_item=acct["bw_item"]) as session:
        total, _ = session.select_folder("INBOX")
        log.info("INBOX: %d messages total", total)
        all_uids = session.search_uids(since_uid=0, skip_classified=False)
        log.info("Got %d UIDs", len(all_uids))
        recent = all_uids[-N_LAST:] if len(all_uids) > N_LAST else all_uids
        log.info("Fetching last %d envelopes", len(recent))

        rows = []
        n_fetched = 0
        n_classified = 0
        t0 = time.time()
        for env in session.fetch_envelopes(recent):
            n_fetched += 1
            sender = f"{env.from_name} <{env.from_addr}>".strip()
            body = env.body_text or ""
            excerpt = body[:200].replace("\n", " ").strip()
            cat = classify_quick(sender, env.subject, excerpt)
            n_classified += 1
            lang = detect_language(body)
            rows.append({
                "uid": env.uid,
                "sender": sender[:120],
                "subject": (env.subject or "")[:120],
                "date": env.date or "",
                "body_excerpt": body[:300].replace("\n", " ").strip(),
                "length_chars": len(body),
                "language": lang,
                "suggested_category": cat,
            })
            if n_classified % 25 == 0:
                elapsed = time.time() - t0
                rate = n_classified / max(elapsed, 1e-6)
                log.info("Progress: %d/%d classified (%.1f mail/s)",
                         n_classified, len(recent), rate)

    log.info("Fetched %d, classified %d in %.1fs", n_fetched, n_classified,
             time.time() - t0)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows
                                else ["uid", "sender", "subject", "date",
                                      "body_excerpt", "length_chars",
                                      "language", "suggested_category"])
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote CSV: %s (%d rows)", OUT_CSV, len(rows))


if __name__ == "__main__":
    main()
