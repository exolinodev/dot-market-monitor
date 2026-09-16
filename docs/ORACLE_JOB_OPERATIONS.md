# Oracle-Jobs: Betrieb und Browser-Test

Stand: 16. September 2026. Der bestehende ChatGPT-Job wurde im Browser von
„Kraken DOT/BTC Monitor v2“ auf „DOT/BTC Oracle v3“ aktualisiert. Er bleibt
stündlich aktiv. Persönliche Positionsangaben bleiben ausschliesslich im privaten
ChatGPT-Aufgabenprompt; diese Datei dokumentiert nur den technischen Betrieb.

## Reihenfolge

| Job | Intervall / Auslöser | Ergebnis |
| --- | --- | --- |
| Cloudflare-Starter | Prüfung alle fünf Minuten, neue Runde ab :50 UTC | Ein Collector bei Bedarf, mit Doppelstartprüfung |
| GitHub-Collector | :52 UTC als unabhängiger Ersatz; manuell / Code-Push | Messungen, Featurehistorie, gereifte Outcomes, Scorecard, Snapshot |
| ChatGPT Oracle | Bestehender Stundentakt um die volle Stunde | Bedingte Analyse und bei erfüllten Voraussetzungen Forecast |
| Forecast-Writer | Pro tatsächlichem neuen Forecast, kein eigener Cron | Create-only Forecast und gebundener Snapshot |
| Tests mit optionalem Live-Collector | Manueller Start auf gewähltem Branch | Isolierte Daten und sieben Tage verfügbares Actions-Artefakt |

Collector und Consumer bleiben stündlich: Die Quelle liefert stündliche
Zustände; häufigere Modellaufrufe würden überwiegend dieselbe Information
wiederholen. Die bisherige Sammlung um :50 war bei der Prüfung mehrfach
erfolgreich und bietet Abstand zum Verbraucher. GitHub und ChatGPT garantieren
keinen minutengenauen Start. Die nächste reguläre Sammlung bewertet inzwischen
gereifte 1h/4h/12h-Horizonte automatisch. Dafür ist kein zusätzlicher LLM-Job nötig.

## Aufgabenprompt 3.0.2

Der private Aufgabenprompt enthält eine eigenständige Betriebsanweisung und lädt
die ausführliche `CHATGPT_MONITOR_PROMPT.md` sowie das Forecast-Schema am selben
vollständigen `main`-SHA wie die Daten, sobald der v3-Kontext vorhanden ist. Ein
alter v2-Repo-Prompt darf die neue Aufgabenanweisung nicht überstimmen. Ein
`partial`-Kontext ist zulässig; ausschliesslich verfügbare Features verwenden. Er prüft:

- Vollständigen JSON-Abruf, tatsächliches Alter, strengere Consumer-Grenzen und
  Quelle für jede verwendete Aussage.
- Bei alten Daten den commitgebundenen Abruf vor einem einzigen begrenzten
  Collector-Wiederanlauf; aktive Jobs und 403 verhindern weitere Startversuche.
- Nach einem Start maximal drei Statusabfragen über insgesamt 90 Sekunden;
  danach Readback und gegebenenfalls einen ausstehenden Run mit Link melden.
- Übergangsmodus ohne `oracle_context`: v2-Messungen bleiben verwendbar,
  v3-Evidenz und Veröffentlichung werden nicht erfunden.
- Identische Snapshot-Zeit/Hash: keine zweite Veröffentlichung.
- ORACLE CALL zuerst, getrennte Macro-/Swing-/Intraday-/Execution-Ebenen,
  drei bedingte Pfade, belegte Ziele/Failure und tatsächliche Asymmetrie.
- A+B+C vor Reversal-Aktivierung, qualitative unkalibrierte Confidence,
  keine Selbstauswertung oder Übernahme alter Count-Preise als aktuelle Messwerte.
- Erzeugung nach tatsächlichem Schema und exaktes Hashing per Werkzeug;
  Erfolg erst nach Workflow-Ergebnis und Readback.

Der Writer behält die 120-Sekunden-Publikationsgrenze. Die CI-Warteschlange kann
sie überschreiten. Dann ist die Veröffentlichung fehlgeschlagen; die Uhrzeit
eines alten Forecasts darf nicht nachträglich geändert werden. Eine Erweiterung
dieser Grenze ohne unabhängige zeitgestempelte Annahme würde rückwirkende
Prognosen erlauben. Ein erneuter Versuch verlangt eine neue aktuelle Analyse.

## Reproduzierbarer Live-Test ohne Merge

```bash
gh workflow run tests.yml --ref oracle-v3 -f live_collection=true
```

Der Workflow kopiert die vorhandene Historie in ein Runner-Tempverzeichnis,
ruft echte Marktquellen ab, validiert den Snapshot samt Oracle-Kontext und lädt
die Ausgaben als `oracle-live-smoke` hoch. Er hat nur Repository-Leserechte und
schreibt keine Testdaten nach `main`. Lokal ist derselbe Weg verfügbar:

```bash
python src/main.py --data-dir /tmp/oracle-smoke
```

Ohne hineinkopierte echte Historie ist das ein Kaltstart; fehlende Vergleiche
bleiben unbekannt. Testdaten dürfen nicht als veröffentlichte Modell-Forecasts
oder als originale historische Beobachtungen in die Produktion übernommen werden.

## Ergebnisse

Der [erste echte Branch-Lauf](https://github.com/exolinodev/dot-market-monitor/actions/runs/35146199667)
erzeugte den Snapshot `2026-09-16T20:23:28.280069Z`, Root-Schema 2, Status `ok`,
keine Quellenfehler. Der Oracle-Kontext war erwartungsgemäss `partial`:
39 von 64 Features `ok`, 25 `unavailable`. Es fehlten vor allem Oracle-Vergleichs-
historie, passende OI-Referenzen beim ausserplanmässigen Abruf und neue bestätigte
Pivots. Keine künstlichen Ersatzwerte. Beide A+B+C-Gates blieben geschlossen.
Analogs haben null geeignete Samples, die Modell-Scorecard ist leer.

Snapshot-Hash:
`f63a9647d907cf17166e1d57d030e77de0be4ce9e77a988bca862524d0615e49`.

Die vorhandenen 238 Python-Tests und 17 Scheduler-Tests bestehen lokal.
Der erste Live-Lauf zeigte eine Node-20-Deprecation-Warnung der neu ergänzten
Artifact-Action; diese wurde auf die bestätigte aktuelle Version 7.0.1 aktualisiert.
Auch die vorhandene Node-Setup-Action wurde auf 7.0.0 aktualisiert.

Der [zweite Live-Test](https://github.com/exolinodev/dot-market-monitor/actions/runs/35146664219)
bestand in 48 Sekunden einschliesslich Tests, echter Sammlung und Artefakt-Upload.
Er erfasste 43 Quellen ohne Fehler, Snapshot `2026-09-16T20:28:14.730464Z`.
Im Browser-Test wurde die bestehende ChatGPT-Aufgabe über „Jetzt ausführen“
gestartet. Sie löste zudem einen echten Collector-Wiederanlauf aus:
[Run 35146516237, Versuch 2](https://github.com/exolinodev/dot-market-monitor/actions/runs/35146516237/attempts/2),
erfolgreicher neuer Daten-Commit `3edc0ac38bf452541cfdf2f3242db389242b52bd`.
Die nächste Prompt-Iteration verhindert die Übernahme des alten v2-Prompts von
`main` vor dem Rollout und begrenzt Statuspolling. Das Modell blieb unverändert.

Der produktive v3-Writer kann erst nach Freigabe/Merge des Oracle-PR auf `main`
ausgeführt werden. Bis dahin benutzt der ChatGPT-Job den expliziten Übergangsmodus.
Der ursprüngliche Auftrag verbietet automatisches Mergen. Ein erfolgreicher
Collector-/Browser-Test ist kein Nachweis prognostischer Profitabilität.
