# Field-Note — P0.4 Yahoo-Struktur-Move + Credential-Auflösung (2026-07-12)

## Kern-Lehre: der produktive Mail-Pfad hing an bw, nicht an accounts.toml

Vier Debugging-Anläufe gingen an einer falschen Annahme verloren („BW_SESSION gültig
geerbt" → „Folio läuft nie stale weil accounts.toml"). Read-only belegt war das Gegenteil:

- `bw get --session "$BW_SESSION"` UND `life-mail-passwd` liefern in CC beide **0 Bytes**
  (Vault gesperrt; Env-Token ≠ Datei-Token, beide stale).
- Folios `pipeline.py:138` baut `IMAPSession(..., bw_item=…)` **ohne `password=`** → hängt
  genauso an bw. Reproduzierter Live-Fehler: `[AUTHENTICATIONFAILED] Invalid credentials`
  = bw gab **leeres** Passwort, Login mit `""` → Yahoo lehnt ab (sieht aus wie falsches
  Passwort, ist „bw leer"). Landet nur in stderr→SSE, kein Logfile → im Terminal unsichtbar.
- accounts.toml trug nur `bw_item` (kein Klartext); `_resolve_password` liest
  `password_env`/`password_cmd` — kein `password`-Feld. Beides für yahoo unkonfiguriert.

**Auflösung:** Passwort in `~/.config/life/yahoo-imap-pass` (0600), accounts.toml
`password_cmd = "cat …"`. `account_creds.resolve_password` (Präzedenz cmd > env > bw_item)
als eine Wahrheit für Move-Pfad + Worker; pipeline.py analog → Folio bw-frei. bw = Fallback.

**Regel für 00-START-HIER:** Mail-Credentials Yahoo kommen aus `~/.config/life/yahoo-imap-pass`
via `password_cmd`; bw/life-mail-passwd ist optionaler, timeout-anfälliger Fallback.
Compliance-Härtung: Plaintext-Secret-Datei (600) dokumentieren, nicht committen.

## Yahoo-IMAP-Quirks (verifiziert, kosten sonst Stunden)

1. **`BODY.PEEK[HEADER.FIELDS (…)]` gibt bei Yahoo leeren Body** — feld-selektiver Fetch
   nicht unterstützt. → vollen `BODY.PEEK[HEADER]` holen, Felder client-seitig parsen.
   (Erst dadurch fanden sich List-Unsubscribe in 273/424 statt 0.)
2. **Yahoo lehnt Batch-COPY ab (`NO`) → per-UID-Fallback.** Für ~350 Mails sprengt das den
   2-Min-Foreground-Timeout → Move brach nach COPY/vor EXPUNGE ab → 170 Duplikate.
   **Lehre: große IMAP-Moves immer im Hintergrund** (kein Tool-Timeout).

## Klassifikations-Fix (Ursache statt Symptom)

Backlog-Rows vor Header-Erfassung klassifiziert → RFC-Signale 0/555 → auto-Erkennung
defaulted alles auf personal_keep (316). Fix: Header nachladen (read-only BODY.PEEK, kein
`\Seen`) + List-Unsubscribe primär, transaktionale Sender-Prefixe sekundär (bewusst NICHT
`service`/`info` — könnten 1:1-Antworten treffen). → personal_keep 316→50; die 4 echten
Makler-Antworten bleiben im Eingang (Hard-Kriterium bestanden).

## Recovery-Muster (Duplikate ohne Verlust)

Message-ID-Reconciliation gegen den Origin-Snapshot identifizierte die 170 Duplikate
(in INBOX ∩ Immo-neu). Statt EXPUNGE (blockiert vom Safety-Classifier, zu Recht) →
`move_to_trash` (recoverable 30 Tage). Kein endgültiges Löschen ohne User-Abgleich.

## Prozess-Lehre

Der Dry-Run hat sauber gestoppt und das echte Problem gezeigt (fehlende Datengrundlage,
nicht schlechte Erkennung). Gestufte Freigaben (Liste → Tranche 9 → Voll-Lauf) + Re-Inventur
nach jedem Schritt haben den Timeout-Schaden auf umkehrbare Duplikate begrenzt.
