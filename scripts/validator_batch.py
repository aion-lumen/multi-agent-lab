#!/usr/bin/env python3
"""validator_batch.py — F.8 Block-E Validator-Pipeline (2-Axes Schema).

Liest feedback.db UIDs (scope-gefiltert), ruft gemma-4-26b via Hermes-Agent-API
mit User-Context-Injection und schreibt validator_opinions
(validator_domain + validator_actionability) in folio.db.

Usage:
    validator_batch.py --scope {unreviewed|all|last-tranche}
                       [--account <id>] [--limit N]

Scopes:
  - unreviewed:    not in folio.db.review_state
  - all:           alle feedback.db rows
  - last-tranche:  rows aus neuestem silent worker_run (Default + Auto-Trigger Use-Case).
                   Cross-DB-Read: folio.db.worker_runs neueste completed silent run
                   → filter feedback by created_at zwischen started_at und ended_at.

Cleanup 2026-05-27: scope `disagreements` raus (obsolet durch Drei-Lens-Architektur).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# Import for user-context + regelwerk + model-swap (re-use Worker's loaders).
# Review-followup A.4 2026-05-27: fail-loud on ImportError. Stub-fallback preserves
# pre-existing behavior, but logs to stderr (logging not yet initialized here) +
# respects EXIT_ON_IMPORT_FAIL=1 to abort instead of running degraded.
sys.path.insert(0, str(Path(__file__).resolve().parent))
_EXIT_ON_IMPORT_FAIL = os.environ.get("EXIT_ON_IMPORT_FAIL") == "1"

try:
    from domain_actionability import (
        load_user_context,
        load_regelwerk,
        validate_regelwerk_against_context,
        DEFAULT_CONTEXT,
        DEFAULT_REGELWERK,
    )
except ImportError as _e:
    print(
        f"[CRITICAL] failed to import domain_actionability ({_e}) — running with "
        f"stub fallback (DEFAULT_CONTEXT minimal, no validation). "
        f"Set EXIT_ON_IMPORT_FAIL=1 to abort instead.",
        file=sys.stderr,
    )
    if _EXIT_ON_IMPORT_FAIL:
        sys.exit(2)
    DEFAULT_CONTEXT = {"active_priorities": [], "sender_priorities": {}}
    DEFAULT_REGELWERK = {"action_definitions": {}, "priority_relevance": {}}
    def load_user_context(path=None):  # type: ignore
        return DEFAULT_CONTEXT
    def load_regelwerk(path=None):  # type: ignore
        return DEFAULT_REGELWERK
    def validate_regelwerk_against_context(rw, uc):  # type: ignore
        return None

try:
    from model_swap import (
        swap_to as _swap_to,
        unload_plugin_before_first_lens as _unload_plugin,
    )
except ImportError as _e:
    print(
        f"[CRITICAL] failed to import model_swap ({_e}) — running with no-op "
        f"fallback (model swapping disabled, all lenses hit currently-loaded model). "
        f"Set EXIT_ON_IMPORT_FAIL=1 to abort instead.",
        file=sys.stderr,
    )
    if _EXIT_ON_IMPORT_FAIL:
        sys.exit(2)
    def _swap_to(model_id: str, *, timeout_s: int = 240) -> bool:  # type: ignore
        return True   # fall back to no-op (caller can still proceed)
    def _unload_plugin() -> bool:  # type: ignore
        return True


from paths import FEEDBACK_DB, FOLIO_DB  # noqa: E402
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://localhost:8642")
HERMES_API_KEY = os.environ.get("API_SERVER_KEY", "") or os.environ.get("HERMES_API_KEY", "")
VALIDATOR_MODEL = os.environ.get("VALIDATOR_MODEL", "gemma-4-26b-a4b-it-mlx")

# Direktive 2026-05-26 Lens-Fix: Hermes-Bypass — Lens-Calls gehen DIREKT an
# LM-Studio (OpenAI-kompatibler chat/completions-Endpoint). Eliminiert Hermes'
# default-model-coercion (Hermes ignoriert den `model`-Param und erzwingt sein
# Default-Modell aus ~/.hermes/config.yaml). LM-Studio respektiert per-request
# model selection.
LM_STUDIO_BASE_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234")

# F.8.5: correspondence → kontakt rename + werbung als 8. Domain.
DOMAIN_KEYS = ("immo", "job", "shopping", "finance", "kontakt", "werbung", "system", "unsorted")
ACTIONABILITY_KEYS = ("actionable", "archive", "archive-silent")


# Direktive 2026-05-26 Lens-Fix: dediziertes Lens-Prompt OHNE Heuristik-/Plugin-
# Hint-Zeilen. Delphi-Prinzip: jede Lens schaut allein auf dieselbe rohe Mail,
# kennt KEIN fremdes Urteil (auch nicht „n/a"-Erwähnungen anderer Stimmen).
LENS_PROMPT = """\
Du bist ein blinder Klassifikator für E-Mail-Triage. Du arbeitest unabhängig —
es gibt keine vorherigen Urteile, kein Plugin-Hint, keine Heuristik-Vorgabe.
Klassifiziere die E-Mail nur aus dem, was du selbst siehst, auf 2 Achsen:
domain × actionability.

Domain (genau EINE):
  - immo            (Immobilien: Portale, Privat, Inserate)
  - job             (Job-Suche: LinkedIn, Indeed, Karriere)
  - shopping        (Bestellungen, Paketzustellung, Lieferungen, Versand)
  - finance         (Rechnungen, Versicherungen, Steuern, Banken, Abos)
  - kontakt         (private Personen, direkte berufliche Kommunikation, kein Bulk-Sender)
  - werbung         (Newsletter, Marketing, Promo, Aktionen, Rabatte)
  - system          (Security-Alerts, 2FA, Account-Verifizierung)
  - unsorted        (kein klares Match)

WICHTIG — Substanz-Definition für domain=immo (Bauteil 8):
Eine Mail ist nur dann domain=immo, wenn sie sich auf EIN KONKRETES
OBJEKT bezieht — erkennbar an mindestens ZWEI der folgenden Stammdaten:
  - Adresse oder PLZ
  - Preis (€/CHF)
  - qm (Wohnfläche)
  - Inserat-URL mit /expose/, /Expose/, /Detail/ oder ähnlichem Pattern
Ratgeber-Artikel, Portal-Newsletter ("Gemeinderatgeber", "Markt-Übersicht"),
Marketing-Mails ohne konkretes Objekt → domain=werbung, NICHT immo.
Themen-Relevanz allein reicht NICHT. "Gemeinderatgeber Homegate" ist
immo-themen-relevant aber kein immo, weil kein konkretes Objekt dahinter.

Beispiele:
  - Positiv (immo):  "1 neue Immobilie: Reihenhaus, Rua das Oliveiras 18,
    8100 Loulé, 4 Zimmer, 120 qm, 450.000 EUR. /expose/123456"
    → domain=immo, actionability=actionable
  - Negativ (werbung): "Portal-Marktbericht: Preise im Algarve steigen.
    Lesen Sie unsere Analyse." → domain=werbung, actionability=archive-silent

Actionability (genau EINE — Definitionen aus zentralem Regelwerk):
{actionability_block}

User-Context (relevant für Priorisierung):
{user_context_block}

E-Mail:
  Sender:  {sender}
  Subject: {subject}
  Body (erste 1000 Zeichen):
{body}

Achte besonders auf:
  - Mails von Job/Immo-Portalen sind oft `werbung`/`archive-silent` AUSSER User hat
    aktive Priorität (z.B. hauskauf → immo bleibt `actionable`).
  - Paketzustellung-Mails sind IMMER `actionable` (User muss Lieferung wahrnehmen).
  - Private Personen ohne Bulk-Sender-Prefix sind `kontakt` + `actionable`.
  - Newsletter/Marketing/Promo-Mails sind `werbung` + `archive-silent` (default),
    AUSSER sie sind zeitkritisch (Sale läuft ab in 24h → actionable).

Antworte AUSSCHLIESSLICH als JSON:
{{"domain": "<domain>", "actionability": "<actionability>", "confidence": <0.0-1.0>, "reasoning": "<max 200 Zeichen>"}}
"""

log = logging.getLogger("validator_batch")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def fetch_last_tranche_window() -> tuple[str, str] | None:
    """Cross-DB-Read folio.db.worker_runs: latest completed silent run.
    Returns (started_at, ended_at) ISO-strings or None if no completed run."""
    if not FOLIO_DB.exists():
        log.warning("folio.db not found for last-tranche scope: %s", FOLIO_DB)
        return None
    conn = sqlite3.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT started_at, ended_at FROM worker_runs "
            "WHERE mode = 'silent' AND status = 'completed' "
            "AND ended_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return (row[0], row[1])


def fetch_target_uids(
    scope: str,
    account: Optional[str],
    limit: Optional[int],
    mail_ids: Optional[list[int]] = None,
) -> list[dict]:
    """Return list of feedback-rows matching scope.

    Bauteil-8 A5b (2026-06-09): Wenn mail_ids gesetzt, explizite Liste
    statt scope-basierter Lookup (verhindert state-race in Subprocess-
    Pfad — siehe Bauteil-7-Cascade-Bug). scope wird ignoriert.
    """
    if not FEEDBACK_DB.exists():
        log.error("feedback.db not found: %s", FEEDBACK_DB)
        return []
    conn = sqlite3.connect(f"file:{FEEDBACK_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    where = []
    params: list = []
    if mail_ids:
        placeholders = ",".join("?" for _ in mail_ids)
        where.append(f"id IN ({placeholders})")
        params.extend(mail_ids)
        log.info("explicit mail-ids: %d rows", len(mail_ids))
    elif scope == "last-tranche":
        window = fetch_last_tranche_window()
        if not window:
            log.warning("no completed silent worker_run found — last-tranche returns 0 rows")
            conn.close()
            return []
        started_at, ended_at = window
        log.info("last-tranche window: started_at=%s ended_at=%s", started_at, ended_at)
        # F.8 Block-E: created_at ist Worker-INSERT-Zeit → liegt zwischen worker_run.started_at und ended_at
        where.append("created_at >= ? AND created_at <= ?")
        params.extend([started_at, ended_at])
    if account:
        where.append("account_id = ?")
        params.append(account)
    sql = "SELECT * FROM feedback"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return rows


def fetch_reviewed_ids() -> set[int]:
    """Cross-DB-Read: return feedback_ids that ARE in folio.db review_state."""
    if not FOLIO_DB.exists():
        return set()
    conn = sqlite3.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)
    try:
        reviewed = {r[0] for r in conn.execute("SELECT feedback_id FROM review_state").fetchall()}
    finally:
        conn.close()
    return reviewed


def format_actionability_block(regelwerk: dict) -> str:
    """Render action_definitions from regelwerk.yaml as prompt-block (Direktive 2026-05-26 E1)."""
    actions = regelwerk.get("action_definitions") or {}
    lines = []
    for key in ("actionable", "archive", "archive-silent"):
        defn = actions.get(key) or {}
        desc = defn.get("description") or "(no description)"
        lines.append(f"  - {key:16s} ({desc})")
    return "\n".join(lines) if lines else "  (action_definitions missing from regelwerk)"


def format_user_context_block(ctx: dict, regelwerk: dict | None = None) -> str:
    """Render user_context + regelwerk's priority_relevance as prompt-block.
    Bei aktiven Prioritäten werden zusätzlich die Distanz-Schwellen aus dem
    Regelwerk gerendert (Direktive 2026-05-26 E2)."""
    priorities = ctx.get("active_priorities") or []
    life_status = ctx.get("life_status") or {}
    sp = ctx.get("sender_priorities") or {}
    silent_count = len(sp.get("always_archive_silent", []) or [])
    actionable_count = len(sp.get("always_actionable", []) or [])
    pr = (regelwerk or {}).get("priority_relevance") or {}

    lines = []
    if priorities:
        lines.append(f"  Aktive Prioritäten: {', '.join(priorities)}")
        for p in priorities:
            keys = life_status.get(f"search_terms_{p}") if isinstance(life_status, dict) else None
            if keys:
                lines.append(f"    {p}: {', '.join(keys[:6])}")
            # E2 — Distanz-Schwelle pro Priorität aus zentralem Regelwerk
            rule = pr.get(p)
            if rule:
                lines.append(
                    f"      → Relevanz: domain={rule.get('domain')}, "
                    f"max_distance_km={rule.get('max_distance_km')}, "
                    f"fallback_unknown_plz={rule.get('fallback_unknown_plz')}"
                )
    else:
        lines.append("  Keine aktiven Lebensprioritäten gesetzt.")
    lines.append(
        f"  User hat {silent_count} immer-silent + {actionable_count} immer-actionable Sender-Regeln."
    )
    return "\n".join(lines) if lines else "  (kein Kontext)"


def strip_llm_response(text: str, strip_mode: str) -> str:
    """Pre-json strip based on regelwerk.voice.response_strip:
        - "think":      strip <think>...</think> blocks (qwen3-thinking), THEN code-fence
        - "code_fence": strip ```...``` / ```json fences (default for non-thinking models)
        - "none":       return as-is

    Idempotent: applying "think" to non-thinking output is a no-op (no <think> match).
    """
    text = text.strip()
    if strip_mode == "think":
        # remove any number of <think>...</think> blocks (qwen sometimes emits multiple)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if strip_mode in ("think", "code_fence"):
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json\n"):
                text = text[5:]
    return text


def call_lens_lm_studio(
    row: dict,
    user_context: dict,
    regelwerk: dict,
    *,
    model_id: str,
    response_strip: str = "code_fence",
) -> Optional[dict]:
    """Direct LM-Studio call (bypasses Hermes' default-model-coercion).

    Direktive 2026-05-26 Lens-Fix: LM-Studio's OpenAI-compatible
    /v1/chat/completions respects the per-request `model` parameter. Hermes'
    /v1/responses does not (it forces ~/.hermes/config.yaml's default-model).
    Each lens calls its OWN model directly; the call is "blind" — the prompt
    contains only the mail + the lens's own prompt template, never another
    lens's verdict or the heuristic result (Delphi principle, no anchor).

    Returns parsed {domain, actionability, confidence, reasoning} or None.
    No auth (LM-Studio is local-only, OpenAI-compatible no-auth on loopback).
    """
    body_excerpt = (row.get("body_excerpt") or "")[:1000]
    # LENS_PROMPT is the blind variant — no heuristic/plugin hint anywhere.
    prompt = LENS_PROMPT.format(
        actionability_block=format_actionability_block(regelwerk),
        user_context_block=format_user_context_block(user_context, regelwerk),
        sender=row.get("sender", ""),
        subject=row.get("subject", ""),
        body=body_excerpt or "[body unavailable for legacy row — pre-i2-migration entry]",
    )
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    try:
        resp = requests.post(
            f"{LM_STUDIO_BASE_URL}/v1/chat/completions",
            json=payload,
            timeout=240,
        )
        if resp.status_code != 200:
            log.error("LM-Studio error %d (model=%s): %s",
                      resp.status_code, model_id, resp.text[:300])
            return None
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            log.warning("LM-Studio returned no choices (model=%s)", model_id)
            return None
        text = ((choices[0].get("message") or {}).get("content") or "")
        # Hermes /v1/responses returns under output[].content[].text — fallback
        # only if chat/completions endpoint ever changes. Direct LM-Studio uses
        # the OpenAI shape above.
        text = strip_llm_response(text, response_strip)
        if not text:
            log.warning("LM-Studio empty content after strip (model=%s)", model_id)
            return None
        parsed = json.loads(text)
        if parsed.get("domain") not in DOMAIN_KEYS:
            log.warning("invalid domain from lens (model=%s): %r",
                        model_id, parsed.get("domain"))
            return None
        if parsed.get("actionability") not in ACTIONABILITY_KEYS:
            log.warning("invalid actionability from lens (model=%s): %r",
                        model_id, parsed.get("actionability"))
            return None
        # Review-followup A.2 2026-05-27: validate confidence here so a partial
        # response (None / non-numeric / out-of-range) becomes a lens-local skip
        # instead of a TypeError abort downstream in write_opinion.
        conf_raw = parsed.get("confidence")
        if conf_raw is None:
            log.warning("missing/null confidence from lens (model=%s)", model_id)
            return None
        try:
            conf_float = float(conf_raw)
        except (TypeError, ValueError):
            log.warning("non-numeric confidence from lens (model=%s): %r",
                        model_id, conf_raw)
            return None
        if not (0.0 <= conf_float <= 1.0):
            log.warning("confidence out of [0,1] from lens (model=%s): %r",
                        model_id, conf_raw)
            return None
        parsed["confidence"] = conf_float  # normalize for downstream
        return parsed
    except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
        log.error("lens call failed (model=%s): %s", model_id, e)
        return None


def write_opinion(row: dict, opinion: dict, *, validator_model: Optional[str] = None) -> None:
    """Insert into folio.db.validator_opinions with 2-axes columns (UPSERT).

    validator_model defaults to module-level VALIDATOR_MODEL for backward-compat
    with single-model runs. The orchestrated voice-loop passes the per-voice
    model_id so each voice gets its own row (UNIQUE(feedback_id, validator_model)).
    Re-run with the same model = idempotent UPDATE."""
    FOLIO_DB.parent.mkdir(parents=True, exist_ok=True)
    effective_model = validator_model or VALIDATOR_MODEL
    conn = sqlite3.connect(str(FOLIO_DB))
    try:
        # F.8 Block-E: validator_action wird mit domain+actionability als kompakter String befüllt
        # für Back-Compat (alte Readers, die nur diese Spalte kennen). Neue Readers nutzen
        # validator_domain + validator_actionability.
        compat_action = f"{opinion['domain']}/{opinion['actionability']}"
        conn.execute(
            """INSERT INTO validator_opinions
               (feedback_id, account_id, imap_uid, validator_model,
                validator_action, validator_domain, validator_actionability,
                validator_confidence, validator_reasoning, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(feedback_id, validator_model) DO UPDATE SET
                 validator_action = excluded.validator_action,
                 validator_domain = excluded.validator_domain,
                 validator_actionability = excluded.validator_actionability,
                 validator_confidence = excluded.validator_confidence,
                 validator_reasoning = excluded.validator_reasoning,
                 evaluated_at = excluded.evaluated_at""",
            (
                row["id"],
                row["account_id"],
                row["imap_uid"],
                effective_model,
                compat_action,
                opinion["domain"],
                opinion["actionability"],
                float(opinion.get("confidence") or 0.0),  # belt-and-suspenders; A.2 validates upstream
                (opinion.get("reasoning") or "")[:500],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(prog="validator_batch")
    ap.add_argument(
        "--scope",
        choices=("unreviewed", "all", "last-tranche"),
        default="last-tranche",
    )
    ap.add_argument("--account", choices=("yahoo", "gmail", "mirhamed"), default=None)
    ap.add_argument("--limit", type=int, default=None)
    # 2026-06-07 Pre-Bauteil Pipeline-Persistenz: run_uuid von folio
    # manager.ts; CLI-arg + env-Fallback FOLIO_RUN_UUID.
    ap.add_argument("--run-uuid", default=None,
                    help="folio worker_runs.run_uuid (für Cross-DB-Logs)")
    # Bauteil-8 A5b (2026-06-09): explizite Mail-ID-Liste statt
    # last-tranche-Lookup. Verhindert Subprocess-State-Race wenn
    # production_worker den Subprocess startet bevor sein eigener
    # worker_run.status='completed' gesetzt ist.
    ap.add_argument("--mail-ids", default=None,
                    help="CSV-Liste feedback.id (überschreibt --scope). "
                         "Sauberer Pfad für post-worker-Subprocess-Hook.")
    args = ap.parse_args()

    user_context = load_user_context()
    regelwerk = load_regelwerk()
    # Cross-Reference-Validierung (Direktive 2026-05-26): exit-with-error if
    # active_priorities ↔ priority_relevance inconsistent.
    try:
        validate_regelwerk_against_context(regelwerk, user_context)
    except Exception as e:  # noqa: BLE001
        log.error("regelwerk/user_context cross-reference invalid: %s", e)
        return 2
    # Voice-Liste aus regelwerk extrahieren — primary_llm + control_llm laufen
    # sequenziell mit Modell-Swap dazwischen (heuristic-voice ist deterministisch,
    # läuft im Worker und nicht hier).
    # F4 (Direktive 2026-05-26): `enabled: false` skipt eine Lens komplett —
    # genutzt während Hermes-Bypass-Build um qwen-Lenses zu pausieren.
    # Default bei fehlendem `enabled`-Feld = True (backward-compat).
    all_voices = (regelwerk.get("voice_consensus") or {}).get("voices") or []
    llm_voices = [
        v for v in all_voices
        if v.get("role") in ("primary_llm", "control_llm")
        and v.get("enabled", True)
    ]
    disabled_llm = [
        v.get("id") for v in all_voices
        if v.get("role") in ("primary_llm", "control_llm")
        and not v.get("enabled", True)
    ]
    if disabled_llm:
        log.info("disabled LLM voices (F4): %s", disabled_llm)
    log.info(
        "validator_batch scope=%s account=%s limit=%s mode=%s priorities=%s voices=%s",
        args.scope, args.account, args.limit,
        regelwerk.get("mode"),
        user_context.get("active_priorities") or [],
        [v.get("id") for v in llm_voices],
    )

    # Bauteil-8 A5b: --mail-ids hat Vorrang vor --scope
    parsed_mail_ids: Optional[list[int]] = None
    if args.mail_ids:
        try:
            parsed_mail_ids = [int(x.strip()) for x in args.mail_ids.split(",") if x.strip()]
        except ValueError as e:
            log.error("invalid --mail-ids CSV: %s", e)
            return 2
    rows = fetch_target_uids(args.scope, args.account, args.limit, mail_ids=parsed_mail_ids)
    if args.scope == "unreviewed":
        reviewed = fetch_reviewed_ids()
        rows = [r for r in rows if r["id"] not in reviewed]
    log.info("target rows: %d", len(rows))

    # Direktive 2026-05-26 Lens-Fix: pro lens sequenziell. Direkt LM-Studio
    # (Bypass Hermes default-model-coercion). Modell-Swap synchron (wartet bis
    # `lms ps` das Zielmodell als geladen bestätigt). Swap-Failure → log+continue,
    # NIE die ganze Tranche failen. Jede Lens-Opinion landet als eigene Zeile
    # in validator_opinions via UNIQUE(feedback_id, validator_model) UPSERT.
    #
    # Plugin-Vor-Lens-Entladung: vor der ERSTEN Lens explicit unload-all damit
    # ein resident-gebliebenes Plugin-Modell (qwen3.6-35b) nicht den RAM-Guard
    # triggert beim Lens-1-Load.
    if llm_voices:
        _unload_plugin()

    total_opinions = 0
    voice_stats: dict[str, dict] = {}
    for voice in llm_voices:
        vid = voice.get("id") or "?"
        model_id = voice.get("lm_studio_model")
        strip_mode = voice.get("response_strip") or "code_fence"
        if not model_id:
            log.warning("lens %s has no lm_studio_model — skipping", vid)
            voice_stats[vid] = {"loaded": False, "ok": 0, "fail": 0, "skipped": len(rows)}
            continue
        log.info("=== lens=%s model=%s strip=%s ===", vid, model_id, strip_mode)
        if not _swap_to(model_id):
            log.warning("model-swap to %s failed — skipping lens %s (tranche continues)",
                        model_id, vid)
            voice_stats[vid] = {"loaded": False, "ok": 0, "fail": 0, "skipped": len(rows)}
            continue
        ok = fail = 0
        for i, row in enumerate(rows, start=1):
            opinion = call_lens_lm_studio(
                row, user_context, regelwerk,
                model_id=model_id, response_strip=strip_mode,
            )
            if opinion is None:
                log.warning("lens=%s uid=%d → lens returned None", vid, row["imap_uid"])
                fail += 1
                continue
            write_opinion(row, opinion, validator_model=model_id)
            ok += 1
            log.info(
                "lens=%s i=%d uid=%d → domain=%s action=%s conf=%.2f",
                vid, i, row["imap_uid"],
                opinion["domain"], opinion["actionability"],
                opinion.get("confidence", 0.0),
            )
            # 2026-06-07 Pre-Bauteil Pipeline-Persistenz: Per-Voice-Log
            # in worker_run_logs (no-op wenn kein run_uuid).
            try:
                from folio_log_writer import write_log, get_run_uuid_from_env_or_args  # noqa: PLC0415
                _ru = get_run_uuid_from_env_or_args(getattr(args, "run_uuid", None))
                if _ru:
                    # voice-Mapping: regelwerk-id (gemma-control / qwen-validator /
                    # qwen35b-lens) → folio-Type (gemma / qwen-thinking / qwen).
                    voice_short = {
                        "gemma-control": "gemma",
                        "qwen-validator": "qwen-thinking",
                        "qwen35b-lens": "qwen",
                    }.get(vid, vid)
                    write_log(
                        _ru, voice_short, "validated",
                        f"#{row['id']} → {opinion['domain']}/{opinion['actionability']}",
                        mail_id=row["id"],
                    )
            except Exception as _e:  # noqa: BLE001
                log.warning("[folio_log_writer] write_log failed: %s", _e)
        voice_stats[vid] = {"loaded": True, "ok": ok, "fail": fail, "skipped": 0}
        total_opinions += ok

    log.info("validator_batch done: total_opinions=%d/(%d×%d) per-voice=%s",
             total_opinions, len(rows), len(llm_voices), voice_stats)

    # 2026-06-06 Bauteil 2 (Mail-zu-Council-Uebergang): nach allen Voice-Loops
    # die Vier-Stimmen-Vollkonsens-Pruefung ausfuehren. Mails mit (immo,
    # actionable) + allen 3 Validatoren einig + kein Block-Marker werden auf
    # actionability='uebernommen' promoted. Nur die feedback_ids dieses Runs.
    # 2026-06-07 Pre-Bauteil: auto_uebernahme erbt run_uuid (gleicher Prozess).
    try:
        from auto_uebernahme import promote_eligible  # noqa: PLC0415
        from folio_log_writer import get_run_uuid_from_env_or_args  # noqa: PLC0415
        run_ids = [r["id"] for r in rows]
        if run_ids:
            ru = get_run_uuid_from_env_or_args(getattr(args, "run_uuid", None))
            stats = promote_eligible(feedback_ids=run_ids, run_uuid=ru)
            log.info("auto_uebernahme: checked=%d eligible=%d promoted=%d",
                     stats["checked"], stats["eligible"], stats["promoted"])
    except Exception as e:  # noqa: BLE001
        log.warning("auto_uebernahme hook failed (non-fatal): %s", e)

    # Direktive D (2026-06-10): IMAP-Cleanup nach auto_uebernahme — strukturell
    # garantiert Validierung → Übernahme → Cleanup. Gates: account=yahoo +
    # regelwerk.imap_cleanup.enabled. Non-fatal (Exit-Code unverändert).
    if args.account == "yahoo":
        try:
            rw = load_regelwerk()
            cleanup_enabled = ((rw.get("imap_cleanup") or {}).get("enabled") is True)
        except Exception:  # noqa: BLE001
            cleanup_enabled = False
        if cleanup_enabled:
            from folio_log_writer import write_log, get_run_uuid_from_env_or_args  # noqa: PLC0415
            _ru_cleanup = get_run_uuid_from_env_or_args(getattr(args, "run_uuid", None))
            log.info("IMAP-Cleanup: starting imap_cleanup.py as subprocess")
            write_log(_ru_cleanup, voice="cleanup", event_type="info",
                      message="cleanup_started", level="info")
            cmd = [
                sys.executable,
                str(Path(__file__).resolve().parent / "imap_cleanup.py"),
            ]
            try:
                rc = subprocess.run(cmd, check=False).returncode
                if rc == 0:
                    write_log(_ru_cleanup, voice="cleanup", event_type="info",
                              message="cleanup_ok", level="info")
                else:
                    log.warning("IMAP-Cleanup subprocess exited rc=%d", rc)
                    write_log(_ru_cleanup, voice="cleanup", event_type="info",
                              message=f"cleanup_failed:rc={rc}", level="warn")
            except Exception as e:  # noqa: BLE001
                log.warning("IMAP-Cleanup subprocess raised: %s", e)
                write_log(_ru_cleanup, voice="cleanup", event_type="info",
                          message=f"cleanup_error:{e}", level="error")

    # 2026-06-07 Pre-Bauteil Pipeline-Persistenz: Run-Ende-Summary fuer
    # validator_batch (geprueft = len(rows), uebernommen/actionable/silent
    # aus aktuellem feedback.actionability nach auto_uebernahme).
    try:
        from folio_log_writer import write_summary, get_run_uuid_from_env_or_args  # noqa: PLC0415
        _ru = get_run_uuid_from_env_or_args(getattr(args, "run_uuid", None))
        if _ru and rows:
            import sqlite3 as _sql, json as _json
            from pathlib import Path as _Path
            mail_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" for _ in mail_ids)
            with _sql.connect(str(FEEDBACK_DB)) as fconn:
                actionable_n = silent_n = uebernommen_n = 0
                reason_breakdown: dict[str, int] = {}
                sample: list[dict] = []
                for fid, subj, sender, dom, act, markers in fconn.execute(
                    f"SELECT id, subject, sender, domain, actionability, heuristic_markers "
                    f"FROM feedback WHERE id IN ({placeholders})",
                    mail_ids,
                ).fetchall():
                    if act == "actionable":
                        actionable_n += 1
                    elif act in ("archive-silent", "archive"):
                        silent_n += 1
                    elif act == "uebernommen":
                        uebernommen_n += 1
                    try:
                        arr = _json.loads(markers) if markers else []
                    except Exception:  # noqa: BLE001
                        arr = []
                    for m in arr:
                        for prefix in ("out_of_corridor", "tier1:projektiert",
                                       "tier1:zwangsversteigerung",
                                       "tier1:price_on_request", "decay:",
                                       "blocked_by:"):
                            if m.startswith(prefix):
                                key = prefix.rstrip(":").replace("tier1:", "")
                                reason_breakdown[key] = reason_breakdown.get(key, 0) + 1
                                break
                    if len(sample) < 15:
                        sample.append({
                            "id": fid, "subject": (subj or "")[:80],
                            "sender": sender, "tag": f"{dom}/{act}",
                        })
            write_summary(
                _ru,
                geprueft=len(mail_ids),
                uebernommen=uebernommen_n,
                actionable=actionable_n,
                archive_silent=silent_n,
                reason_breakdown=reason_breakdown if reason_breakdown else None,
                worker_imports_sample=sample if sample else None,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("validator_batch summary failed (non-fatal): %s", e)

    # Review-followup A.3 2026-05-27: exit-code reflects lens-health.
    # Silent success (return 0) only if we actually produced opinions AND at
    # least half of configured voices loaded.
    loaded_count = sum(1 for s in voice_stats.values() if s.get("loaded"))
    configured_count = len(llm_voices)
    if total_opinions == 0:
        log.warning(
            "validator_batch returned without any lens output (%d/%d voices loaded)",
            loaded_count, configured_count,
        )
        return 1
    if configured_count > 0 and loaded_count / configured_count < 0.5:
        log.warning(
            "validator_batch: less than half of voices loaded (%d/%d) — exit 1",
            loaded_count, configured_count,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
