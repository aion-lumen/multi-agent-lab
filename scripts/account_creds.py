"""account_creds — single source of truth for resolving an IMAP account password.

P0 Yahoo-Struktur-Move (2026-07-12). The productive mail path (Folio pipeline,
production_worker) historically resolved the password via Bitwarden (`bw_item`
→ life-mail-passwd → `bw get`). That path goes stale whenever the vault locks
and surfaces as a misleading `AUTHENTICATIONFAILED` (empty password). To make the
move path bw-/timeout-free, the Yahoo password now lives in a 0600 file read via
`password_cmd` in accounts.toml.

Precedence (highest first):
  1. password_cmd  — a shell command whose stdout is the password
  2. password_env  — an environment variable holding the password
  3. bw_item       — Bitwarden item name, resolved via life-mail-passwd (fallback)

Two entry points:
  - resolve_password_or_none(acct): cmd > env > None. Returns None when only
    `bw_item` is present, so IMAPSession-based callers can keep letting the
    session layer resolve bw lazily. Mirrors production_worker's historic
    _resolve_password semantics exactly (one truth — production_worker delegates).
  - resolve_password(acct): always returns a concrete password string; resolves
    `bw_item` via life-mail-passwd as a last resort. Used by raw-imaplib callers
    (imap_cleanup) that need the actual string for conn.login().

Dependency-free (stdlib only) so both the requests-heavy production_worker and
the lean move scripts can import it without pulling extra deps.
"""
from __future__ import annotations

import os
import subprocess


def resolve_password_or_none(acct: dict) -> str | None:
    """Resolve from password_cmd, then password_env; else None (→ bw_item fallback).

    None signals the caller to fall back to `bw_item` (e.g. via IMAPSession).
    """
    cmd = acct.get("password_cmd")
    if cmd:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    env_key = acct.get("password_env")
    if env_key:
        val = os.environ.get(str(env_key))
        if not val:
            raise RuntimeError(f"password_env {env_key!r} is unset")
        return val
    return None


def _bw_password(bw_item: str) -> str:
    """Bitwarden fallback via the life-mail-passwd shell-helper (reads the
    file-session ~/.config/life/bw-session). Timeout-prone — only reached when
    neither password_cmd nor password_env is configured."""
    return subprocess.run(
        ["life-mail-passwd", bw_item],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()


def resolve_password(acct: dict) -> str:
    """Always return a concrete password string. cmd > env > bw_item(→bw)."""
    pw = resolve_password_or_none(acct)
    if pw is not None:
        return pw
    bw_item = acct.get("bw_item")
    if bw_item:
        return _bw_password(bw_item)
    raise RuntimeError(
        "no password source in account config (need password_cmd, password_env, or bw_item)"
    )
