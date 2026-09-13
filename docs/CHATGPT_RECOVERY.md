# ChatGPT kann den Collector neu starten

Eine geplante ChatGPT-Aufgabe kann die in ihrem Chat verfügbaren verbundenen Tools
verwenden. Voraussetzung ist ein tatsächlich autorisierter GitHub-Anschluss mit
Actions-Schreibrecht auf `exolinodev/dot-market-monitor`. Ein öffentlich lesbares
Repository allein genügt nicht. Ein `403 Resource not accessible by integration`
ist ein Berechtigungsfehler; ein Prompt oder ein öffentlicher GET-Link löst ihn
nicht. Die App-Freigabe muss im GitHub-Konto erfolgen.

Der offizielle OpenAI-Connector bündelt Actions-, Code-, Issue-, Pull-Request- und
Workflow-Schreibrechte. Bei seiner Installation nur dieses Repository auswählen.
Einzelne Rechte dieses Bündels sind nicht abwählbar. Es ist kein PAT im Prompt,
öffentlichen Repository oder URL erforderlich.

## Ablauf für die stündliche Aufgabe

1. `data/llm_snapshot.json` laden und `meta.generated_at_utc` mit UTC vergleichen.
   Wenn Daten fehlen oder älter als 30 Minuten sind, über den GitHub-Anschluss
   zunächst `/git/ref/heads/main` lesen und den Snapshot am zurückgegebenen SHA
   laden. Das vermeidet eine Entscheidung allein anhand eines Raw-CDN-Caches.
2. Sind die Daten weiterhin zu alt, die Lauf-Liste des festen Workflows lesen:
   `https://api.github.com/repos/exolinodev/dot-market-monitor/actions/workflows/market-data.yml/runs?branch=main&per_page=10`.
   Bei aktivem Collector keinen weiteren starten.
3. Andernfalls im neuesten abgeschlossenen Lauf die Jobs mit
   `fetch_workflow_run_jobs` lesen und den Job `collect` einmal mit
   `rerun_workflow_job` neu starten. Die tatsächliche Job-ID aus der Antwort
   verwenden, keine fest hinterlegte alte ID. Der Lauf muss jünger als 30 Tage sein.
4. Danach Status, Erfolg, neuen Daten-Commit und Snapshot-Zeit prüfen. Ein
   angenommener Wiederanlauf ist noch kein erfolgreicher Datenabruf. Höchstens ein
   Wiederanlauf pro Monitorlauf; nach 403 nicht wiederholt versuchen.
5. Bei fehlendem Tool oder fehlender Berechtigung den Fehler und den
   [manuellen Startlink](https://github.com/exolinodev/dot-market-monitor/actions/workflows/market-data.yml)
   ausgeben. Alte Kurse bleiben als alt gekennzeichnet.

GitHub verwendet beim Wiederanlauf die Workflow-Definition des ausgewählten Runs.
Unser Checkout setzt ausdrücklich `ref: main`, sodass die aktuellen Python-Dateien
und Daten geladen werden. `GITHUB_RUN_ATTEMPT > 1` wird im Frische-Guard als
manueller Abruf behandelt: Ein alter Schedule-Run darf damit innerhalb derselben
Stundenrunde neue Daten erzeugen. Ein gerade erst erzeugter Snapshot (höchstens
zwei Minuten alt) unterdrückt redundante Wiederanläufe. Tests laufen separat bei
Codeänderungen, nicht beim manuellen oder stündlichen Collector.

Die ursprüngliche Aufgabenbeschreibung und Marktinterpretation bleiben in
ChatGPT. Dieses Dokument enthält nur den technischen Wiederanlauf und keine
Positionsdaten, Elliott-Wahrscheinlichkeiten oder Handelsanweisungen.

Quelle zu verbundenen Tools in geplanten Aufgaben:
https://learn.chatgpt.com/docs/automations
