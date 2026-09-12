# DOT Market Monitor

[![Tests](https://github.com/exolinodev/dot-market-monitor/actions/workflows/tests.yml/badge.svg)](https://github.com/exolinodev/dot-market-monitor/actions/workflows/tests.yml)
[![Market data](https://github.com/exolinodev/dot-market-monitor/actions/workflows/market-data.yml/badge.svg)](https://github.com/exolinodev/dot-market-monitor/actions/workflows/market-data.yml)

Deterministische Datengrundlage für eine stündliche DOT/BTC Marktanalyse.

Das Repository holt öffentliche Marktdaten direkt von Kraken, berechnet technische Indikatoren selbst und schreibt die Resultate in eine maschinenlesbare Datei `data/latest.json`. Zusätzlich wird `data/latest.md` für Menschen erzeugt.

**Aktuelle Daten:** [JSON](https://raw.githubusercontent.com/exolinodev/dot-market-monitor/main/data/latest.json) · [lesbare Übersicht](data/latest.md) · [Workflow-Läufe](https://github.com/exolinodev/dot-market-monitor/actions/workflows/market-data.yml)

## Was wird berechnet?

Für DOT/USD werden die Timeframes 1m, 5m, 15m, 30m, 1h, 4h, 1d und 1w geladen.

Pro Timeframe werden unter anderem berechnet:

- RSI 14 nach Wilder/RMA
- Stochastic RSI 14,3,3
- MACD 12,26,9
- EMA 20, 50 und 200
- DEMA 20
- ATR 14
- Session VWAP ab 00:00 UTC auf Intraday-Timeframes
- Volumen SMA 20, Volumen-Verhältnis und Z-Score
- bestätigte Pivot Highs und Pivot Lows
- einfache HH/HL/LH/LL Marktstruktur

Zusätzlich:

- DOT Spot Ticker, Trades und Spread von Kraken
- DOT Perp Mark Price, Funding und Open Interest soweit der Kraken Futures Ticker diese Felder liefert
- DOT Spot Orderbuch und Depth-Imbalance innerhalb 0,25 %, 0,5 %, 1 % und 2 %
- Trade-Flow Buy/Sell Volumen und Delta für 5m, 15m und 60m
- BTC/USD 1h, 4h und 1d als Marktfilter
- DOT/BTC 1h, 4h, 1d und 1w direkt von Kraken, wenn das Pair verfügbar ist
- BTC Dominance und Altcoin Breadth via CoinGecko
- DOT Relative Strength gegen ETH, BNB, XRP, SOL, DOGE, ADA, LINK und AVAX
- eigener 24h/7d Verlauf der BTC Dominance aus den stündlichen Snapshots

## Wichtig: offene und geschlossene Kerze

Kraken liefert im OHLC Endpoint die aktuell laufende, noch nicht abgeschlossene Kerze mit.

Darum enthält jeder Timeframe zwei Zustände:

- `live`: Indikatoren inklusive aktueller offener Kerze
- `last_closed`: Indikatoren nur auf vollständig geschlossenen Kerzen

Damit kann die Analyse sauber unterscheiden zwischen einem intrabar Signal und einer bestätigten Kerze.

## Lokaler Test

Python **3.12** verwenden; dieselbe Version läuft in GitHub Actions.

```bash
git clone https://github.com/exolinodev/dot-market-monitor.git
cd dot-market-monitor
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m pytest -q
python src/main.py
```

Danach liegen die Dateien unter:

```text
data/latest.json
data/latest.md
data/history.json
```

## GitHub einrichten

Das Zielrepository ist [exolinodev/dot-market-monitor](https://github.com/exolinodev/dot-market-monitor).
Der Collector startet bei Änderungen am Collector auf `main`, stündlich und bei einem manuellen Aufruf unter `Actions > Update DOT market data > Run workflow`.
Die Tests laufen vor jedem Abruf sowie separat bei Pushes und Pull Requests. Der Collector fordert `contents: write` an, um ausschließlich die drei Dateien in `data/` zu committen. Actions muss im Repository erlaubt sein.

Optional unter `Settings > Secrets and variables > Actions` ein Secret `COINGECKO_API_KEY` mit einem CoinGecko Demo API Key erstellen. Kraken benötigt für diese öffentlichen Endpoints keinen API Key. CoinGecko wird ohne Schlüssel versucht; bei einem Ausfall stehen die betroffenen Zusatzdaten auf `null` und der Fehler wird protokolliert.

Der Workflow läuft danach stündlich bei Minute 55. GitHub Scheduled Actions sind Best Effort und können einige Minuten verspätet starten. Jeder Snapshot enthält deshalb `generated_at_utc` und Source-Zeitstempel. Ein Consumer soll immer das Alter prüfen.

## Datenqualität

- `status` ist `ok`, wenn kein Abruffehler auftrat, sonst `partial` bei fehlenden Zusatzdaten. Details stehen in `errors` und in der lesbaren Übersicht.
- Fehlen verifizierte DOT-/BTC-Spotpreise oder einer der DOT-/BTC-Timeframes, schlägt der Lauf fehl und veröffentlicht keinen neuen Snapshot. Die bisherigen Dateien bleiben erhalten.
- `generated_at_utc` ist der Beginn des Abrufs; `collection_completed_at_utc` ist dessen Ende. Das Alter wird dadurch konservativ berechnet.
- Die 24h-/7d-Dominanzänderung bleibt `null`, bis ein historischer Wert innerhalb von 90 Minuten um den jeweiligen Vergleichszeitpunkt existiert.
- Kraken liefert höchstens etwa 720 OHLC-Kerzen. Session VWAP verwendet die volumen­gewichteten Kraken-Kerzenpreise und bleibt `null`, wenn der Tagesbeginn fehlt; dies betrifft häufig 1m. Weitere Indikatoren bleiben während ihrer Aufwärmphase ebenfalls `null`.
- Trade-Flow bezieht sich auf die aktuelle Uhrzeit. `window_complete: false` bedeutet, dass die gelieferten jüngsten Trades das Zeitfenster nicht vollständig abdecken; Volumen und Delta sind dann nur Teilsummen.
- `markets.DOTUSD.perp.mark_fresh_le_120s` prüft die Frische des Markpreises einschließlich des optionalen 1m-Fallbacks. Veraltete Markpreise werden als `null` ausgegeben.

## Empfohlene Nutzung mit ChatGPT

Für maximale Zuverlässigkeit sollte `data/latest.json` öffentlich direkt abrufbar sein, aber keine persönlichen Tradingdaten enthalten. Alle Daten in diesem Repository sind öffentliche Marktinformationen.

Bei einem öffentlichen Repository lautet die Raw URL ungefähr:

```text
https://raw.githubusercontent.com/exolinodev/dot-market-monitor/main/data/latest.json
```

Ein stündlicher ChatGPT Monitor kann dann genau diese eine JSON-Datei direkt öffnen. Er braucht weder Suchmaschinen noch Kraken Webseiten. Er soll den Snapshot ablehnen, wenn `generated_at_utc` zu alt ist.

Empfohlene Regel für den ChatGPT Monitor:

```text
Öffne direkt die Raw GitHub URL von data/latest.json.
Akzeptiere den Snapshot nur, wenn generated_at_utc höchstens 90 Minuten alt ist.
Verwende die numerischen Felder als primäre Datenbasis.
Nutze live für Intrabar-Frühsignale und last_closed für bestätigte Signale.
Interpretiere Elliott Counts selbst, aber erfinde keine Preise oder Indikatorwerte.
```

## Warum kein persönlicher Positionsstatus im Repository?

Einstieg, Kontowert, Cross Margin und Liquidationspreis gehören nicht in ein öffentliches Repository. Die Marktdaten bleiben öffentlich. Persönliche Positionswerte können getrennt im Chat oder in einem privaten System geführt werden.

## Anpassungen

Die Parameter stehen in `src/indicators.py` in `IndicatorConfig`. Falls TradingView für einzelne Indikatoren leicht andere Initialisierungen verwendet, können die Formeln dort gezielt angepasst und mit Referenzwerten getestet werden.
