# ChatGPT Consumer Prompt

Öffne direkt https://raw.githubusercontent.com/exolinodev/dot-market-monitor/main/data/latest.json. Verwende keine Suchmaschine und keine anderen Preisquellen, solange der Snapshot frisch ist.

Validierung:

1. Prüfe `generated_at_utc`. Bei mehr als 90 Minuten Alter: Snapshot als veraltet melden und keine Live-Aussage daraus ableiten.
2. Nutze für DOT Spot `markets.DOTUSD.spot.verified_price`.
3. Nutze für DOT Perp `markets.DOTUSD.perp.mark_price`, aber nur wenn `markets.DOTUSD.perp.mark_fresh_le_120s` wahr ist.
4. Nutze `timeframes.<tf>.live` für Intrabar-Frühsignale und `timeframes.<tf>.last_closed` für bestätigte Kerzensignale.
5. Interpretiere 1W und 1D als höhere Degrees, 4H und 1H als Subwellen, 30m und 15m als Subsubwellen und 5m als Feinstruktur.
6. Nutze RSI, Stoch RSI, MACD, EMA20/50/200, DEMA20, ATR, Session VWAP, Volumen-Verhältnis, Pivotstruktur, Orderbuch-Imbalance und Trade-Flow gemeinsam. Kein einzelner Indikator entscheidet allein.
7. DOT/BTC ist ein Bestätigungsfilter für relative Stärke. BTC/USD und BTC Dominance sind Regimefilter.
8. Bei Elliott immer mehrere plausible Count-Familien parallel führen. Keine Single-Count-Sicherheit vortäuschen.
9. Erfinde keine fehlenden Werte. Falls ein Feld fehlt, sage exakt, dass es im Snapshot nicht verfügbar ist.
10. Prüfe `status` und `errors`. Trade-Flow mit `window_complete: false` ist eine Teilsumme. Eine fehlende Session-VWAP oder Dominanzhistorie darf nicht durch Annahmen ersetzt werden.

Gib pro Lauf kompakt aus:

- Datenstand und Alter
- DOT Spot, DOT Perp, BTC Spot, DOT/BTC
- Multi-Timeframe Status 1W, 1D, 4H, 1H, 30m, 15m, 5m
- Momentum und Struktur mit klarer Trennung von live und confirmed
- Orderbuch und Trade-Flow
- BTC Dominance und Altcoin Breadth
- DOT Relative Strength
- Count-Baum mit Wahrscheinlichkeiten
- wichtigste Bestätigungs- und Invalidierungslevels
