# Verspätete Funding-Daten – 24. September 2026

Ab 08:00 UTC scheiterten volle Stundenläufe, während Light-Läufe das Paper-Konto
weiterführten. Der letzte volle Snapshot blieb bei 07:00 UTC. Der volle Collector
archiviert historische Funding-Raten; eine bei der vorigen Abfrage noch fehlende
Rate von 07:00 lag nun vor dem letzten verbuchten Minutenereignis. Der Replay-Guard
verweigerte korrekt eine rückwirkende Einfügung. Jeder erneute Stundenlauf traf
wieder auf dieselbe Rate und brach mit `Events must be in deterministic
chronological order` ab.

`ledger_runtime.advance` nimmt neu eintreffende Funding-Raten hinter dem bereits
verbuchten Ereignisschlüssel nicht nachträglich ins Journal auf. Ihre unveränderten
Quelldaten bleiben im Funding-Archiv. Die Anzahl und die letzten 24 betroffenen
Intervalle stehen unter `late_funding` im Quartals-Ledger und im Execution-Kontext;
der Collector protokolliert die Anzahl. Zeitgerecht verfügbare Raten werden wie
bisher angewendet. Andere rückdatierte Ereignisse und veränderte, bereits verbuchte
Evidenz werden weiterhin abgewiesen.

Es gibt keine nachträglichen Cash-Korrekturen, geänderten Trade-Dateien oder
umgeschriebenen Ereignisse. Bereits festgestellte Funding-Lücken bleiben bestehen:
betroffene Trades behalten `funding_incomplete`, der Buy-and-Hold-Vergleich bleibt
bei fehlenden Raten vorläufig. `late_funding` zählt archivierte, nicht angewendete
Raten, nicht zwingend fehlende Kosten einer tatsächlich offenen Position.
Eine wirtschaftliche Nachverrechnung wäre ein gesondertes, belegtes
Reconciliation-Verfahren und ist nicht Bestandteil dieser Betriebsreparatur.

Die Regression simuliert zuerst fortgeschriebene Minuten ohne die Funding-Rate,
danach deren verspäteten Eingang und eine weitere Viertelstunde. Sie prüft den
unveränderten Journal-Präfix, den unveränderten Cash-Bestand, fortbestehende
Funding-Lücken, Replay und Idempotenz. Eine zweite Regression beweist, dass ein
rückdatierter Spread weiter fehlschlägt. Die bestehende Suite deckt zeitgerechtes
Funding sowie Forecast-Publikation, Fills, Stops und Exit-Buchungen ab.
