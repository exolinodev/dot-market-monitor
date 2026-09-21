# Cloudflare-Starter für Oracle v4

## Aktueller Betrieb ab 21.09.2026

Der Worker dispatcht vier Viertelstundenrunden pro Stunde. Nach den ersten
Timing-Messungen wird der Vorlauf von einer auf zwei Minuten erhöht:
`58,13,28,43 * * * *`, zusätzlich `*/5` zur Kontrolle. Der Runner installiert
Dependencies vorab und wartet bis zur UTC-Grenze +8 Sekunden (maximal 150 Sekunden).
Die Grenze :00 erzeugt den vollen Snapshot, :15/:30/:45 einen Light-Lauf.
Der unabhängige GitHub-Fallback läuft um :03/:18/:33/:48. Bereits erfolgreich
veröffentlichte Runden werden auch bei Code-Pushes übersprungen. Identität,
Dispatch-Budget und Recovery beziehen sich auf die jeweilige Viertelstundenrunde.

Das sind regulär 16 Worker-Aufrufe pro Stunde und 96 GitHub-Fallback-Starts pro
Tag; auch übersprungene GitHub-Jobs benötigen kurze Laufzeit. Deployment allein
belegt keine Timing-Abnahme. Aktuelle Evidenz und offene Kriterien stehen in
[ORACLE_V4_ROLLOUT_STATUS.md](ORACLE_V4_ROLLOUT_STATUS.md), der genaue Vertrag in
[ORACLE_V4_PHASE1.md](ORACLE_V4_PHASE1.md).

## Historische Einrichtung vor v4

Die folgenden ursprünglichen Stundenzeiten und Kostenmengen beschreiben den
früheren Betrieb. Für aktuelle Zeiten und Frische gilt der v4-Vertrag oben;
Zugänge, Betriebsbefehle und die dokumentierte Fehlerhistorie bleiben relevant.

Cloudflare führt keine Marktberechnungen aus. Der Worker startet den bestehenden
GitHub-Actions-Collector und prüft, ob dieser einen aktuellen Snapshot veröffentlicht
hat. Der Python-Code und alle Marktquellen bleiben unverändert.

Seit dem Ausfall vom 13.09.2026 bleibt zusätzlich ein unabhängiger GitHub-Zeitplan
um `:52` aktiv. Der Job prüft unmittelbar nach Checkout mit dem vorinstallierten
Python und ohne Dependencies die Snapshot-Zeit. Liegt bereits ein gültiger Snapshot
der um `:50` begonnenen Runde vor, entfallen Python-Setup, Installation, Sammlung
und Commit. Ein verspäteter GitHub-Start kann ausgefallene Cloudflare-Aufrufe auch
nach der vollen Stunde nachholen. Manuelle Starts werden nur dann übersprungen,
wenn ein gültiger Snapshot höchstens zwei Minuten alt ist. Pushes prüfen neuen Code
immer durch einen echten Collector-Lauf. Concurrency serialisiert alle diese Wege.

**Direkt manuell starten:**
https://github.com/exolinodev/dot-market-monitor/actions/workflows/market-data.yml
→ `Run workflow` → Branch `main` → `Run workflow`.
Alternativ: `gh workflow run market-data.yml --repo exolinodev/dot-market-monitor --ref main`.
Ein angenommener Start ist noch kein frischer Snapshot; danach Daten-Commit und
`meta.generated_at_utc` kontrollieren.

## Ablauf und Grenzen

Die folgenden Absätze beschreiben den Stundentakt bis zum 20.09.2026 (Runde ab
`:50`, GitHub-Ersatz `:52`). Seit dem 21.09.2026 gilt die Viertelstundenrunde aus
dem ersten Abschnitt: Vorwärm-Dispatch bei `:58/:13/:28/:43`, Grenze `:00/:15/:30/:45`,
GitHub-Ersatz `:03/:18/:33/:48`, höchstens zwei Versuche pro Viertelstundenrunde.
Die Mechanik (Doppelstartprüfung, Frischeprüfung, Nachholen) ist unverändert.

Neue oder geänderte Cron-Konfigurationen können laut Cloudflare bis zu 15 Minuten
zur Übernahme benötigen. Deshalb wird nach einer Einrichtung ein tatsächlicher
Cloudflare-Start geprüft; eine erfolgreiche Konfigurationsantwort allein genügt
nicht. Entfernte Trigger werden während dieser Übernahmephase ohne Start ignoriert.
Quelle: https://developers.cloudflare.com/workers/configuration/cron-triggers/

* Cloudflare prüft alle fünf Minuten. Eine neue Stundenrunde beginnt um `:50 UTC`.
* Um `:50` ist ein erster Start erlaubt. Ab `:55` können fehlende Daten mit einem
  zweiten Versuch nachgeholt werden. Frische Daten oder ein aktiver Collector
  verhindern weitere Starts. Es bleiben höchstens zwei Versuche pro Stundenrunde.
* Ziel ist die folgende volle Stunde. Verspätete Ereignisse prüfen die bei ihrem
  tatsächlichen Eintreffen aktuelle Runde. Auch nach der vollen Stunde werden
  fehlende Daten nachgeholt; der frühere harte Abbruch ist entfernt.
* Der unabhängige GitHub-Zeitplan um `:52` benötigt keinen Cloudflare-Zugang.

Eine Runde beginnt um `:50`. Ein Snapshot ist dafür aktuell, wenn
`meta.generated_at_utc >= Rundenbeginn`, höchstens 60 Sekunden in der Zukunft liegt,
`meta.fresh == true` und `meta.status` entweder `ok` oder `partial` ist. `partial`
bleibt ausdrücklich ein Ergebnis mit Quellenlücken. Die Prüfung liest zuerst den
SHA von `main`, danach die Snapshot-Datei an genau diesem Commit über die GitHub-API.
Der veränderliche GitHub-Raw-Cache wird dabei umgangen.

Als laufend zählen `queued`, `in_progress`, `waiting`, `pending` und `requested`.
Auch alte laufende Collector-Jobs verhindern einen weiteren Start. Unbekannte
API-Zustände, HTTP-Fehler und ungültige Antworten brechen die Steuerung ab, ohne
vorsorglich weitere Jobs zu erzeugen. Maximal zwei in dieser Runde registrierte
Collector-Läufe sind erlaubt; manuelle und durch Push gestartete Läufe zählen mit.
Ein POST wird bei Timeout/HTTP-Fehler niemals blind wiederholt. Der nächste
Kontrolltermin prüft erneut den tatsächlichen GitHub-Zustand.

Die Run-Liste von GitHub kann verzögert aktualisiert werden. Deshalb ist dies keine
atomare Exactly-once-Garantie. Die vorhandene GitHub-Concurrency-Gruppe verhindert
gleichzeitige Collector-Ausführung. Auch Cloudflare und extern gestartete
GitHub-Runner bieten hier keine minutengenaue Verfügbarkeitsgarantie. Die Zeitstempel
der Daten sind die tatsächlichen Erfassungszeiten vor der vollen Stunde.

## Konto, Rechte und Kosten

Worker: `dot-market-scheduler`, Cloudflare-Konto
`daeba6ff3204db3296d50477ce7dfc5c`. Konfiguration und Code liegen unter
`src/scheduler/`; Tests unter `tests/scheduler.test.mjs`.

Zwölf kurze Prüfungen stündlich ergeben regulär 288 Worker-Ausführungen täglich.
Nur bei Bedarf wird ein GitHub-Collector gestartet; der Kontrollaufruf allein
verbraucht keine GitHub-Runner-Minuten. Der Worker benötigt keine Datenbank, KV,
Durable Objects oder kostenpflichtigen Zusatzdienste. Er ist für das Workers-Free-
Kontingent ausgelegt. Der unabhängige GitHub-Ersatzzeitplan benötigt zusätzlich
24 kurze Runner-Jobs täglich, auch wenn die Sammlung übersprungen wird. Diese
verbrauchen Runtime. GitHub-Standard-Runner bleiben für dieses öffentliche Repo
kostenlos. Limits: https://developers.cloudflare.com/workers/platform/limits/

Cloudflare-Secrets (niemals im Repository speichern):

* `GITHUB_TOKEN`: Fine-grained PAT nur für `exolinodev/dot-market-monitor`,
  **Actions: Read and write**, **Contents: Read-only**, automatisch Metadata:
  Read-only. Keine Inhalts-Schreibrechte, keine Account-Berechtigungen. GitHub
  erlaubt dem Token darüber hinaus das Lesen ohnehin öffentlicher Repositories.
* `CONTROL_TOKEN`: zufälliger separater Schlüssel für manuelle Administration.

Der GitHub-Zugang muss vor seinem Ablauf erneuert und als Worker-Secret ersetzt
werden. Ein abgelaufener/entzogener Zugang führt zu einem sichtbaren HTTP-Fehler in
den Worker-Logs. Es ist kein gesonderter Benachrichtigungsdienst eingerichtet.
Der bei der Einrichtung angelegte Zugang läuft am **11. Dezember 2026** ab.

## Betrieb

```bash
# Abhängigkeiten für Marktcode bleiben von Wrangler getrennt.
node --test tests/scheduler.test.mjs
npx --yes wrangler@4.131.1 deploy --config src/scheduler/wrangler.jsonc --dry-run
npx --yes wrangler@4.131.1 secret put GITHUB_TOKEN --config src/scheduler/wrangler.jsonc
npx --yes wrangler@4.131.1 secret put CONTROL_TOKEN --config src/scheduler/wrangler.jsonc
npx --yes wrangler@4.131.1 deploy --config src/scheduler/wrangler.jsonc
npx --yes wrangler@4.131.1 tail --config src/scheduler/wrangler.jsonc --format json
```

Die Secret-Werte interaktiv eingeben, nicht als Kommandozeilenargumente oder in
Logs schreiben. Wrangler-Version ist für diese Einrichtung gepinnt; es ist keine
Node-Installation oder Wrangler-Installation im stündlichen Collector erforderlich.

`ENABLED=false` ist die sichere Erstbereitstellung bzw. Pause: Cron- und manuelle
Aufrufe starten dann keine Jobs. Erst nach Einrichten der Secrets und erfolgreichem
Test wird `ENABLED=true` gesetzt. Secrets bleiben bei normalen Deployments erhalten.
Der unabhängige `schedule`-Block im GitHub-Collector bleibt als Ersatz aktiv;
Push-Trigger und `workflow_dispatch` bleiben ebenfalls verfügbar.

Worker-URL: https://dot-market-scheduler.dot-market-monitor.workers.dev

* `GET /health`: öffentlich, nur Konfigurationsstatus. **Kein** Beleg für aktuelle
  Marktdaten oder gültige GitHub-Anmeldung.
* `GET /status`: mit `Authorization: Bearer <CONTROL_TOKEN>` aktuelle, nur lesende
  Prüfung. `action=dispatch` bedeutet dort lediglich, dass ein Start nötig wäre.
* `POST /check`: geschützt; führt die Kontrollentscheidung für die aktuelle Runde
  aus, gegebenenfalls mit einem Start.
* `POST /run`: geschützt; manueller Abruf mit aktuellem Zeitpunkt als Frischeziel,
  ebenfalls ohne Doppelstart bei laufendem Collector.

Die strukturierten Worker-Logs unterscheiden `dispatched`, `fresh`,
`already_running`, `attempt_limit`, `disabled` und Fehler. Ein angenommener
Startauftrag ist noch kein erfolgreicher Datenlauf: dafür müssen anschliessend
Actions-Ergebnis, Daten-Commit und Snapshot-Zeitstempel geprüft werden.

## Inbetriebnahme

Am 12. September 2026 durch einen echten Cloudflare-Cron geprüft:
[Collector-Lauf 34720956471](https://github.com/exolinodev/dot-market-monitor/actions/runs/34720956471),
gestartet 21:47:41 UTC, erfolgreicher Daten-Commit `ec97c92` um 21:48 UTC,
42 Quellen ohne Fehler. Der zeitlich begrenzte Test-Cron wurde danach entfernt.
Die Worker-spezifische HTTP-Behandlung wurde zusätzlich in der lokalen
Cloudflare-Workers-Laufzeit geprüft. Weiterleitungen werden mit `redirect: manual`
unterbunden und als HTTP-Fehler behandelt; der von Node unterstützte Modus `error`
ist in Workers nicht verfügbar.

## Ausfall am 13. September 2026

Der letzte automatische Collector vor der Lücke startete am 12.09. um 22:50 UTC.
Cloudflare-Konfiguration und Secrets waren weiterhin vorhanden. Die abgefragte
Cloudflare-Statistik enthielt für die späteren Stunden keine Invocations; sie
belegt keine konkrete Fehlerursache. Es gab keinen Nachweis eines abgelaufenen
GitHub-Tokens oder eines fehlgeschlagenen Collectors. Der manuelle Wiederanlauf
[34733834467](https://github.com/exolinodev/dot-market-monitor/actions/runs/34733834467)
veröffentlichte um 02:46 UTC frische Daten. Deshalb wird Cloudflare jetzt durch
einen unabhängigen GitHub-Zeitplan ergänzt. Ein einzelner erfolgreicher Test
ist ausdrücklich keine Garantie für spätere pünktliche Cron-Ausführungen.

## Code-Push nach veröffentlichter v4-Runde

Auch ein Code-Push respektiert die bereits veröffentlichte Stundenrunde. Am
21.09.2026 versuchte [Lauf 35573734331](https://github.com/exolinodev/dot-market-monitor/actions/runs/35573734331)
nach einem Push um 07:36 UTC erneut die Runde 07:00 zu verarbeiten, obwohl das
Paper-Ledger bereits bis 07:29 fortgeschritten war. Der Ledger-Guard lehnte das
ab; bestehende Daten wurden nicht überschrieben. Die Sonderregel, jeden Push
ungeachtet des Snapshots sammeln zu lassen, ist deshalb entfernt. Ein frischer
`ok`- oder `partial`-Snapshot derselben Runde unterbindet den Wiederholungslauf.
Fehlt die aktuelle Runde, wird weiterhin gesammelt. Änderungen am Collector
werden beim nächsten noch nicht veröffentlichten Zyklus wirksam; bestehende
Rundenevidenz wird nicht zur sofortigen Code-Aktivierung neu geschrieben.
