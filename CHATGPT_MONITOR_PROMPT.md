# ChatGPT DOT Consumer — Schema v2

Öffne direkt diese eine Datei:
https://raw.githubusercontent.com/exolinodev/dot-market-monitor/main/data/llm_snapshot.json

1. Prüfe `meta.schema_version == 2` und berechne das Alter von `meta.generated_at_utc`. Über 90 Minuten: keine Live-Aussage. Lies `errors`, `sources` und Komponentenstatus; `meta.fresh` gilt nur zum Erzeugungszeitpunkt.
2. Lies die Tabellenspalten aus `meta.cross_event_columns`, `meta.pivot_columns` und `meta.history_delta_columns`. Fehlende Werte sind null; keine Werte ergänzen oder schätzen.
3. Nutze `markets.DOTUSD.spot.current_price` mit `current_price_type`. Ein `spread_midpoint` ist ein Quote-Mittelpunkt und kein Trade. Die verifizierte letzte Ausführung steht separat in `verified_price`. Perp-Ticker, BTC, DOTBTC und ETHBTC nur mit frischer Quelle verwenden.
4. `timeframes.<tf>.live` sind Intrabar-Werte, `last_closed` sind bestätigte Kerzen. Struktur basiert ausschliesslich auf abgeschlossenen Kerzen. Eine Pivot-Bestätigung ist erst nach Schluss der angegebenen Bestätigungskerze gültig. Nutze Slopes, Crosses und Bars seit Cross gemeinsam mit den numerischen Indikatoren.
5. Tape-Werte mit `window_complete=false` sind Teilsummen. CVD startet in jedem Fenster bei null. Absorption ist eine definierte Heuristik, keine bewiesene Teilnehmeraktivität. Wall-Entfernung und Ausführung am selben Preis beweisen keine Orderidentität oder Spoofing-Absicht.
6. Returns vergleichen abgeschlossene Stundenkerzen. Relative Strength, Breadth, Dominanz, TOTAL3-Proxy, Beta und Korrelation tragen ihre Quellen und Zeitgrenzen. Historienwerte können in den ersten Stunden/Tagen fehlen. Absolute Funding-Raten sind keine Prozentangaben.
7. Python liefert keine Elliott-Counts oder Count-Wahrscheinlichkeiten. Interpretiere bestätigte Pivots, Fibs, bedingte Overlap-Flags und Momentum selbst; führe mehrere plausible Count-Familien. Ein hypothetischer 1/4-Overlap ist keine automatische Count-Entscheidung. Keine Single-Count-Sicherheit vortäuschen.

Gib kompakt aus: Datenstand/Einschränkungen; Spot/Perp/BTC/DOTBTC/ETHBTC; DOT-Timeframes vom höheren zum kleineren Zeitrahmen; bestätigte Struktur gegenüber Intrabar-Momentum; Orderflow/Derivate; relative Returns und Marktbreite; mehrere plausible Elliott-Szenarien mit klaren Bestätigungs-/Invalidierungslevels. Trenne gelieferte Messwerte ausdrücklich von eigener Interpretation. Erfinde keine fehlenden Messwerte, Fundamentaldaten oder API-Felder.
