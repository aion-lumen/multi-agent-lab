# Field-Note — Heuristik System-Domain 2-Listen-Pattern (2026-05-27)

**Direktive:** Review-Followup Phase C (review-2026-05-27 §5)
**Branch:** `feature/heuristik-systemdomain-2026-05-27`
**Scope:** `scripts/domain_actionability.py` — SYSTEM_DOMAINS strict + neue SYSTEM_DOMAIN_TOKENS

## Symptom-Klasse (latent)

Pre-Fix-Code in `_detect_domain`:
```python
for d in SYSTEM_DOMAINS:
    if domain == d or domain.endswith("." + d) or d in domain:
        ...
```

`or d in domain` ist Substring-überall. Aber SYSTEM_DOMAINS war gemischt aus echten TLDs (`anthropic.com`, `github.com`, `infoemail.microsoft.com`) und einem Brand-Token (`microsoftrewards`, kein TLD). Der Substring-Match war für `microsoftrewards` **gewollt** (MicrosoftRewards-Sender erkennen), für die TLDs **nicht gewollt** — aber alle vier wurden gleich behandelt.

**Trap-Sender (latent):**

| Sender | Pre-Fix | Begründung |
|---|---|---|
| `hr@anthropicfan.evil-spam.com` | system (falsch) | `"anthropic.com" in "anthropicfan.evil-spam.com"` — Substring überall |
| `noreply@notgithub.com` | system (falsch) | `"github.com" in "notgithub.com"` |
| `info@mygithub.io` | system (falsch) | `"github.com" in "mygithub.io"`? Nope — kein „github.com" als Substring. Aber `"github" in "mygithub"`? Wir nutzten `"github.com"` nicht `"github"`, also OK. Aber: ähnliche Konstellation bei künftigen Token-Erweiterungen wahrscheinlich. |
| `noreply@phishing-github.com.attacker.io` | system (falsch) | `"github.com" in "phishing-github.com.attacker.io"` |

**Latent-Status:** Im aktuellen 19-row-post-reset-Korpus **0 Kipper** (Audit-Report). Code-Fix ist prophylaktisch.

## Fix-Mechanik

### 2-Listen-Pattern

```python
SYSTEM_DOMAINS = (
    "anthropic.com",
    "infoemail.microsoft.com",
    "github.com",
)
SYSTEM_DOMAIN_TOKENS = (
    "microsoftrewards",  # intentional substring (Brand-Match)
)
```

In `_detect_domain`:
```python
for d in SYSTEM_DOMAINS:
    if domain == d or domain.endswith("." + d):
        markers.append(f"system:domain:{d}")
        return ("system", markers)
for t in SYSTEM_DOMAIN_TOKENS:
    if t in domain:
        markers.append(f"system:domain-token:{t}")
        return ("system", markers)
```

Marker-Unterscheidung: `system:domain:` (strict TLD) vs `system:domain-token:` (intentional substring). Audit + Debugging können die beiden Klassen sauber auseinanderhalten.

## Test

`scripts/test_system_domain_match.py` — 14 Asserts grün:
- 5 „DARF NICHT matchen" (Substring-Trap eliminiert für alle TLDs)
- 5 „MUSS matchen" via strict-TLD + Subdomain (`anthropic.com`, `www.anthropic.com`, `github.com`, `security.github.com`, `infoemail.microsoft.com`)
- 2 „MUSS matchen via Brand-Token" (`microsoftrewards`, `microsoftrewards.anything.example`)
- 2 End-to-End via `classify_domain_actionability()`:
  - `MicrosoftRewards@infoemail.microsoft.com` → `domain=werbung` (matched via TLD-Subdomain, plugin-class promoted)
  - `hr@anthropicfan.evil-spam.com` → `domain=kontakt` (war: system, vor Fix)

Plus Regression: subject 31/31 + sender-prefix 28/28 weiterhin grün.

## Bestandskorrektur

`scripts/audit_systemdomain_reclass.py` → **0 Kipper** im 19-row-post-reset-Korpus. Migration-Script entfällt. Audit-Trail in `state/systemdomain-reclass-report-2026-05-27.md`.

## Wiederkehrendes Muster — Substring ohne Wortgrenze (**4. Auftreten**)

Reihe wächst:

1. **2026-05-25 Lens-Swap** — `"qwen3-30b" in "qwen3-30b-thinking"`. Fix: exakt-Match auf model_id.
2. **2026-05-27 Subject-Keywords** — `"stelle" in "zuzustellen"`. Fix: word-boundary `\b…\b`.
3. **2026-05-27 Sender-Prefix** — `"info" in "linkedin-info"`. Fix: 3-stufige Match-Mechanik (exact + delimiter + segment ≥ 5).
4. **2026-05-27 System-Domain** (dieser Fix) — `"github.com" in "notgithub.com"`. Fix: **2-Listen-Pattern** (strict-TLD + explizite Brand-Token-Liste).

**Verschärfte Lesson für Future-Self:**

Bei jedem `x in y` in Token-Domänen fragen:
1. **Ist substring intended?** Wenn nein: word-boundary / exact / startswith-mit-delimiter / segment-split.
2. **Wenn substring intended ist:** durch separate, **explizit ausgewiesene Liste** mit klarem Marker-Naming dokumentieren. Niemals mischen mit strict-Listen.
3. **Marker-Namen tragen Semantik:** `system:domain:` vs `system:domain-token:` ist nicht Whim — Debug-Sessions und Audit-Reports brauchen die Trennung.

Niemals nackt `or x in y` ohne diese Frage beantwortet zu haben.

## Out of Scope

- Keine weiteren Substring-Spots in `domain_actionability.py` identifiziert (alle anderen Match-Stellen sind schon strict).
- 5-Zeichen-Schwelle aus Sender-Prefix-Fix bleibt unangetastet (Phase C berührt nur System-Domain).
