#!/usr/bin/env python3
"""plz_lookup.py — DE+CH PLZ → city + coords lookup.

Datenquelle: `data/plz-de-ch.csv` (gebundelt, geonames.org, 5 Spalten:
country_code, postal_code, place_name, latitude, longitude). Eine Zeile pro
PLZ (dedupliziert, Business-Pattern wie 'GmbH'/'AG' im place_name beim
Build-Schritt rausgefiltert — siehe Build-Workflow in der Field-Note).

Lazy load: CSV wird beim ersten Call eingelesen in In-Memory-Dict {plz: rec}.
Subsequent calls = O(1) dict lookup.

(Direktive bugs-links-distanz Block 3, 2026-05-25)
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, TypedDict


REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "data" / "plz-de-ch.csv"


class PlzRecord(TypedDict):
    plz: str
    country: str  # 'DE' or 'CH'
    city: str
    lat: float
    lng: float


_cache: Optional[dict[str, PlzRecord]] = None


def _load() -> dict[str, PlzRecord]:
    global _cache
    if _cache is not None:
        return _cache
    if not CSV_PATH.exists():
        _cache = {}
        return _cache
    data: dict[str, PlzRecord] = {}
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 5:
                continue
            try:
                data[row[1]] = PlzRecord(
                    plz=row[1],
                    country=row[0],
                    city=row[2],
                    lat=float(row[3]),
                    lng=float(row[4]),
                )
            except (ValueError, IndexError):
                continue
    _cache = data
    return _cache


def lookup(plz: str) -> Optional[PlzRecord]:
    """Returnt PLZ-Record oder None wenn PLZ unbekannt."""
    return _load().get(str(plz))


def stats() -> dict[str, int]:
    """Diagnose: count by country."""
    data = _load()
    by_country: dict[str, int] = {}
    for rec in data.values():
        by_country[rec["country"]] = by_country.get(rec["country"], 0) + 1
    return {"total": len(data), **by_country}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(stats(), indent=2))
    for plz in sys.argv[1:]:
        print(plz, "→", json.dumps(lookup(plz)))
