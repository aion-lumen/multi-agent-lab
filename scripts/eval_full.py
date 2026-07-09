#!/usr/bin/env python3
"""eval_full.py — automated 3-model end-to-end eval.

Runs the 40 demo mails through the LLM classification lens (the validator voices)
per model and scores each model against the Golden Labels (demo_labels.yaml,
`classifier_action`). The **report file** is the deliverable / Cowork interface —
not stdout. No real IMAP, no real mails (Demo-Korpus only).

Model handling (Aufgabe 3, no new lms wrapper):
  --mode jit   : one pass, LM Studio serves each model on request (JIT loading).
  --mode swap  : loop over model_swap.swap_to() (loads/unloads each model).
Reuses: validator_batch.call_lens_lm_studio / load_user_context / load_regelwerk,
model_swap.swap_to. Golden labels = demo_labels.yaml. Corpus = demo_quickstart.json.

Exit codes: 0 = at least one model produced results; 1 = all models failed / no data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import yaml  # noqa: E402
from validator_batch import (  # noqa: E402
    call_lens_lm_studio,
    load_regelwerk,
    load_user_context,
)

FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "imap" / "demo_quickstart.json"
LABELS = _REPO_ROOT / "tests" / "fixtures" / "imap" / "demo_labels.yaml"
OUT_DIR = _REPO_ROOT / "evals" / "full"


def load_mails(limit: int | None = None) -> list[dict]:
    envs = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for e in envs:
        rows.append(
            {
                "id": e["uid"],
                "imap_uid": e["uid"],
                "sender": f'{e.get("from_name", "")} <{e.get("from_addr", "")}>'.strip(),
                "subject": e.get("subject", ""),
                "body_excerpt": (e.get("body_text") or "")[:1000],
            }
        )
    rows.sort(key=lambda r: r["imap_uid"])
    return rows[:limit] if limit else rows


def load_golden() -> dict[int, tuple[str, str]]:
    """{uid: (domain, classifier_action)} — the eval-full measure (uebernommen→actionable)."""
    raw = yaml.safe_load(LABELS.read_text(encoding="utf-8")) or {}
    out: dict[int, tuple[str, str]] = {}
    for uid, v in (raw.get("labels") or {}).items():
        out[int(uid)] = (str(v["domain"]), str(v.get("classifier_action") or v["action"]))
    return out


def models_from_regelwerk(regelwerk: dict) -> list[tuple[str, str, str]]:
    voices = (regelwerk.get("voice_consensus") or {}).get("voices") or []
    return [
        (v.get("id"), v["lm_studio_model"], v.get("response_strip") or "code_fence")
        for v in voices
        if v.get("lm_studio_model")
    ]


def ensure_model(model_id: str, mode: str) -> bool:
    if mode == "jit":
        return True  # LM Studio auto-loads on request
    from model_swap import swap_to  # local import: only needed in swap mode

    return swap_to(model_id)


def eval_model(model_id, strip, rows, golden, user_context, regelwerk) -> dict:
    per_mail: list[dict] = []
    n = correct = domain_ok = action_ok = fp = 0
    confusion: dict[str, int] = {}
    for row in rows:
        uid = row["imap_uid"]
        gold = golden.get(uid)
        if gold is None:
            continue
        gd, ga = gold
        op = call_lens_lm_studio(
            row, user_context, regelwerk, model_id=model_id, response_strip=strip
        )
        if op is None:
            per_mail.append({"uid": uid, "error": "lens returned None"})
            continue
        pd, pa = op.get("domain"), op.get("actionability")
        n += 1
        d_ok, a_ok = pd == gd, pa == ga
        both = d_ok and a_ok
        domain_ok += d_ok
        action_ok += a_ok
        correct += both
        if pa == "actionable" and ga != "actionable":
            fp += 1
        if not both:
            key = f"{gd}/{ga} -> {pd}/{pa}"
            confusion[key] = confusion.get(key, 0) + 1
            per_mail.append({"uid": uid, "expected": f"{gd}/{ga}", "got": f"{pd}/{pa}"})
    return {
        "model": model_id,
        "n": n,
        "accuracy": round(correct / n, 4) if n else None,
        "domain_accuracy": round(domain_ok / n, 4) if n else None,
        "action_accuracy": round(action_ok / n, 4) if n else None,
        "fp_rate": round(fp / n, 4) if n else None,
        "confusion_pairs": confusion,
        "mismatches": per_mail,
    }


def write_report(out_dir: Path, stamp: str, payload: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stamp}-report.json"
    md_path = out_dir / f"{stamp}-report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Full-Eval Report — {stamp}",
        "",
        "**Interne Betriebskennzahl** (40 Demo-Mails × Domain×Actionability gegen Golden Labels).",
        "NICHT die öffentliche Site-Zahl — die bleibt die Import-Triage-Accuracy (14 Fixtures).",
        "",
        f"- Korpus: `{payload['corpus']}` ({payload['n_mails']} Mails)",
        f"- Labels: `{payload['labels_source']}`",
        f"- Modus: `{payload['mode']}` · generiert: {payload['generated_at']}",
        "",
        "| Modell | n | Accuracy | Domain-Acc | Action-Acc | FP-Rate |",
        "|--------|---|----------|-----------|-----------|---------|",
    ]
    for r in payload["models"]:
        if "accuracy" not in r or r["accuracy"] is None:
            lines.append(f"| {r['model']} | — | ERROR: {r.get('error', 'n/a')} | | | |")
            continue
        lines.append(
            f"| {r['model']} | {r['n']} | {r['accuracy']:.3f} | "
            f"{r['domain_accuracy']:.3f} | {r['action_accuracy']:.3f} | {r['fp_rate']:.3f} |"
        )
    for r in payload["models"]:
        mm = r.get("mismatches") or []
        if mm:
            lines += ["", f"### Abweichungen — {r['model']}"]
            for m in mm:
                if "error" in m:
                    lines.append(f"- uid {m['uid']}: {m['error']}")
                else:
                    lines.append(f"- uid {m['uid']}: erwartet `{m['expected']}`, bekam `{m['got']}`")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> int:
    ap = argparse.ArgumentParser(description="3-Modell E2E-Eval → Report-Datei.")
    ap.add_argument("--models", help="Komma-Liste von lm_studio_model-IDs (default: regelwerk-Voices)")
    ap.add_argument("--limit", type=int, default=None, help="nur erste N Mails (Verify schlank)")
    ap.add_argument("--mode", choices=["jit", "swap"], default="swap",
                    help="jit=ein Pass (LM Studio lädt on-request), swap=model_swap-Schleife")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--stamp", default=None, help="Datum-Stempel für den Report (default: heute UTC)")
    args = ap.parse_args()

    user_context = load_user_context()
    regelwerk = load_regelwerk()
    voices = models_from_regelwerk(regelwerk)
    if args.models:
        want = {m.strip() for m in args.models.split(",")}
        voices = [(vid, m, s) for (vid, m, s) in voices if m in want]
    if not voices:
        print("Keine Modelle zu evaluieren (regelwerk-Voices leer / --models filtert alles).", file=sys.stderr)
        return 1

    rows = load_mails(args.limit)
    golden = load_golden()
    print(f"eval-full: {len(voices)} Modell(e) × {len(rows)} Mails, mode={args.mode}", file=sys.stderr)

    results = []
    for vid, model_id, strip in voices:
        print(f"  → {vid} ({model_id}) …", file=sys.stderr)
        if not ensure_model(model_id, args.mode):
            results.append({"model": model_id, "error": "model load failed (swap_to)"})
            continue
        results.append(eval_model(model_id, strip, rows, golden, user_context, regelwerk))

    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": FIXTURE.name,
        "labels_source": LABELS.name,
        "n_mails": len(rows),
        "mode": args.mode,
        "models": results,
    }
    md_path, json_path = write_report(args.out_dir, stamp, payload)
    print(f"Report: {md_path}\n        {json_path}", file=sys.stderr)

    ok = any(r.get("accuracy") is not None for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
