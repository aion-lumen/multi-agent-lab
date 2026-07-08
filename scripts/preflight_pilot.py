#!/usr/bin/env python3
"""preflight_pilot.py — Install-Preflight für Pilot/Fremdrechner.

Prüft: Modell-Host, IMAP-Login (optional), Schreibrechte state/, Configs.
Klarer Exit-Report (0 = OK, 1 = Fehler).

Usage:
    python scripts/preflight_pilot.py
    python scripts/preflight_pilot.py --account mirhamed --skip-imap
    python scripts/preflight_pilot.py --categories config/categories.pilot-praxis.yaml
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import (  # noqa: E402
    ACCOUNTS_TOML,
    CATEGORIES_YAML,
    CONFIG_DIR,
    FEEDBACK_DB,
    REGELWERK_YAML,
    STATE_DIR,
    USER_CONTEXT_YAML,
)


def _check(name: str, ok: bool, detail: str = "") -> tuple[bool, str]:
    status = "OK" if ok else "FAIL"
    msg = f"  [{status}] {name}"
    if detail:
        msg += f" — {detail}"
    return ok, msg


def main() -> int:
    ap = argparse.ArgumentParser(prog="preflight_pilot")
    ap.add_argument("--account", default=None, help="accounts.toml key for IMAP probe")
    ap.add_argument("--skip-imap", action="store_true", help="skip live IMAP login test")
    ap.add_argument("--categories", default=None, help="categories yaml path override")
    args = ap.parse_args()

    lines: list[str] = ["═══ Pilot Preflight ═══"]
    all_ok = True

    # Config files
    cats_path = Path(args.categories) if args.categories else CATEGORIES_YAML
    for label, path in (
        ("categories.yaml", cats_path),
        ("regelwerk.yaml", REGELWERK_YAML),
        ("user_context.yaml", USER_CONTEXT_YAML),
    ):
        ok, msg = _check(label, path.exists(), str(path))
        lines.append(msg)
        all_ok &= ok

    if cats_path.exists():
        try:
            from categories_loader import load_categories  # noqa: PLC0415
            cfg = load_categories(cats_path)
            ok, msg = _check(
                "categories parse",
                len(cfg.categories) > 0,
                f"{len(cfg.categories)} categories, fallback={cfg.fallback_domain}",
            )
            lines.append(msg)
            all_ok &= ok
        except Exception as e:  # noqa: BLE001
            ok, msg = _check("categories parse", False, str(e))
            lines.append(msg)
            all_ok = False

    # State dir writable
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        probe = STATE_DIR / ".preflight_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        ok, msg = _check("state/ writable", True, str(STATE_DIR))
    except OSError as e:
        ok, msg = _check("state/ writable", False, str(e))
        all_ok = False
    lines.append(msg)

    # feedback.db parent
    try:
        FEEDBACK_DB.parent.mkdir(parents=True, exist_ok=True)
        ok, msg = _check("feedback.db path", True, str(FEEDBACK_DB))
    except OSError as e:
        ok, msg = _check("feedback.db path", False, str(e))
        all_ok = False
    lines.append(msg)

    # LM Studio
    base = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234")
    try:
        resp = requests.get(f"{base}/v1/models", timeout=8)
        if resp.status_code == 200:
            models = [m.get("id") for m in (resp.json().get("data") or []) if m.get("id")]
            ok, msg = _check("LM Studio", True, f"{base} — {len(models)} model(s) loaded")
        else:
            ok, msg = _check("LM Studio", False, f"HTTP {resp.status_code}")
            all_ok = False
    except requests.RequestException as e:
        ok, msg = _check("LM Studio", False, f"{base}: {e}")
        all_ok = False
    lines.append(msg)

    # accounts.toml
    if ACCOUNTS_TOML.exists():
        ok, msg = _check("accounts.toml", True, str(ACCOUNTS_TOML))
    else:
        ok, msg = _check("accounts.toml", False, f"missing at {ACCOUNTS_TOML}")
        all_ok = False
    lines.append(msg)

    # IMAP probe
    if not args.skip_imap and args.account:
        if not ACCOUNTS_TOML.exists():
            ok, msg = _check("IMAP login", False, "accounts.toml missing")
            all_ok = False
        else:
            try:
                import tomllib
                from production_worker import _load_account, _open_imap_session  # noqa: PLC0415

                ns = argparse.Namespace(
                    account=args.account,
                    imap_fixture=None,
                )
                _load_account(args.account)
                with _open_imap_session(ns) as session:
                    total, _ = session.select_folder("INBOX")
                ok, msg = _check("IMAP login", True, f"account={args.account}, inbox={total} msgs")
            except Exception as e:  # noqa: BLE001
                ok, msg = _check("IMAP login", False, str(e))
                all_ok = False
        lines.append(msg)
    elif args.account is None:
        lines.append("  [SKIP] IMAP login — pass --account <id> to test")
    else:
        lines.append("  [SKIP] IMAP login — --skip-imap")

    # Optional tooling (informational)
    for tool in ("hermes", "bw", "life-mail-passwd"):
        present = shutil.which(tool) is not None
        lines.append(f"  [{'OK' if present else 'INFO'}] {tool} in PATH: {present}")

    lines.append("")
    if all_ok:
        lines.append("RESULT: PASS — ready for demo-pilot or worker run")
    else:
        lines.append("RESULT: FAIL — fix items above before pilot install")
    print("\n".join(lines))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
