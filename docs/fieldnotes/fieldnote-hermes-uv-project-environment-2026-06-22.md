# Field-Note: Hermes `UV_PROJECT_ENVIRONMENT=venv`-Falle (2026-06-22)

**Direktive:** `~/Projects/direktive-2026-06-22.md` (Block B, #4/#5)
**Checkout:** `~/.hermes/hermes-agent/` (Gateway v0.16, localhost-only, launchd)

## Was

Beim Hermes-Checkout existierten zwei Virtualenvs nebeneinander:
- `~/.hermes/hermes-agent/venv/` (98M) — **aktiv**, vom LaunchAgent benutzt.
- `~/.hermes/hermes-agent/.venv/` (65M) — **redundant**, gelöscht.

Der aktive Env-Pfad ist im Plist
`~/Library/LaunchAgents/ai.hermes.gateway.plist` fest verdrahtet:
`ProgramArguments → /Users/.../hermes-agent/venv/bin/python` (plus
`VIRTUAL_ENV=.../venv`). Das Gateway läuft also ausschließlich aus `venv/`.

## Warum die Falle

`uv` legt standardmäßig `.venv/` an. Ein nacktes `uv sync` in diesem Checkout
baut/aktualisiert daher `.venv/` — **nicht** das `venv/`, aus dem das Gateway
tatsächlich startet. Folge: ein Dependency-Upgrade (z.B. Starlette-Bump gegen
CVE-2026-48710) landet in `.venv/`, das Gateway läuft aber weiter mit der alten
Starlette aus `venv/`. Das Upgrade ist still wirkungslos, der Healthcheck bleibt
grün, der Fix greift nie.

## Regel für künftige Syncs

Jeder Hermes-Dependency-Sync in diesem Checkout MUSS das aktive Env adressieren:

```sh
UV_PROJECT_ENVIRONMENT=venv uv sync
```

(Alternativ den Env-Var dauerhaft für diesen Checkout setzen.) Nach einem Sync,
der Server-Dependencies berührt, das Gateway neu starten und verifizieren:

```sh
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
curl -s http://localhost:8642/health   # {"status": "ok", "platform": "hermes-agent"}
```

## Verifikation (dieser Cleanup)

- `.venv/` gelöscht, `venv/` unverändert vorhanden.
- Gateway nach Löschung grün: `curl localhost:8642/health` →
  `{"status": "ok", "platform": "hermes-agent"}` (PID lief durchgehend,
  Port 8642 — nicht 8644, das ist der Webhook-Adapter-Default).
