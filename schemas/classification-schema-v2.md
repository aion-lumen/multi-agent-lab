# Email-Klassifikations-Schema v2

**Status:** aktiv ab Phase 3.5a (2026-05-09)
**Vorgänger:** v1 (Phase 3) — `newsletter_business` als Phantom-Bucket identifiziert
**Quelle:** Phase-3.5-pre-Ground-Truth-Auswertung (15 Mails, 9/14 Genauigkeit)

## Änderungen ggü. v1

| Aspekt | v1 (Phase 3) | v2 (Phase 3.5a) |
|---|---|---|
| Anzahl Kategorien | 6 | 5 |
| `newsletter_business` | aktiv | **gestrichen** (User klassifizierte 0×, wird in `geschaeftspost`/`privat` aufgelöst) |
| `spam` Definition | „ungewollt" (zu breit) | **geschärft** auf Phishing/Scam mit Schaden-Potenzial |
| `geschaeftspost` Scope | Geschäftskommunikation | **erweitert** um Service-Notices (Account-Änderungen, App-Passwörter) |
| Eskalations-Trigger | `confidence < 0.7` | (in Phase 3.5b) **Disagreement-basiert**, nicht Confidence-basiert |

## Kategorien

### `werbung`

Klare Marketing-, Sales-, Promotions-Mails. Phase-3-Pilot: 100% Worker-Genauigkeit (2/2).

**Erkennungs-Indikatoren:**
- Rabatt-Codes, Sale-Push, „Jetzt kaufen"-CTAs
- Mass-Mailing-Charakter (HTML-Tracking-Pixel, mehrsprachige Footer)
- Sender ist Marketing-Domain (newsletter@, marketing@, deals@, promo@, offers@, news@)

**Kein werbung:**
- Service-Notices über bestehende Verträge → `geschaeftspost`
- Persönliche Kauf-Empfehlungen von Bekannten → `privat`

### `geschaeftspost`

Direkte Geschäftskommunikation und Service-Notices. Phase-3-Pilot: 78% User-GT-Genauigkeit (7/9).

**Erkennungs-Indikatoren:**
- Bestellbestätigungen, Versandbenachrichtigungen, Lieferungen
- Rechnungen, Zahlungsbelege, Abrechnungen
- Service-Notices: Account-Änderungen, App-Passwörter, Sicherheits-Warnungen, Login-Notifications
- Transaktionale Mails von bekannten Service-Plattformen (Amazon, PayPal, Stripe, DHL, etc.)
- Antworten von Geschäftspartnern auf eigene Anfragen (Makler, Dienstleister)

### `privat`

Persönliche Korrespondenz. Phase-3-Pilot: 0% Worker-Genauigkeit (0/2) — **Heuristik-Bedarf.**

**Erkennungs-Indikatoren:**
- Sender ist natürliche Person (Vorname + Nachname @ irgendeine Domain)
- Sender-Domain in `private_senders` aus dynamischer Heuristik
- An User direkt adressiert (kein Mass-Mailing-Charakter)
- Themen: persönliche Themen, Antwort auf eigene Anfrage, Familie/Freunde
- NGO-Newsletter mit persönlichem Ton (z.B. „I represent…")

**Kritischer Hinweis:** **Heuristik dominiert LLM.** Sender-Heuristik-Match (`sender_heuristic.heuristic_classify()`) entscheidet vor LLM-Inhalt.

### `spam`

**Schmal definiert:** Phishing, Scam, ungewollte Inhalte mit aktivem Schaden-Potenzial.

**Erkennungs-Indikatoren:**
- Sender-Spoofing-Indikatoren (display name ≠ from address)
- Body-Pattern: „Klicken Sie sofort", „Account suspended", „Crypto-Trading-Gewinne", „erbgesetzliche Vorteile"
- Deutliche Abweichung vom legitimen Service-Stil
- Verdächtige Anhänge oder verkürzte URLs

**NICHT spam:**
- Legitime Newsletter (auch wenn ungewollt) → `werbung`
- Service-Mails von echten Anbietern → `geschaeftspost`
- Phase-3-Befund: Worker hat SAMS-ON-Passwort-Reset und Tor-Newsletter fälschlich als spam klassifiziert

### `unklar`

Eskalations-Marker. Worker erkennt Mehrdeutigkeit:
- ≥ 2 Kategorien plausibel
- keine eindeutigen Indikatoren
- ungewohnter Sender ohne Heuristik-Match

→ Triggert Routing zu Validator/Architect/User (in Phase 3.5b implementiert).

## Heuristik-Vorrang

Die `sender_heuristic.heuristic_classify(sender)`-Funktion läuft VOR dem LLM-Call. Wenn sie ein Ergebnis liefert (≠ `None`), wird das verwendet:

1. Marketing-Patterns auf Subdomain (`newsletter@`, `deals@`, …) → `werbung`
2. Dynamic `marketing_senders` (aus state/sender-heuristics.json) → `werbung`
3. Dynamic `private_senders` → `privat`
4. Static Service-Domains → `geschaeftspost`
5. Dynamic `service_senders` → `geschaeftspost`
6. Kein Match → an LLM weiterleiten (Executor-Profil)

## Worker-Output-Compliance

Worker-Output JSON-Schema bleibt wie in `docs/worker-output-schema.md` (Phase 1). `result.value` muss aus dem v2-Set sein:

```
werbung | geschaeftspost | privat | spam | unklar
```

Bei Verwendung der Heuristik: `evidence` enthält einen Eintrag mit `type: "rule_match"`, `rule: "sender_heuristic_static"` (oder `_dynamic`), `matches: 1`, plus den `reason`-String aus `heuristic_classify()`.
