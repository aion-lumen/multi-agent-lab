"""add_midrun_snapshot.py — temporäre running-Snapshots für Screenshots #3, #4.

Inseriert (add) bzw. entfernt (clean) zwei synthetische "running" Runs in
folio-demo.db damit das UI den Mid-State zeigt:

  Shot #3 — Pipeline mid-validator-run
    worker_run mit mode='validator', status='running'
    + partielle validated-Logs (5+5+3 → gemma FERTIG, qwen FERTIG,
      qwen-thinking LÄUFT 3/5)
    → ModelStatusPanel zeigt WARTET/LÄUFT/FERTIG cards

  Shot #4 — Pipeline lens-run
    Schreibt ~/.council/lens-run.lock + ein fake lens-log
    damit getLensRunStatus running=true zurückgibt
    → ModelStatusPanel switched auf lens-stack mit 3 persona cards

Beide Snapshots sind „demo-midrun-*"-prefixed, lassen sich sauber per --clean
entfernen. Lockfile-Cleanup ist Best-Effort.

Usage:
  python3 scripts/add_midrun_snapshot.py --add   # insert + create
  python3 scripts/add_midrun_snapshot.py --clean # delete + remove
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FOLIO_DB = Path.home() / ".folio" / "folio-demo.db"

# Council-Lens fake lockfile + log paths must match COUNCIL_LENS_CONFIG in
# folio/src/lib/council/lens-config.server.ts:12-18.
_COUNCIL_LOCKFILE = Path.home() / ".council" / "lens-run.lock"
_COUNCIL_LOG_DIR = _REPO_ROOT.parent / "council" / "data"
_COUNCIL_LOG_PREFIX = "lens-run-ui"  # per folio/src/lib/council/lens-config.server.ts

MIDRUN_VALIDATOR_UUID = "demo-midrun-validator-snapshot"
MIDRUN_LENS_UUID = "demo-midrun-lens-snapshot"


def add_validator_midrun(folio: sqlite3.Connection) -> None:
    now = datetime.now()
    started = now - timedelta(minutes=4)
    started_iso = started.strftime("%Y-%m-%d %H:%M:%S")
    log_start = started + timedelta(seconds=30)

    folio.execute(
        """INSERT INTO worker_runs
               (run_uuid, parent_run_uuid, account, board, mode, tranche_size,
                pid, status, started_at, mails_processed)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (MIDRUN_VALIDATOR_UUID, "demo", "last-tranche", "validator", 5,
         12999, "running", started_iso, 0),
    )

    # Partial logs: gemma 5/5, qwen 5/5, qwen-thinking 3/5 → last voice =
    # qwen-thinking → state LÄUFT.
    sequences = (
        [("gemma", uid) for uid in [1, 2, 3, 4, 5]]
        + [("qwen", uid) for uid in [1, 2, 3, 4, 5]]
        + [("qwen-thinking", uid) for uid in [1, 2, 3]]
    )
    for seq, (voice, mail_id) in enumerate(sequences, start=1):
        recorded = (log_start + timedelta(seconds=seq * 12)).strftime("%Y-%m-%d %H:%M:%S")
        folio.execute(
            """INSERT INTO worker_run_logs
                   (run_uuid, seq, recorded_at, voice, mail_id, event_type, message, level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (MIDRUN_VALIDATOR_UUID, seq, recorded, voice, mail_id,
             "validated", f"#{mail_id} → immo/actionable", "info"),
        )


def add_lens_midrun(pid: int) -> bool:
    """Returns True if lens midrun was set up, False if council/ dir missing.

    `pid` must be a long-lived process — the Playwright test runner usually.
    `os.getpid()` would die when this helper exits, getLensRunStatus would
    auto-clean the stale lockfile and report running=false. We pass in the
    parent test process PID via --pid.
    """
    if not _COUNCIL_LOG_DIR.exists():
        print(f"  WARN: {_COUNCIL_LOG_DIR} not present — skipping lens midrun",
              file=sys.stderr)
        return False

    now = datetime.now()
    started = now - timedelta(minutes=14)

    _COUNCIL_LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    _COUNCIL_LOCKFILE.write_text(json.dumps({
        "pid": pid,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "domain": "council",
    }))

    # Fake lens log — three personas, first done, second evaluating, third
    # pending. Format matches log-parser.ts:1-15 expectations.
    def ts(offset_s: int) -> str:
        return (started + timedelta(seconds=offset_s)).strftime("%Y-%m-%d %H:%M:%S,000")

    log_lines = [
        f"{ts(0)} [INFO] === council_lens_run start ===",
        f"{ts(1)} [INFO] Personas: ['lens-baumeister', 'lens-rechner', 'lens-ortskundige'] | Candidates: 6 | no_llm=False",
        # Baumeister — done
        f"{ts(5)} [INFO] --- persona lens-baumeister (model=qwen3-30b-a3b-thinking-2507) ---",
        f"{ts(8)} [INFO] model swap: unload all → load qwen3-30b-a3b-thinking-2507",
        f"{ts(38)} [INFO] wait_for_lens_model_loaded: qwen3-30b-a3b-thinking-2507 confirmed loaded",
        f"{ts(380)} [INFO]   scored=6 ranked=6 beobachten=2 verworfen=1 skipped=0 cmp=15",
        # Rechner — evaluating (loaded but no scored= line yet)
        f"{ts(390)} [INFO] --- persona lens-rechner (model=qwen3.6-35b-a3b-ud-mlx) ---",
        f"{ts(395)} [INFO] model swap: unload all → load qwen3.6-35b-a3b-ud-mlx",
        f"{ts(440)} [INFO] wait_for_lens_model_loaded: qwen3.6-35b-a3b-ud-mlx confirmed loaded",
        # Ortskundige — pending (no --- persona line yet)
    ]
    log_path = _COUNCIL_LOG_DIR / f"{_COUNCIL_LOG_PREFIX}-demo-midrun.log"
    log_path.write_text("\n".join(log_lines) + "\n")
    return True


def clean_validator_midrun(folio: sqlite3.Connection) -> None:
    folio.execute("DELETE FROM worker_run_logs WHERE run_uuid = ?", (MIDRUN_VALIDATOR_UUID,))
    folio.execute("DELETE FROM worker_runs WHERE run_uuid = ?", (MIDRUN_VALIDATOR_UUID,))


def clean_lens_midrun() -> None:
    try:
        if _COUNCIL_LOCKFILE.exists():
            _COUNCIL_LOCKFILE.unlink()
    except Exception:
        pass
    log_path = _COUNCIL_LOG_DIR / f"{_COUNCIL_LOG_PREFIX}-demo-midrun.log"
    try:
        if log_path.exists():
            log_path.unlink()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--folio-db",
                    default=os.environ.get("FOLIO_DB_PATH", str(_DEFAULT_FOLIO_DB)))
    ap.add_argument("--pid", type=int, default=None,
                    help="Long-lived PID to write into lens lockfile (Playwright "
                         "runner PID). Defaults to caller's PID if omitted, but that "
                         "will be stale once this helper exits.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--add", action="store_true", help="Insert both validator + lens midrun")
    g.add_argument("--add-validator", action="store_true", help="Insert validator midrun only")
    g.add_argument("--add-lens", action="store_true", help="Insert lens midrun only")
    g.add_argument("--clean", action="store_true", help="Remove all midrun snapshots")
    args = ap.parse_args()

    folio = sqlite3.connect(args.folio_db)
    try:
        if args.add or args.add_validator:
            clean_validator_midrun(folio)
            add_validator_midrun(folio)
            folio.commit()
            print(f"Validator midrun ADDED: run_uuid={MIDRUN_VALIDATOR_UUID}")
            print(f"  status=running, 13 partial validated logs")
        if args.add or args.add_lens:
            clean_lens_midrun()
            lock_pid = args.pid if args.pid else os.getppid()
            lens_ok = add_lens_midrun(lock_pid)
            if lens_ok:
                print("Lens midrun ADDED: lockfile + log written")
                print("  baumeister=done, rechner=evaluating, ortskundige=pending")
            else:
                print("Lens midrun SKIPPED (council/data dir missing)")
        if args.clean:
            clean_validator_midrun(folio)
            clean_lens_midrun()
            folio.commit()
            print("Midrun snapshots CLEANED.")
    finally:
        folio.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
