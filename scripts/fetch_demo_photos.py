"""fetch_demo_photos.py — download CC0 placeholder photos for the 6 demo
council objects from picsum.photos (deterministic seeds → reproducible).

Replaces the bundled SVG placeholders in folio/static/demo-assets/photos/
with actual photography. Run once after `make demo` to upgrade visuals.

The seeds give random real photos (landscapes/buildings/abstract — not
guaranteed house-specific, but real). If you have curated CC0 villa photos,
drop them at the same filenames manually — this script won't overwrite if
a JPEG already exists.

Idempotent: skips if all six JPEGs exist. Run with --force to re-download.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLIO_PHOTOS_DIR = REPO_ROOT.parent.parent / "folio" / "static" / "demo-assets" / "photos"

# Seed → filename mapping. Seeds chosen for variety, deterministic.
PHOTOS = [
    ("algarve-villa-coastal", "faro-t3.jpg"),
    ("algarve-villa-inland", "loule-moradia.jpg"),
    ("algarve-pool-terrace", "tavira-t3.jpg"),
    ("algarve-historic-village", "olhao-cluster.jpg"),
    ("algarve-luxury-villa", "almancil-villa.jpg"),
    ("algarve-modern-apartment", "vilamoura-expired.jpg"),
]

PICSUM_TEMPLATE = "https://picsum.photos/seed/{seed}/800/600"


def fetch_one(seed: str, filename: str, dest_dir: Path, force: bool = False) -> bool:
    dest = dest_dir / filename
    if dest.exists() and not force:
        print(f"  {filename}: already exists, skipping")
        return False
    url = PICSUM_TEMPLATE.format(seed=seed)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "folio-demo-seed/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            # picsum redirects to actual photo URL — follow happens automatically
            data = resp.read()
        dest.write_bytes(data)
        size_kb = len(data) // 1024
        print(f"  {filename}: {size_kb} KB ({len(data)} bytes, seed={seed})")
        return True
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  {filename}: FAILED ({e}) — keeping existing SVG fallback")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if JPEG already exists")
    ap.add_argument("--photos-dir", default=str(FOLIO_PHOTOS_DIR),
                    help="Output directory (default: folio/static/demo-assets/photos/)")
    args = ap.parse_args()

    dest_dir = Path(args.photos_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading 6 demo photos to {dest_dir}…")
    n_new = 0
    for seed, filename in PHOTOS:
        if fetch_one(seed, filename, dest_dir, force=args.force):
            n_new += 1

    print()
    print(f"Done. {n_new} new JPEGs downloaded.")
    print(f"To use these in the demo: re-run `make demo-force` after updating")
    print(f"seed_council_demo.py's photo_url to '/demo-assets/photos/<id>.jpg'")
    print(f"(or keep SVG fallback URLs if the JPEGs aren't suitable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
