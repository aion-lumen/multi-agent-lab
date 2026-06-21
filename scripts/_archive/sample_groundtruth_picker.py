#!/usr/bin/env python3
"""
sample_groundtruth_picker.py - Pick 3 mails per target_category bucket.

Reads pilot-sample-final.csv, groups by target_category, takes up to 3
random rows per bucket (seed=42 for reproducibility), writes
pilot-sample-groundtruth.csv with original columns plus 'gt_notes'.

User then fills 'ground_truth' (and optional 'gt_notes') manually.
"""
from __future__ import annotations

import csv
import logging
import random
from collections import defaultdict
from pathlib import Path

STATE = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"
INPUT = STATE / "pilot-sample-final.csv"
OUTPUT = STATE / "pilot-sample-groundtruth.csv"

PER_BUCKET = 3
SEED = 42

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gt-picker")


def main() -> None:
    if not INPUT.exists():
        raise SystemExit(f"Input not found: {INPUT}")

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    with open(INPUT) as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            by_bucket[row.get("target_category", "unknown")].append(row)

    rng = random.Random(SEED)
    selected: list[dict] = []
    log.info("Bucket-Auswahl (seed=%d):", SEED)
    for cat in sorted(by_bucket.keys()):
        rows = list(by_bucket[cat])
        rng.shuffle(rows)
        picks = rows[:PER_BUCKET]
        log.info("  %-22s %d ausgewaehlt (von %d verfuegbar)",
                 cat, len(picks), len(rows))
        selected.extend(picks)
    log.info("Total ausgewaehlt: %d", len(selected))

    out_fields = list(fieldnames)
    if "ground_truth" not in out_fields:
        out_fields.append("ground_truth")
    if "gt_notes" not in out_fields:
        out_fields.append("gt_notes")

    with open(OUTPUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        for r in selected:
            r2 = dict(r)
            r2.setdefault("ground_truth", "")
            r2.setdefault("gt_notes", "")
            w.writerow(r2)
    log.info("Geschrieben: %s (%d rows)", OUTPUT, len(selected))


if __name__ == "__main__":
    main()
