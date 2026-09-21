# Aktuelle Stundenrunde für den ChatGPT-Monitor abrufen

Der v4-Stundenjob startet um :03 UTC (Zeitplan der ChatGPT-Aufgabe). Lies zuerst main und den vollständigen
Snapshot bzw. Consumer-Index am zurückgegebenen SHA. Nutzbar ist nur
`meta.run_kind=full` mit `meta.cycle_boundary_utc` gleich der aktuellen UTC-Stunde;
Frische und Quellenabdeckung zusätzlich prüfen. Ein junger Snapshot der falschen
Runde genügt nicht. Nach einem Stundenwechsel erneut prüfen.

Fehlt die Runde, main/Snapshot einmal neu lesen (CDN-Verzögerung ausschliessen),
dann über tatsächlich verfügbare GitHub-Tools:

1. Runs von `market-data.yml` auf main lesen. Bei queued/in_progress/waiting keinen
   zweiten Collector starten; auch der Light-Lauf nutzt dieselbe Schreibsperre.
2. Gibt es keinen aktiven Lauf, höchstens ein `workflow_dispatch` auf main mit
   `run_kind=full`, `boundary_utc=<aktuelle Stunde als YYYY-MM-DDTHH:00:00Z>`.
   Tool-Schema zuerst lesen. Kein alter Run-Rerun: dessen feste boundary_utc kann
   zur falschen Runde gehören. Ein Dispatch-Erfolg ist kein Publikationsnachweis.
3. Höchstens vier Statusabfragen über insgesamt 150 Sekunden, danach main und
   Snapshot einmal neu lesen. Neuer Commit, passende Grenze und Quellen prüfen.
   Ein noch laufender Run wird mit Link als ausstehend gemeldet.
4. Fehlende Tools oder 403: keinen Toolnamen erfinden, keine Wiederholungsversuche.
   Störung und [manuellen Start](https://github.com/exolinodev/dot-market-monitor/actions/workflows/market-data.yml)
   nennen. Keine v4-Einreichung ohne aktuellen, verifizierten execution_context.

Erforderlich ist ein tatsächlich autorisierter GitHub-Anschluss mit Actions-
Schreibrecht auf dieses Repository. Öffentliche Lesbarkeit genügt nicht. Keine
Tokens im Prompt oder in öffentlichen Dateien. Dieser Ablauf ändert keine
bestehende ChatGPT-Aufgabe von selbst; deren Zeitplan und Prompt müssen separat
aktualisiert und zurückgelesen werden.
