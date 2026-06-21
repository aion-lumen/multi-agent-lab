# Befund — C1: Comparis + Homegate Body-Parser-Diagnose (Bauteil 10)

**Datum:** 2026-06-10. **Stopp-Punkt vor Pattern-Strategie-Wahl.**

## Comparis-Samples (3 Mails)

Alle 3 Comparis-Mails haben **konsistente Body-Struktur** mit
direkten Stammdaten im body_excerpt (1000 Zeichen reichen):

| id  | Title (gekürzt)             | Preis      | Adresse                           | qm  | Zimmer |
|-----|------------------------------|------------|------------------------------------|------|--------|
| 954 | Freistehendes Haus mit ...   | CHF 495'000 | Hauptstrasse 125 \| 4247 Grindel | 118 | 6.5    |
| 876 | Triplex mit 125 m² in Hésingue | EUR 285'000 | 4001 Bâle (FR-Inserat, keine Strasse) | 125 | 6      |
| 775 | Maisonettewohnung ...        | CHF 2'650   | Niklaus von Flüe-Strasse 35 \| 4059 Basel | 106 | 4.5 (Miete!) |

**Body-Struktur (redundant 2× pro Inserat):**
```
 <https://www.comparis.ch/comparis/dispatcher/go?cgid=XXXXX>

TITLE

PREIS_LINE (CHF/EUR XXX'XXX)

 ADDRESS_LINE

ZIMMER_LINE (X(.Y)? Zimmer | Y m²)

Weitere Informationen ...
```

**Pattern-Vorschlag (Engineer):**
```python
URL_RE      = r"https://www\.comparis\.ch/comparis/dispatcher/go\?cgid=([A-Za-z0-9_-]+)"
PRICE_RE    = r"(CHF|EUR)\s+([\d']+)"
ADDR_RE     = r"^\s*(?:(.+?)\s+\|\s+)?(\d{4,5})\s+(.+?)\s*$"  # multi-line
ZIMMER_QM_RE = r"(\d+(?:\.\d+)?)\s+Zimmer\s+\|\s+(\d+(?:\.\d+)?)\s+m²"
```

**Variabilität:**
- Multi-Currency (CHF, EUR) — Apostroph-Thousands (Schweizer Format)
- Adress-Format: Pipe-separated (`Str. 125 | 4247 Stadt`) ODER nur PLZ+Stadt (`4001 Bâle`, FR-Inserat ohne Strasse)
- Miete vs Kauf: Body unterscheidet nicht — Filter im Klassifikator nötig (Preis <50k = Miete, existing block_pattern?)
- Multi-Inserat-Support: Comparis hat heute 1 Inserat pro Mail (Sample), Code muss aber robust gegen N sein
- Image-URLs: heute keine im body_excerpt sichtbar (vermutlich in HTML-Body weiter unten)

## Homegate-Samples (3 Mails)

**Ergebnis:** body_excerpt zeigt **NUR Sendgrid-Tracking-URL**,
keine Stammdaten. Beispiel id=1051:

```
Hier ist ein neuer Treffer, der deinen Suchkriterien entspricht.
Immobilien in Basel kaufen

https://u8489473.ct.sendgrid.net/ls/click?upn=u001.FWAihBcI-2B...
[1000-Char-Limit erreicht, nur Tracking-URL sichtbar]
```

Alle 3 Homegate-Mails (id 737, 745, 776, 1051) haben body_excerpt
auf 1000 chars truncated mit nur dem Sendgrid-Click-Link. Echte
Stammdaten leben hinter dem Sendgrid-Redirect (Homegate-Detail-
URL `homegate.ch/kaufen/<id>`).

**Engineer-Befund:** Body-Parser für Homegate funktioniert nicht
(keine Substanz im body_excerpt). Stammdaten leben in HTTP-Detail-
Page, die nach Sendgrid-Redirect erreichbar ist.

## Engineer-Empfehlung Pattern-Strategie

| Portal     | Strategie                                  | Begründung |
|------------|--------------------------------------------|------------|
| Comparis   | **Body-Parser** (Regex auf body_excerpt)   | Stammdaten direkt im Body, parsbar |
| Homegate   | **Fetch-Pfad mit Sendgrid-Resolve**        | Body leer, Sendgrid-URL muss resolved werden zur Detail-Page |

**Sendgrid-Resolve (Homegate):** in `fetch_with_canonical()` —
wenn URL-Host enthält `sendgrid.net` / `ct.sendgrid.net`:
HEAD-Request mit `allow_redirects=True`, dann GET der Final-URL
(typisch `https://www.homegate.ch/kaufen/<id>`) für og:tags.

## Mindest-Substanz-Schwelle (Engineer-Empfehlung)

3-von-4-Schwelle wie Bauteil 8 (PLZ + qm + Preis + Adresse):
- Comparis Body-Parser liefert alle 4 in den Samples → matcht
- Homegate via Fetch og:tags: typisch PLZ aus inserat_plz +
  qm aus og:title (existing title_parser) — Engineer-Hoffnung
  3/4 erfüllt

## Fallback-Strategie (Engineer)

- **Comparis ohne qm** → bleibt `not_an_inserat:no_qm` (existing
  Bauteil-8-Filter)
- **Homegate Sendgrid-Resolve failed** → existing `expired_url`-
  Pfad bleibt
- **Comparis Miete-Inserat** (Preis <50k) → wird via existing
  Korridor- / Preis-Schwelle filter (nicht zwingend Bauteil-10-
  Job; Folge-Direktive 10b wenn Miete-Filter nötig)

## Architekt-Stopp

Bestätigung Pattern-Strategie (Comparis Body-Regex + Homegate
Sendgrid-Resolve+Fetch) oder Anpassung. Engineer-Empfehlung wörtlich
übernehmbar.

## Sample-Substanz für Cluster-Bewährung

Nach C2-Implementation: aus den 3 Comparis-Samples sollten 3
verschiedene Council-Objekte mit vollen Stammdaten entstehen
(unterschiedliche Adressen). Cross-Portal-Match wird sich erst
zeigen wenn eines der Comparis-Inserate als ImmoScout-Inserat
ebenfalls in der Inbox landet.
