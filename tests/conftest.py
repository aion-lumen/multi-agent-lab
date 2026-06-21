"""pytest conftest — put scripts/ on sys.path so `import immo_heuristic` works."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
TESTS = REPO_ROOT / "tests"

for p in (REPO_ROOT, SCRIPTS, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
