#!/usr/bin/env python3
"""
audit_filesystem_exposure.py - Inspect what the Worker process can see.
Read-only. NEVER prints secret values - only paths, permissions, sizes,
and (for risky env-vars) length + masked prefix/suffix.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

CRITICAL_PATHS = {
    "ssh_private_rsa": Path.home() / ".ssh" / "id_rsa",
    "ssh_private_ed25519": Path.home() / ".ssh" / "id_ed25519",
    "ssh_authorized_keys": Path.home() / ".ssh" / "authorized_keys",
    "aws_credentials": Path.home() / ".aws" / "credentials",
    "anthropic_creds": Path.home() / ".anthropic" / "credentials.json",
    "openai_creds": Path.home() / ".openai" / "credentials.json",
    "gh_token_yml": Path.home() / ".config" / "gh" / "hosts.yml",
    "git_credentials": Path.home() / ".git-credentials",
    "macos_keychain_db": Path.home() / "Library" / "Keychains" / "login.keychain-db",
}

AION_LUMEN_SENSITIVE = {
    "hermes_root_env": Path.home() / ".hermes" / ".env",
    "council_env": Path.home() / "Projects" / "aion-lumen" / "council" / ".env",
    "lifemail_env_a": Path.home() / "Projects" / "life-mail" / ".env",
    "lifemail_env_b": Path.home() / "Projects" / "aion-lumen" / "life-mail" / ".env",
    "hermes_memories": Path.home() / ".hermes" / "memories",
    "hermes_kanban_db": Path.home() / ".hermes" / "kanban.db",
    "vault_root": Path.home() / "Projects" / "life",
    "carta_do_not_touch": Path.home() / "Projects" / "carta",
}

PROJECT_AREAS = {
    "multi_agent": Path.home() / "Projects" / "aion-lumen" / "multi-agent",
    "scripts": Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "scripts",
    "state": Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state",
}


def file_status(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "readable": None, "writable": None,
                "mode": None, "size": None}
    try:
        s = path.stat()
        return {
            "exists": True,
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
            "mode": stat.filemode(s.st_mode),
            "size": (s.st_size if not path.is_dir() else None),
        }
    except (OSError, PermissionError) as e:
        return {"exists": True, "readable": False, "writable": False,
                "mode": str(e), "size": None}


def main() -> None:
    print("=" * 70)
    print("FILESYSTEM-EXPOSITION-AUDIT")
    print("=" * 70)
    print(f"Running as: {os.environ.get('USER', '?')} (uid={os.getuid()})")
    print(f"env-vars visible: {len(os.environ)}\n")

    print("CRITICAL PATHS (should not be readable by Worker):")
    print("-" * 70)
    for label, path in CRITICAL_PATHS.items():
        st = file_status(path)
        if st["exists"]:
            risk = "HIGH-RISK" if st["readable"] else "ok"
            print(f"  [{risk:<10}] {label:<24} mode={st['mode']:<11} size={st['size']}")
        else:
            print(f"  [not-present] {label:<24}")
    print()

    print("AION-LUMEN-SENSITIVE PATHS:")
    print("-" * 70)
    for label, path in AION_LUMEN_SENSITIVE.items():
        st = file_status(path)
        if st["exists"]:
            r = "R" if st["readable"] else "-"
            w = "W" if st["writable"] else "-"
            sz = st["size"] if st["size"] is not None else "<dir>"
            print(f"  [{r}{w}] {label:<24} mode={st['mode']:<11} size={sz}")
        else:
            print(f"  [--] {label:<24} (not present)")
    print()

    print("PROJECT AREAS (worker workspace):")
    print("-" * 70)
    for label, path in PROJECT_AREAS.items():
        st = file_status(path)
        if st["exists"]:
            r = "R" if st["readable"] else "-"
            w = "W" if st["writable"] else "-"
            print(f"  [{r}{w}] {label:<24} {path}")
    print()

    print("RISKY ENV-VARS IN WORKER PROCESS (mask values):")
    print("-" * 70)
    patterns = ("TOKEN", "KEY", "SECRET", "PASSWORD", "API", "CREDENTIAL", "AUTH",
                "BEARER", "OAUTH")
    risky = []
    for k, v in os.environ.items():
        if any(p in k.upper() for p in patterns):
            masked = (v[:4] + "..." + v[-4:]) if len(v) > 12 else "[short]"
            risky.append((k, masked, len(v)))
    if risky:
        for k, masked, ln in sorted(risky):
            print(f"  ! {k:<40} {masked} (len={ln})")
    else:
        print("  (none found - good)")
    print()

    print("=" * 70)
    print("Audit complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
