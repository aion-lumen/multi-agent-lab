#!/usr/bin/env python3
"""job_digest.py — Lokaler Job-Mail-Digest aus feedback.db → Vault-Markdown.

Liest domain='job' mit actionability='actionable', schreibt nach
{VAULT_PATH}/internal/mail/job-digest.md. Kein LLM, kein Netzwerk.

Usage:
  VAULT_PATH=~/Projects/life python3 scripts/job_digest.py
  python3 scripts/job_digest.py --limit 50 --dry-run
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import FEEDBACK_DB

DEFAULT_LIMIT = 50


def vault_mail_dir() -> Path:
    vault = Path(os.environ.get("VAULT_PATH", str(Path.home() / "Projects" / "life")))
    out = vault / "internal" / "mail"
    out.mkdir(parents=True, exist_ok=True)
    return out


def fetch_job_mails(limit: int) -> list[sqlite3.Row]:
    if not FEEDBACK_DB.exists():
        raise FileNotFoundError(f"feedback.db nicht gefunden: {FEEDBACK_DB}")
    conn = sqlite3.connect(FEEDBACK_DB)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT id, mail_date, created_at, sender, subject, body_excerpt,
                   actionability, account_id
            FROM feedback
            WHERE domain = 'job' AND actionability = 'actionable'
            ORDER BY COALESCE(mail_date, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def render_digest(rows: list[sqlite3.Row], limit: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "---",
        "type: mail-digest",
        "layer: internal",
        "status: active",
        "tags: [mail, job, digest, jobsuche]",
        f"updated: {datetime.now().strftime('%Y-%m-%d')}",
        "summary: Lokaler Job-Mail-Digest aus feedback.db (domain=job, actionable)",
        "---",
        "",
        "# Job-Mail-Digest",
        "",
        f"> Generiert: {now} · Quelle: `{FEEDBACK_DB}` · Limit: {limit}",
        "> Manuell: `VAULT_PATH=~/Projects/life python3 scripts/job_digest.py`",
        "",
        f"**{len(rows)}** actionable Job-Mails (neueste zuerst).",
        "",
    ]
    if not rows:
        lines.append("_Keine actionable Job-Mails in feedback.db._")
        lines.append("")
        return "\n".join(lines)

    for r in rows:
        received = r["mail_date"] or r["created_at"] or "—"
        sender = (r["sender"] or "—").strip()
        subject = (r["subject"] or "(kein Betreff)").strip()
        excerpt = (r["body_excerpt"] or "").strip().replace("\n", " ")
        if len(excerpt) > 280:
            excerpt = excerpt[:277] + "…"
        lines.extend(
            [
                f"## {received} — {subject}",
                "",
                f"- **Absender:** {sender}",
                f"- **Account:** {r['account_id'] or '—'}",
                f"- **feedback_id:** {r['id']}",
                "",
            ]
        )
        if excerpt:
            lines.append(f"> {excerpt}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Job-Mail-Digest → Vault internal/mail/")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--dry-run", action="store_true", help="nur stdout, nicht schreiben")
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output path (default: VAULT_PATH/internal/mail/job-digest.md)",
    )
    args = ap.parse_args()

    try:
        rows = fetch_job_mails(args.limit)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    content = render_digest(rows, args.limit)
    out_path = args.output or (vault_mail_dir() / "job-digest.md")

    if args.dry_run:
        print(content)
        print(f"\n# would write: {out_path}", file=sys.stderr)
        return 0

    out_path.write_text(content, encoding="utf-8")
    print(f"Wrote {len(rows)} entries → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
