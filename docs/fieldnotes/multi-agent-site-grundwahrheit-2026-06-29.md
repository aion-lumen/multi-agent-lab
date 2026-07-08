# Multi-Agent — ground truth for the site section (2026-06-29)

Research-only (directive `direktive-cc-recherche-multiagent-grundwahrheit-2026-06-29`).
Repo: `~/Projects/aion-lumen/multi-agent` (git, branch `main`). Every claim sourced to
a file:line / path / command. "unverified" = could not confirm on this machine. No
private-vault data used (counts only, never mail contents).

## §1 · The real production path
**Finding — NOT a bypass.** `scripts/production_worker.py` `process_envelope()` (:668)
runs, per mail:
1. `call_plugin(task_id)` → subprocess `~/.hermes/plugins/email-classification/cli.py`
   (call site :710, impl :365) = the **Hermes Worker-LLM** (qwen plugin executor).
2. `classify_immo(...)` (:718) — deterministic Python heuristic (`immo_heuristic.py`).
3. `classify_domain_actionability(...)` (:730) — deterministic 2-axis
   Domain×Actionability (`domain_actionability.py`).
4. `_call_telegram(...)` (:742) — human decision, 1 h timeout.
5. `write_feedback_row(...)` (:799) → `state/feedback.db`.

So the LLM and the rules engine run **side by side**, then a human confirms. The
deterministic heuristics do not replace the LLM; they are a second signal.

**Models** (`config/regelwerk.yaml` E4 voices + README): heuristic = **no LLM**
(`lm_studio_model: null`, :322); plugin executor = **qwen3.6-35b-a3b-ud-mlx**;
validator (separate stage) = 3 lenses — **gemma-4-26b-a4b-it-mlx** (gemma-control),
qwen3.6-35b (qwen35b-lens), qwen3-30b-a3b-thinking-2507 (qwen-validator) (:325-338).

**Plain words:** "For each incoming mail, a local LLM and a rules engine each propose a
category; you confirm or correct it in one tap; every decision is logged on-device."

**Site-usable claim:** *"A local LLM proposes, a deterministic rules engine
cross-checks, a human confirms — every step on your machine."*

## §2 · The "200+ real mail runs" number
**Finding — the real figure is 550.** `state/feedback.db` table `feedback` = **550
rows**, every one with a human `user_final_action`. Each row = one real mail
(`task_id, imap_uid, plugin_value, plugin_confidence, heuristic_*, user_final_action,
domain, actionability, created_at, …`).

- `user_final_action`: move_immo_portal 222 · keep 181 · move_paketzustellung 57 ·
  move_zu_pruefen 26 · move_immo_privat 24 · actionable 16 · archive-silent 16 ·
  archive 5 · uebernommen 3 → ~329 move-decisions, 181 keep, ~21 archive.
- `domain`: immo 238 · job 92 · shopping 70 · werbung 48 · unsorted 38 · kontakt 33 ·
  finance 29 · system 2.
- Window: `created_at` ISO values from **2026-06-08** onward (June 2026). A few rows
  hold an RFC-format date in `created_at` — minor data-quality outliers, not a
  different time window.
- **What actually happened in production:** classified + human-triaged for all 550.
  "Moved" — the decisions are `move_*` labels; the physical IMAP move is
  `imap_actions.py` / `imap_cleanup.py` ("optional IMAP cleanup", README). Full
  end-to-end IMAP execution is mode-dependent → the safe public phrasing is
  "classified and triaged".
- Validator side (`folio.db`: `validator_opinions`, `worker_run_logs`): **`folio.db`
  not found on this machine** (searched `Projects` / `.local` / `Shared`, depth 4) →
  validator production run-count **unverified here**.

**Site-usable claim:** *"550+ real emails classified and triaged in daily use
(June 2026)"* — sourced to `feedback.db`. The earlier "200+" understates it.

## §3 · Screenshots capturable from the DEMO vault today
**Demo assets present:** `scripts/seed_pipeline_demo.py`, `scripts/init_demo_dbs.sh`,
`state/feedback-demo.db` (40 rows), `tests/fixtures/imap/demo_quickstart.json`,
`scripts/fetch_demo_photos.py`. Dry-run (README quickstart):
`python scripts/production_worker.py --dry-run --no-telegram` against the fixture —
no IMAP, no Telegram, no PII.

**Existing release shots** in `folio/docs/screenshots/release/` (dated 2026-06-11):
01-mail-queue, 02-pipeline-idle, 03-pipeline-validator, 04-pipeline-lens,
05-verlauf-detail, 06-council, 07-hauskauf, 08-heute. ⚠ **Provenance unverified — they
may contain real mail. Verify PII-free or RE-CAPTURE from the demo before any site
use.**

**Capturable (neutral, from demo):**
1. Validator three-voice cards (gemma-control · qwen35b-lens · qwen-validator, blind /
   Delphi — no voice sees another's verdict) — folio pipeline UI on a demo run
   (cf. 03-pipeline-validator).
2. Run detail — per-mail block reasons (out_of_corridor, decay, projektiert,
   price_on_request) (cf. 05-verlauf-detail).
3. Domain × Actionability triage — rendered from `feedback-demo.db`.
4. Heuristic worker dry-run CLI output — fixture `demo_quickstart.json`.

**Excluded (needs private data → off-site):** live mail-queue with real senders/
subjects; anything rendered from `state/feedback.db` (real mail); Council
(06-council, off by decision).

## §4 · The roles
**Finding — narrative device.** "Tidyler / Fact-Finder / Judge" appear **nowhere** in
the code (`grep -rniE` across `.py`/`.yaml`/`.md` = 0 hits). Real components: heuristic
worker (`domain_actionability.py` + `immo_heuristic.py`), three-voice LLM validator
(`validator_batch.py`, lenses gemma + 2×qwen), auto-promotion ("Auto-Übernahme nach
Vier-Stimmen-Vollkonsens", `regelwerk.yaml:56`).

**Cross-family validation (Qwen + Gemma, swap on 48 GB) — REAL.** `scripts/model_swap.py`
docstring: "48GB RAM doesn't fit two LLMs of this size at once" → `swap_to()` unloads
one and loads the next between lenses; the validator lenses deliberately mix the gemma
and qwen families. Honest and technically interesting → **worth surfacing** as a
workshop detail (a real hardware constraint solved), not a boast.

**Site-usable claim:** *"The three figures are an illustration. The real system is a
rules engine plus three blind LLM voices across two model families that must agree
before anything is auto-applied — run on a single 48 GB machine by swapping models."*

## §5 · Roadmap vs shipped + Council
- **Shipped (production; evidenced by feedback.db = 550 + code wiring):** per-mail
  LLM + heuristic classification, Domain×Actionability, human Telegram triage,
  feedback logging.
- **Built / verify volume before claiming "in use":** the 3-voice validator +
  auto-promotion + IMAP move/cleanup (`validator_batch.py`, `imap_actions.py`,
  `imap_cleanup.py`, one release screenshot) — production run-count **unverified here**
  (folio.db absent). Present as "built, in limited use" unless counts are confirmed.
  Learning loops (`marketing_learner.py`, `sender_learner.py`) — maturity unverified →
  treat as roadmap.
- **Council:** an **entirely separate** system (`aion-lumen/council`, own README
  "Aion-Lumen V2", own `council.db`, own UI) — not the mail pipeline. Two-user
  real-world testing: **no evidence found → unverified.** Stays **OFF the site** (per
  decision and honesty).

## Screenshots worth capturing (next physical step — demo only)
1. Validator three-voice cards (demo run, folio pipeline UI).
2. Run detail with block reasons (demo run).
3. Domain × Actionability triage (from `feedback-demo.db`).
4. Heuristic dry-run CLI output (fixture `demo_quickstart.json`).

Re-capture fresh from the demo; do not reuse the 2026-06-11 release shots unless
verified PII-free.

---
*Prohibitions honored: read-only, no code/commit, no private-vault data, no invented
numbers (folio.db / validator volume / Council two-user = unverified), roadmap not
softened into shipped.*
