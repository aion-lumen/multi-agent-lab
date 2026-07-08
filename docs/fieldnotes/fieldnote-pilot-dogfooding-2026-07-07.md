# Field Note: Pilot-Dogfooding Mail-Triage (2026-07-07)

## Was gebaut wurde

- **Kategorien-Config:** `categories.yaml` + `categories.pilot-praxis.yaml`; `domain_actionability`, `validator_batch`, Worker gated
- **Fremdrechner-Entkopplung:** `password_env`/`password_cmd`, `--no-kanban`, `folio.db` auto-init, `LM_STUDIO_BASE_URL`
- **Preflight:** `scripts/preflight_pilot.py`, `make preflight`
- **Demo:** `demo_praxis.json` (25 fiktive DE-Mails), `make demo-pilot`
- **PILOT.md** + Netz-Capability-Audit
- **Folio Heute-Hub:** `triageTodayByDomain` + actionable-Liste in CardMail
- **Vault P6:** Abschlusskriterien Standalone-Install + Demo-Datensatz in `obj-02-07`

## Reifegrad — Pilotstart August?

**Ehrliche Einschätzung: August = machbar als begleiteter Pilot, nicht als «einfach installieren und vergessen».**

| Kriterium | Stand |
|-----------|-------|
| Config-driven Domains | ✅ In-place, getestet |
| Offline Demo (Praxis) | ✅ `make demo-pilot` |
| Install-Doku | ✅ PILOT.md < 30 Min Ziel |
| Zweitrechner live verifiziert | ❌ Noch Afschin (DoD) |
| Tägliches Dogfooding `mirhamed` | 🟡 Technisch ready, Nutzung offen |
| Validator/LLM auf Fremdrechner | 🟡 LM Studio Pflicht, Modell-Setup ~15 Min |

**Empfehlung CV-Session:** August-Zusage mit Formulierung *«begleiteter Pilot, erste Woche Remote-Setup»* — Oktober wenn zweiter Rechner + 2 Wochen Dogfooding-Daten vorliegen sollen.

## Antwort-Entwurf (CV-Session)

> Die Pipeline ist jetzt pilot-fähig im bestehenden Repo: Kategorien per Config (Praxis-Preset mit 4 Domains), Install-Pfad unter 30 Min dokumentiert, Demo mit synthetischen Praxis-Mails. Ich dogfoode über `afschin@mirhamed.ch` — Triage erscheint im Folio-Heute-Hub.
>
> August als Starttermin: **ja, unter der Bedingung** eines kurzen gemeinsamen Setup-Termins (LM Studio + Config). Vollständige Selbstständigkeit ohne Begleitung würde ich eher ab September empfehlen, sobald der Zweitrechner-Install bei mir durch ist.

## Offen (Afschin)

- [ ] Install auf echtem Zweitrechner + Screenshot
- [ ] Tägliche `mirhamed`-Triage eine Woche
- [ ] Browser-Check Heute-Hub mit echten Tagesdaten
