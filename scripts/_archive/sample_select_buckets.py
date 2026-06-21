#!/usr/bin/env python3
"""
sample_select_buckets.py - Select stratified 100-mail sample.

Reads pilot-sample-candidates.csv (output of sample_curator.py) and
selects 100 mails with quota:
  30 werbung
  30 geschaeftspost
  20 newsletter_business
  15 privat
  5 edge-cases (long, multilingual, or spam)

Strategy:
- Group by `suggested_category`
- Random-sample with seed=42 for reproducibility
- 'edge' bucket: mails with length >5000, language=unknown, or category=spam

Output: pilot-sample-final.csv with same columns plus:
- target_category (= bucket label)
- ground_truth (empty, user fills 10-20 entries)
"""
from __future__ import annotations

import csv
import logging
import random
from collections import defaultdict
from pathlib import Path

STATE_DIR = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"
INPUT = STATE_DIR / "pilot-sample-candidates.csv"
OUTPUT = STATE_DIR / "pilot-sample-final.csv"

QUOTAS = {
    "werbung": 30,
    "geschaeftspost": 30,
    "newsletter_business": 20,
    "privat": 15,
    "edge": 5,
}
SEED = 42

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bucket")


def is_edge(row: dict) -> bool:
    try:
        length = int(row.get("length_chars") or 0)
    except ValueError:
        length = 0
    if length > 5000:
        return True
    if row.get("language") == "unknown":
        return True
    if row.get("suggested_category") == "spam":
        return True
    return False


def main() -> None:
    if not INPUT.exists():
        raise SystemExit(f"Input not found: {INPUT}")

    with open(INPUT) as f:
        rows = list(csv.DictReader(f))
    log.info("Loaded %d candidate rows", len(rows))

    rng = random.Random(SEED)
    rng.shuffle(rows)

    # Bucket assignment - prefer edge-bucket first, then category
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        cat = r.get("suggested_category", "")
        if is_edge(r) and cat != "geschaeftspost":
            by_bucket["edge"].append(r)
        elif cat in ("werbung", "geschaeftspost",
                     "newsletter_business", "privat"):
            by_bucket[cat].append(r)
        # other categories ('unklar', 'error', 'spam' that didn't fall to edge)
        # are skipped

    selected: list[dict] = []
    actual_counts: dict[str, int] = {}
    deficit = 0
    used_uids: set[str] = set()
    for bucket, quota in QUOTAS.items():
        avail = [r for r in by_bucket.get(bucket, []) if r["uid"] not in used_uids]
        take = avail[:quota]
        actual_counts[bucket] = len(take)
        for r in take:
            r2 = dict(r)
            r2["target_category"] = bucket
            r2["ground_truth"] = ""
            selected.append(r2)
            used_uids.add(r["uid"])
        if len(take) < quota:
            shortfall = quota - len(take)
            deficit += shortfall
            log.warning("Bucket %r short: %d/%d available - deficit +%d",
                        bucket, len(take), quota, shortfall)

    # Backfill deficit from newsletter_business (typically abundant)
    if deficit:
        backfill = [r for r in by_bucket.get("newsletter_business", [])
                    if r["uid"] not in used_uids][:deficit]
        for r in backfill:
            r2 = dict(r)
            r2["target_category"] = "newsletter_business"
            r2["ground_truth"] = ""
            selected.append(r2)
            used_uids.add(r["uid"])
        actual_counts["newsletter_business"] = (
            actual_counts.get("newsletter_business", 0) + len(backfill))
        log.info("Backfilled %d from newsletter_business", len(backfill))

    log.info("Bucket counts (actual / quota):")
    for b in QUOTAS:
        log.info("  %-20s %d / %d", b, actual_counts.get(b, 0), QUOTAS[b])
    log.info("Total selected: %d", len(selected))

    if not selected:
        raise SystemExit("No rows selected - check input quality")

    fieldnames = list(selected[0].keys())
    with open(OUTPUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(selected)
    log.info("Wrote %s (%d rows)", OUTPUT, len(selected))


if __name__ == "__main__":
    main()
