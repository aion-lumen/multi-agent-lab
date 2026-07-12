"""council_state — single-source-of-truth read of whether Council is registered
for the active Folio vault.

MIRROR of folio/src/lib/server/env.ts `isCouncilRegistered()`. Both languages read
the SAME file (`~/.folio/active-vault.json`) and apply the SAME rule — there is NO
second flag and NO derived cache. A cross-language parity test locks this against
divergence. If you change the rule here, change env.ts in lockstep (and vice versa).

Rule (P0 immo/council-Move-Entkopplung, 2026-07-12):
  - demo vault            -> NOT registered (Council never runs on demo)
  - real vault, council:true  -> registered
  - real vault, council absent/other -> NOT registered   (Default AUS)
  - unreadable/missing file   -> NOT registered           (mirrors env.ts catch)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

def _active_vault_json_path() -> Path:
    """Resolved at CALL time (not import) so the path always reflects the current
    environment — mirrors env.ts reading the file fresh on every call and keeps
    tests hermetic regardless of import order (FOLIO_ACTIVE_VAULT_JSON override)."""
    return Path(
        os.environ.get("FOLIO_ACTIVE_VAULT_JSON", str(Path.home() / ".folio" / "active-vault.json"))
    )


def _is_demo_vault(meta: dict) -> bool:
    """Mirror of env.ts: demo === true OR path matches the demo-vault heuristic."""
    p = (meta.get("path") or "").strip() or None
    if meta.get("demo") is True:
        return True
    return p is not None and ("demo-vault" in p or "folio-demo" in p)


def council_registered() -> bool:
    """True iff Council is registered for the currently active vault.

    Reads ~/.folio/active-vault.json (override via FOLIO_ACTIVE_VAULT_JSON).
    """
    try:
        meta = json.loads(_active_vault_json_path().read_text())
    except Exception:
        return False
    if not isinstance(meta, dict):
        return False
    if _is_demo_vault(meta):
        return False
    return meta.get("council") is True
