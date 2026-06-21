"""Central path resolution for multi-agent pipeline scripts.

Override via environment variables for non-default layouts.
"""
from __future__ import annotations

import os
from pathlib import Path

_HOME = Path.home()
_REPO_ROOT = Path(__file__).resolve().parent.parent

STATE_DIR = Path(os.environ.get("MULTI_AGENT_STATE_DIR", str(_REPO_ROOT / "state")))
FEEDBACK_DB = Path(os.environ.get("FEEDBACK_DB_PATH", str(STATE_DIR / "feedback.db")))
FOLIO_DB = Path(os.environ.get("FOLIO_DB_PATH", str(_HOME / ".folio" / "folio.db")))
CONFIG_DIR = Path(os.environ.get("MULTI_AGENT_CONFIG_DIR", str(_REPO_ROOT / "config")))
REGELWERK_YAML = CONFIG_DIR / "regelwerk.yaml"
USER_CONTEXT_YAML = CONFIG_DIR / "user_context.yaml"
IMMO_WHITELIST_YAML = CONFIG_DIR / "immo_whitelist.yaml"
COUNCIL_CONFIG_DIR = Path(
    os.environ.get("COUNCIL_CONFIG_PATH", str(_HOME / "Projects" / "aion-lumen" / "council" / "config"))
)
PORTALS_YAML = COUNCIL_CONFIG_DIR / "portals.yaml"
ACCOUNTS_TOML = Path(
    os.environ.get("LIFE_MAIL_ACCOUNTS_TOML", str(_HOME / "Projects" / "life-mail" / "accounts.toml"))
)
PLUGIN_CLI = Path(
    os.environ.get(
        "HERMES_PLUGIN_CLI",
        str(_HOME / ".hermes" / "plugins" / "email-classification" / "cli.py"),
    )
)
LIFE_MAIL_SCRIPTS = Path(
    os.environ.get("LIFE_MAIL_SCRIPTS", str(_HOME / "Projects" / "life-mail" / "scripts"))
)
REPO_ROOT = _REPO_ROOT
LOG_FILE = STATE_DIR / "production-worker.log"
SENDER_HEURISTICS_JSON = STATE_DIR / "sender-heuristics.json"
MARKETING_PATTERNS_JSON = STATE_DIR / "marketing-patterns.json"
