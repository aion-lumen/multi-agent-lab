# Field-Note: Watchdog-Bridge stillgelegt (2026-06-22)

**Direktive:** `~/Projects/direktive-2026-06-22.md` (Block C)
**Entscheidung:** Afshin — bewusst abstellen, archivieren statt löschen.

## Was

Der Telegram-Watchdog-Bot (`com.aionlumen.watchdog-bridge`) wurde stillgelegt.
Er pollte Kanban-`task_events` aus `~/.hermes/kanban/` und schickte
Eskalations-Alerts (`blocked`/`gave_up`/`crashed`) per Telegram (eigener
`HERMES_WATCHDOG_*`-Token). Ersetzt durch das eigene Web-/Mobile-UI →
Telegram-Kanal obsolet. Zuletzt ohnehin im Fail-Loop (Plist zeigte auf den
bereits nach `scripts/_archive/` verschobenen Script-Pfad, Exit 2).

## Aktionen

- `launchctl bootout gui/$UID/com.aionlumen.watchdog-bridge` — entladen.
- Plist verschoben: `~/Library/LaunchAgents/com.aionlumen.watchdog-bridge.plist`
  → `~/.hermes/archived/watchdog-bridge/` (nicht gelöscht, reboot-fest).
- Script verbleibt in `scripts/_archive/watchdog_bridge.py` (war schon dort).
- Archiv-`README.md` neben der Plist dokumentiert Funktion + Reaktivierung
  (3 Schritte). Token nicht zitiert.

## Nicht angefasst

- **Email-Feedback-Bot** (`scripts/feedback_telegram.py`, in-process im
  production_worker, KEIN LaunchAgent, Token `AION_EMAIL_FEEDBACK_*`).
- **ImmoAlert-Bot** (`council/src/notifier.py`, scheduled, KEIN LaunchAgent,
  Token `TELEGRAM_BOT_TOKEN`).
Beide laufen unabhängig über die telegram-bot-API, kein launchctl-Bezug.

## Verifikation

- `launchctl list | grep -i watchdog` → leer.
- `~/Library/LaunchAgents/com.aionlumen.watchdog-bridge.plist` → weg.
- Plist + README vorhanden unter `~/.hermes/archived/watchdog-bridge/`.
- Reboot-fest: kein Eintrag mehr in `~/Library/LaunchAgents/`.
