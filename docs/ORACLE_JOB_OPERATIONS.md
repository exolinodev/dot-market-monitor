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
| Forecast-Writer | Push einer neuen Einreichung auf main oder manueller Dispatch, kein eigener Cron | Create-only Forecast und gebundener Snapshot |
| Tests mit optionalem Live-Collector | Manueller Start auf gewähltem Branch | Isolierte Daten und sieben Tage verfügbares Actions-Artefakt |

Collector und Consumer bleiben stündlich: Die Quelle liefert stündliche
Zustände; häufigere Modellaufrufe würden überwiegend dieselbe Information
wiederholen. Die bisherige Sammlung um :50 war bei der Prüfung mehrfach
erfolgreich und bietet Abstand zum Verbraucher. GitHub und ChatGPT garantieren
keinen minutengenauen Start. Die nächste reguläre Sammlung bewertet inzwischen
gereifte 1h/4h/12h-Horizonte automatisch. Dafür ist kein zusätzlicher LLM-Job nötig.

## Aufgabenprompt 3.1.0

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
- REFLOOP als geprüfte Zustandsfolge, Abgleich des vorherigen Writers, konkrete
  Outcome-Dateien und versionsgleiche Scoregruppen vor der nächsten Analyse.
- Outcome-Identitäten und qualitative Konsequenz im neuen text_summary als
  unveränderlicher Nachweis, welche Erkenntnis verwendet wurde. Wiederholtes
  Lesen erzeugt keine zusätzliche Stichprobe; bei fehlendem Vorcheckpoint bleibt
  die Erstverwendung unbekannt.

Der bestehende Job wurde über die ChatGPT-Weboberfläche gespeichert und erneut
geöffnet: Prompt 3.1.0 stimmt vollständig mit dem eingegebenen Text überein,
Intervall weiterhin eine Stunde. Die privaten Positionsangaben wurden unverändert
erhalten. Es wurde keine lokale Codex-Automation angelegt.

## Einreichung aus den verfügbaren ChatGPT-Werkzeugen

Die verbundenen GitHub-Werkzeuge bieten Datei-Erstellung, aber nicht durchgehend
einen Workflow-Dispatch mit frei belegbaren Inputs. Die alte Anweisung, direkt
den Writer aufzurufen, war daher kein ausreichender operativer Pfad. Neu legt
ChatGPT mit seinem vorhandenen autorisierten Create-File-Zugriff genau eine Datei
`data/oracle/submissions/<forecast_id>.json` auf main an:

```json
{"schema_version":1,"snapshot_commit":"<vollständiger Analyse-SHA>","forecast":{}}
```

`forecast` steht hier für das vollständige Objekt des tatsächlichen Forecast-
Schemas, nicht für ein zulässiges leeres Objekt. Der Push startet den Writer,
der die Originaldatei aus dem auslösenden Commit liest und den bestehenden
Publisher benutzt. Einreichungs-ID/Dateiname, neue reguläre Datei, eindeutiger
Push, main-Abstammung, Snapshot-Bindung, Zeit und Schema werden geprüft.
Die vorhandenen Rechte genügen; es braucht weder neue Credentials noch einen
weiteren Modelljob. Änderungen/Löschungen und wiederverwendete IDs werden abgelehnt.

Der Consumer nennt zuerst „eingereicht“. „Persistiert“ gilt erst nach dem eigenen
erfolgreichen Run und dem vollständigen Readback aus `data/oracle/forecasts`.
Der Collector bewertet spätere Horizonte und reicht die Ergebnisse im nächsten
Oracle-Kontext zurück. Ein Feld mit dem Namen recent_matured_outcomes kann auch
vorläufige partial/unavailable-Zeilen enthalten; der tatsächliche Status zählt.
Finaler R=-1 und T1 vor Failure können gleichzeitig korrekt sein: Ein Zieltreffer
beweist keinen realisierten Handelsgewinn. Ohne geeignete Samples keine neue
kalibrierte Wahrscheinlichkeit und keine automatische Schwellenoptimierung.

Der Writer behält die 120-Sekunden-Publikationsgrenze. Seit dem 19. September
misst er sie an der von GitHub gestempelten Öffnungszeit des Draft-PR, einer
unabhängigen, vom Consumer nicht editierbaren Annahmezeit, statt am Start des
Actions-Runners. Damit scheitert eine ehrliche Einreichung nicht mehr an der
CI-Warteschlange; die eigene Verzögerung zwischen Erstellungszeit und Create-PR
zählt weiterhin, und ein Draft, der mehr als 30 Minuten vor dem Writer geöffnet
wurde, gilt als veraltet. Die Uhrzeit eines alten Forecasts darf nicht
nachträglich geändert werden. Eine weitere Lockerung ohne unabhängige
zeitgestempelte Annahme würde rückwirkende Prognosen erlauben. Ein erneuter
Versuch verlangt eine neue aktuelle Analyse.

## Prompt 3.3.3 und Writer-Stabilisierung vom 19. September

Auslöser waren sieben abgewiesene Drafts (#35, #52, #78, #89, #94, #96, #101):
zwei zu spät geöffnete PRs, drei unbalancierte JSON-Envelopes (zwei mit fehlender,
eine mit überzähliger Schlussklammer) und zwei Einreichungen, die ein
`unavailable`-Feature (`structure.1h.new_low`) als Evidenz zitierten. Der Writer
nennt jetzt bei jeder Ablehnung den konkreten Grund (Klammerbilanz und Dateiende,
gemessene Zeitabweichung, nicht verfügbare Feature-IDs), schliesst auch abgewiesene
Drafts mit diesem Grund als Kommentar und misst die 120-Sekunden-Grenze an der
PR-Öffnungszeit. Prompt 3.3.3 verlangt vor Create-File die Prüfung der exakt zu
schreibenden Zeichenkette (Anfang, Ende `}}`, Klammerbilanz, erneutes Parsen) und
den Abgleich jeder zitierten Feature-ID gegen `status: "ok"` im geladenen
features-Teil. Der Tests-Workflow läuft für PRs nicht mehr auf reinen
`data/**`-Änderungen ausser `data/oracle/submissions/**`; die Daten-PRs des
vertrauenswürdigen Producers tragen ihren eigenen `test`-Check.

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

## Im Browser gefundener Zugriffsfehler und Korrektur

Der erste echte ChatGPT-Lauf brach nach rund neun Minuten mit NO_TRADE ab:
Der Recovery war erfolgreich, aber die 289-KB-Snapshot-Datei konnte mit den dort
verfügbaren Werkzeugausgaben nicht vollständig ausgewertet werden. Das war ein
Verbraucherproblem, kein Fehler der Marktquellen. Die Aufgabe erfand richtigerweise
keine fehlenden Werte. Ihre Störungsmeldung begann allerdings noch mit einer
Erklärung statt ORACLE CALL; der Aufgabenprompt wurde auch dafür präzisiert.

`src/oracle_consumer.py` exportiert deshalb zusätzlich
`data/oracle/consumer/index.json` mit versioniertem Format, vollständigem
Snapshot-Hash und Teildateien von höchstens 10.000 Bytes. Deren Pfade sind relativ
zum Indexverzeichnis. Jeder Record ist ein JSON-Pointer und exakt dessen
Originalwert. Kein Indikator wird neu berechnet, kein ausgelassenes Feld ersetzt.
Die Teile enthalten Überblick, Oracle-Metadaten/Feedback, alle Features,
geschlossene DOT-Zeitrahmen, Struktur, Zeitfenster, ausgewählte Beobachtungen und
Quellenstatus. Intrabar-Daten und ausgelassene Detailindikatoren sind daraus
nicht bekannt; bei Bedarf bleibt die vollständige Datei verfügbar.

Im ersten echten Beispiel sind das 14 Teile mit zusammen etwa 101 KB statt
einer unteilbaren 289-KB-Antwort. Die kleine Indexdatei und Teilgrössen machen
den Zugriff auch bei gekürzten Dateitools prüfbar. Python berechnet den Hash
über den vollständigen Snapshot; der Forecast-Writer lädt diesen später selbst
und validiert die Bindung. Ein Consumer darf den berechneten Index-Hash
übernehmen, statt ihn zu erraten. Alle Teile müssen vom gleichen Commit stammen.

Vier zusätzliche Tests prüfen jeden exportierten Wert gegen den vollständigen
Snapshot, deterministische Reihenfolge, Teilegrössen/Hashes, fehlenden v3-Kontext,
JSON-Pointer-Aufteilung und dass nur rollierende Projektionsdateien ersetzt werden.
Die echten Testdaten liegen unter `docs/evaluation/oracle-v3/consumer-smoke/`,
ausdrücklich als Testdatensatz markiert, ohne veröffentlichten Modell-Forecast.

Der [abschliessende echte Collector-Lauf](https://github.com/exolinodev/dot-market-monitor/actions/runs/35147819567)
bestand einschliesslich aller 242 Python- und 17 Scheduler-Tests, Sammlung,
Index-/Teil-Hashprüfung und Upload. Snapshot `2026-09-16T20:39:53.582368Z`,
43 Quellen, null Quellenfehler, 64 Features (45 verfügbar, 19 unbekannt).
Die 14 Teile umfassen 101.660 Bytes, der grösste 9.984 Bytes.

Der zweite echte ChatGPT-Browserlauf verwendete ausschliesslich den festgehaltenen
Testdatensatz vom ersten Collector-Lauf. Nach 4m 59s lieferte er ORACLE CALL zuerst,
getrennte Macro-/Swing-/Intraday-/Execution-Ebenen, drei bedingte Pfade,
Asymmetrie und den originalen JSON-Entwurf `consumer-smoke/browser_forecast.json`.
Alle 14 kleinen Dateien waren laut Abrufnachweis vollständig lesbar; eine erste
gekürzte Darstellung von `structure-01.json` wurde vollständig nachgelesen.
Python `validate_forecast(forecast, snapshot)` bestätigt Schema, Zeit, Snapshot-
und Config-Bindung, sämtliche verwendeten Feature-IDs und Setup-Konsistenz.
Forecast-Hash: `ef7f8c7ad30ab9b11d7144c97fc0362f90e87054c5c152afc3cbc1a5d50aa9c5`.

Der Entwurf trennt bearishen Macro-Bias von bullishem Swing/Intraday, bleibt
bei `EXHAUSTION_WATCH` und `NO_TRADE`, ohne falsche A+B+C-Aktivierung. Er weist
null Analog-/Scorecard-Samples korrekt als unkalibriert aus. Die 1h-Rationale
verband fehlendes A+B+C sprachlich zu stark mit fehlender Fortsetzungsbestätigung;
der Prompt stellt nun klar, dass dieses Gate ausschliesslich Reversal-Regime
betrifft. Der ursprüngliche Testentwurf wird dafür nicht nachträglich verändert.

Die Testzeit im JSON ist eine ausdrücklich festgelegte Experimentkonvention,
kein echter Publikationszeitpunkt. Dieser Einzeltest im bestehenden Chat ist
kein verblindeter historischer Vergleich und keine Profitabilitätsmessung.
Es erfolgte kein Write-back in die Produktions-Scorecard.

Der produktive v3-Writer kann erst nach Freigabe/Merge des Oracle-PR auf `main`
ausgeführt werden. Bis dahin benutzt der ChatGPT-Job den expliziten Übergangsmodus.
Der ursprüngliche Auftrag verbietet automatisches Mergen. Ein erfolgreicher
Collector-/Browser-Test ist kein Nachweis prognostischer Profitabilität.

## Refloop-Test des ChatGPT-Verbrauchers, Prompt 3.1.0

Der bestehende Chat wurde über den Browser mit sechs ausdrücklich synthetischen,
kleinen Vertragsfällen aufgerufen. Festgehaltener Test-Commit:
`01d1215e37a7dff6ea63ea0b015d1bd1bcd70ebd`. Der Test lief laut ChatGPT-Anzeige
2m 17s. Die Fälle enthalten Original-Fixture-Forecasts, Ergebnisse des echten
Python-Evaluators und dessen Scorecard. Speicherbelege sind ausdrücklich simuliert.
Reproduktion: `python tests/refloop_fixture.py`; die automatisierte Prüfung
vergleicht alle Dateien vollständig und hält jede unter 10 KB.

| Fall | Python-/Speicherbefund | Beobachtetes ChatGPT-Verhalten |
| --- | --- | --- |
| a | gespeichert simuliert; 1h pending | kein Gewinn/Verlust und keine Anpassung |
| b | T1 vor Failure; final failure, R=-1 | unterscheidet Zielberührung von Gewinn; 1/30 Samples, nur begrenzte qualitative Konsequenz |
| c | ambiguous; Outcome schon konsultiert | keine Reihenfolge erfunden; kein neues Sample; Trade-n 0, Direction-n 1 getrennt |
| d | Failure mit inkompatibler Strategie | aus aktueller v3-Statistik und v3-Anpassung ausgeschlossen |
| e | NO_TRADE/ABSTAIN, Python abstained | kein erfolgreicher Trade; beide Performance-Samples 0 |
| f | Einreichung vorhanden, Writer queued, finale Datei fehlt | nur eingereicht; kein persistiert, kein Outcome, kein Doppelversuch |

Alle sechs Fälle wurden korrekt unterschieden. Die Antwort begann mit
`ORACLE CALL: NO_TRADE – isolierter Refloop-Test` und lieferte einen Checkpoint
sowie eine text_summary-Passage mit der tatsächlichen Test-Outcome-Identität.
Nicht gelieferte 4h/12h-Resultate blieben unbekannt. Der Job erklärte ausdrücklich,
dass die Testbelege keine produktive Speicherung oder Performance nachweisen.
Die Fähigkeiten wurden im Chat selbst durch read-only Werkzeugentdeckung geprüft:
`GitHub.create_file` ist verfügbar mit repository_full_name, path, content,
message und optional branch; ein freier workflow_dispatch wurde nicht gefunden.
Der direkte Read von `.github/workflows/oracle-forecast.yml` auf main lieferte
404, während der festgehaltene Test-Commit beide Einreichungswege enthält.

Die 259 Python-Tests und 17 Scheduler-Tests bestanden lokal und in
[GitHub CI](https://github.com/exolinodev/dot-market-monitor/actions/runs/35150401268).
16 neue Git-/Publisher-Fälle prüfen Original-Commit statt veränderter Arbeitsdatei,
create-only Forecast/Inputs, veraltete Zeit, falsche Snapshot-Bindung, Änderungen,
Löschungen, Umbenennung, mehrere Dateien, ID-Zuordnung, zukünftigen Snapshot-Commit,
Symlink, Extrafelder, falsche Branch-/Commit-Zuordnung und Wiederverwendung einer ID.

Anschliessend wurde im Browser beim gespeicherten Job „Jetzt ausführen“ gewählt.
Der automatische Freigabeprüfer blockierte den Aufruf vor der Ausführung, weil
der reale Job Collection und Forecast-Write-back auslösen kann und keine explizite
Produktionsfreigabe anerkannt wurde. Es wurde kein Ausweichen auf einen indirekten
Start versucht. Der ursprüngliche Auftrag untersagt zusätzlich automatisches
Mergen. Offen bleibt daher die ausdrücklich freigegebene Übernahme nach main und
die echte Speicherung samt späterem 1h/4h/12h-Readback; die synthetische Prüfung
darf diesen fehlenden Nachweis nicht ersetzen.
