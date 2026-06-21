# Email-Klassifikations-Schema v2.1 — Tie-Breaker-Regeln

**Status:** aktiv ab Phase 3.5c (2026-05-12)
**Vorgänger:** v2 (Phase 3.5a) — Kategorien stabil, aber 3× `schema_konflikt` in Phase-3.5b-Re-Run
**Quelle:** Phase-3.5b-Diagnose (6 Mismatches, 3× schema_konflikt — siehe `state/diagnose-mismatches-2026-05-10.csv`)

## Motivation

v2 definierte 5 Kategorien klar. Im Re-Run #2 (100 Mails) entstanden 6 Mismatches gegen 15 Ground-Truth-Labels:

| Diagnose-Kategorie | Anzahl | Beispiel |
|---|---|---|
| schema_konflikt | 3 | RASA-NGO-Mail (`newsletter_business` GT vs `werbung`/`privat` v2), Makler-Antwort (`privat` GT vs `geschaeftspost` Schema) |
| heuristik_falsch | 1 | Andreas Heinze (Einzelunternehmer-Fotograf, GT `privat`) als Service-Sender |
| heuristik_zu_breit | 1 | Polymarket (Subject-Pattern `smart money` 🐳, GT `werbung`) als Service-Sender |
| user_inkonsistent | 1 | Yahoo App-Passwort (GT `geschaeftspost`) per User-Override fälschlich `werbung` |

3/6 sind reine Schema-Definitions-Probleme. v2.1 schärft die Tie-Breaker, ohne neue Kategorien einzuführen.

## v2.1-Änderungen

Die 5 Kategorien bleiben unverändert. Vier explizite Tie-Breaker-Regeln werden ergänzt — sie sollen sowohl im LLM-Prompt als Hint als auch im Heuristik-Code (`heuristic_classify_v2`) Anwendung finden.

### Tie-Breaker 1: `privat` vs `geschaeftspost` (Einzelunternehmer-Fall)

Eine Mail ist **privat**, wenn ALLE drei Bedingungen erfüllt sind:

1. **Sender ist eindeutig natürliche Person.** Local-Part enthält Vorname und/oder Nachname (z.B. `andreas@...`, `max.mustermann@...`). Negativ-Liste der Local-Parts: `noreply`, `no-reply`, `info`, `service`, `system`, `admin`, `support`, `hello`, `notifications`, `news`, `marketing`, `deals`, `promo`, `contact`, `mail`, `postmaster`, `team`.
2. **Kein Mass-Mailing-Charakter.** Keine HTML-Tracking-Pixel, kein mehrsprachiger Footer, kein `unsubscribe`-Link, keine Tabellen-/Banner-Templates.
3. **Keine reine Firma-Antwort auf eigene Anfrage.** Wenn die Mail eine Geschäfts-Konversation fortsetzt (Makler antwortet auf Immobilien-Anfrage, Versicherung antwortet auf Schadensmeldung), ist es **`geschaeftspost`** — auch wenn der Absender persönlich unterschreibt.

**Konsequenz Phase 3.5b:** Andreas Heinze (Einzelunternehmer-Fotograf, persönliche Frage nach Rechnungsadresse) → erfüllt 1+2+3 → **`privat`**. Johanna Dosenbach (Makler-Antwort über Immobilienscout24-Anfrage) → erfüllt 1+2, scheitert an 3 → **`geschaeftspost`** (schema-konsistent).

### Tie-Breaker 2: `werbung` vs `geschaeftspost` (mehrdeutiger Sender)

Wenn ein Sender sowohl Service-Notices als auch Marketing verschickt (z.B. Polymarket, Booking.com, Check24), entscheidet das **Subject + Body-Pattern**:

**`werbung` wenn ≥1 Indikator:**
- Marketing-Vokabular im Subject: `smart money`, `deals`, `Rabatt`, `% off`, `% Rabatt`, `Sale`, `Aktion`, `Newsletter`, `Jetzt kaufen`, `Letzte Chance`, `Nur diese Woche`
- Emoji im Subject: 🐳 💰 🚀 🎉 🛍️ 🔥 💸 🎁
- Body enthält Push-CTAs („Klicken Sie jetzt", „Holen Sie sich…", „Sichern Sie sich…")
- Tracking-URL-Pattern (`/go/`, `?ref=marketing`, `?utm_campaign=`)

**`geschaeftspost` sonst** (Bestellbestätigung, Versand-Status, Account-Notice, Rechnung).

**Konsequenz Phase 3.5b:** Polymarket „Here's how you can copy smart money 🐳" → Subject enthält `smart money` + 🐳 + `click` → **`werbung`** (statt `geschaeftspost`).

### Tie-Breaker 3: `spam` schmal definiert

`spam` ist NUR für **Phishing/Scam mit aktivem Schaden-Potenzial**:

- Sender-Spoofing (display name ≠ from address)
- Body-Pattern: `Klicken Sie sofort`, `Account suspended`, `Crypto-Trading-Gewinne`, `erbgesetzliche Vorteile`, `won the lottery`, `urgent transfer`
- Verdächtige Anhänge oder verkürzte URLs (`bit.ly`, `tinyurl`, `t.co`)
- Sender-Domain ist offensichtlich Tippfehler (`amaz0n.de`, `paypa1.com`)

**NICHT spam:**
- Legitime, ungewollte Newsletter → `werbung`
- Service-Mails von echten Anbietern (auch wenn nervig) → `geschaeftspost`
- Phase-3-Fehler: SAMS-ON Passwort-Reset, Tor-Newsletter

### Tie-Breaker 4: NGO-Sonderfall

NGO-Newsletter (Tierschutz, Umwelt, Politik) sind Massen-Mailings, auch wenn der Ton persönlich ist:

- **NGO-Spenden-Mailing mit Mass-Mailing-Indikatoren** (mehrere Empfänger-Templates, „Liebe(r) Unterstützer:in", Spenden-CTA, unsubscribe-Link) → **`werbung`**
- Reine individuelle persönliche Mail eines NGO-Mitglieds an User → `privat`

**Konsequenz Phase 3.5b:** RASA Animal Shelter `<redacted@example.org>` „They Don't Understand War…" + „Keeping Them Safe…" → Spenden-Mass-Mailing → **`werbung`**. Validator hatte recht.

## Heuristik-Vorrang in v2.1

Reihenfolge von `heuristic_classify_v2(sender, subject)`:

1. Static Marketing-Pattern auf Subdomain (`newsletter@`, `deals@`, …) → `werbung`
2. Dynamic `marketing_senders` → `werbung`
3. Dynamic `private_senders` → `privat`
4. **NEU: Dynamic `ambiguous_senders`** mit Subject-Score:
   - Subject-Marketing-Score ≥ 1 → `werbung`
   - sonst → an LLM weiterleiten (`route_to_llm=True`)
5. Static Service-Domains:
   - Subject-Marketing-Score ≥ 2 → `werbung` (Override für klare Marketing-Mails von Service-Plattformen)
   - sonst → `geschaeftspost`
6. Dynamic `service_senders` (analog)
7. Kein Match → LLM (Executor → Validator → ggf. User)

## LLM-Prompt-Hint (Schema v2.1-Kurzfassung)

Production-Worker fügt im `PROMPT_TPL` einen kompakten Tie-Breaker-Hint hinzu (max ~10 Zeilen) — keine vollständige Schema-Doku im Prompt.

## Worker-Output-Compliance

Bleibt wie in v2:

```
result.value ∈ {werbung, geschaeftspost, privat, spam, unklar}
```

Bei Heuristik-Match-v2 mit `ambiguous_senders`-Routing wird `evidence` um den Eintrag `{"type": "rule_match", "rule": "sender_heuristic_v2_ambiguous", "matched_patterns": [...]}` ergänzt.
