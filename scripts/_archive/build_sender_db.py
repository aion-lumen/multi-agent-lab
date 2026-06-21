#!/usr/bin/env python3
"""
build_sender_db.py - Top-30 Yahoo-sender classification (deterministic).

1. Read accounts.toml from life-mail
2. Open Yahoo IMAPSession, fetch last 500 envelopes (Phase-3 pattern)
3. Aggregate sender (`from_addr`/`from_name`) into Counter
4. Sort by frequency; deterministic tie-break via random.Random(42)
5. Take Top-30
6. Per sender: life-agent (Ollama gemma3:4b) classifies as
   private_person | service_platform | marketing | unclear
7. Write state/sender-heuristics.json

Constraints:
- Read-only on Yahoo IMAP
- No life-mail code modifications
- Deterministic seed=42
- No user pause / no manual corrections
"""
from __future__ import annotations

import json
import logging
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

LIFE_MAIL = Path.home() / "Projects" / "life-mail"
sys.path.insert(0, str(LIFE_MAIL / "scripts"))
from mail_fetcher import IMAPSession  # noqa: E402

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

ACCOUNTS_TOML = LIFE_MAIL / "accounts.toml"
ACCOUNT = "yahoo"
N_LAST = 500
TOP_N = 30
SEED = 42

STATE = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"
OUT = STATE / "sender-heuristics.json"

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODEL = "life-agent:latest"

PROMPT = """Du klassifizierst Email-Sender-Adressen. Antworte mit GENAU EINEM Wort:
- private_person: natuerliche Person (Vorname Nachname @ Domain) ODER Privat-Email
- service_platform: Service-Plattform, Online-Shop, App-Anbieter, no-reply@, info@, system@
- marketing: Marketing/Newsletter-Domain (deals@, newsletter@, promo@)
- unclear: nicht eindeutig

Sender: {sender}
Haeufigkeit: {count}

Antwort (nur ein Wort):"""

VALID = ("private_person", "service_platform", "marketing", "unclear")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("build-sender")


def load_account(name: str) -> dict:
    with open(ACCOUNTS_TOML, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["accounts"][name]


def normalize_sender(name: str, addr: str) -> str:
    """Stable representation 'Name <addr>' or just 'addr' (lowercased addr)."""
    addr = (addr or "").strip().lower()
    name = (name or "").strip()
    if name and addr:
        return f"{name} <{addr}>"
    return addr or name


def classify_sender(sender: str, count: int) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user",
                      "content": PROMPT.format(sender=sender, count=count)}],
        "max_tokens": 15,
        "temperature": 0.0,
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip().lower()
    except Exception as e:
        log.warning("Ollama error for %r: %s", sender[:60], e)
        return "unclear"
    for w in re.findall(r"[a-z_]+", text):
        if w in VALID:
            return w
    return "unclear"


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    acct = load_account(ACCOUNT)
    log.info("Account: %s @ %s", acct["login"], acct["host"])

    counter: Counter[str] = Counter()
    with IMAPSession(host=acct["host"], port=acct["port"],
                     login=acct["login"], bw_item=acct["bw_item"]) as session:
        total, _ = session.select_folder("INBOX")
        log.info("INBOX: %d messages", total)
        all_uids = session.search_uids(since_uid=0, skip_classified=False)
        recent = all_uids[-N_LAST:] if len(all_uids) > N_LAST else all_uids
        log.info("Counting senders in last %d", len(recent))
        for env in session.fetch_envelopes(recent):
            sender = normalize_sender(env.from_name, env.from_addr)
            if sender:
                counter[sender] += 1
    log.info("Distinct senders: %d", len(counter))

    # Deterministic top-30: sort by (count DESC, sender ASC) + seed-based tie-break
    rng = random.Random(SEED)
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    # If many ties at the boundary, randomise within equal-count groups deterministically.
    grouped: dict[int, list[str]] = {}
    for s, c in items:
        grouped.setdefault(c, []).append(s)
    ordered: list[tuple[str, int]] = []
    for c in sorted(grouped.keys(), reverse=True):
        group = list(grouped[c])
        rng.shuffle(group)
        for s in group:
            ordered.append((s, c))
    top = ordered[:TOP_N]
    log.info("Top-%d ausgewaehlt (seed=%d)", len(top), SEED)

    classifications: dict[str, list[dict]] = {
        "private_senders": [], "service_senders": [],
        "marketing_senders": [], "unclear_senders": [],
    }
    bucket_for = {
        "private_person": "private_senders",
        "service_platform": "service_senders",
        "marketing": "marketing_senders",
        "unclear": "unclear_senders",
    }

    t0 = time.time()
    for i, (sender, count) in enumerate(top, 1):
        kind = classify_sender(sender, count)
        classifications[bucket_for[kind]].append({"sender": sender,
                                                  "count": count, "kind": kind})
        log.info("[%2d/%d] %s -> %s", i, len(top), sender[:60], kind)

    classifications["metadata"] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "account": ACCOUNT,
        "total_distinct_senders": len(counter),
        "top_n_classified": len(top),
        "seed": SEED,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    OUT.write_text(json.dumps(classifications, indent=2, ensure_ascii=False))

    print()
    print("Top-30 Sender-Klassifikation (seed=42):")
    for k in ("private_senders", "service_senders",
              "marketing_senders", "unclear_senders"):
        print(f"  {k:<22} {len(classifications[k])}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
