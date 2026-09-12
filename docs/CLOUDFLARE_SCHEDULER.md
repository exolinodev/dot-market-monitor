# Stündlicher Cloudflare-Starter

Cloudflare führt keine Marktberechnungen aus. Der Worker startet den bestehenden
GitHub-Actions-Collector und prüft, ob dieser einen aktuellen Snapshot veröffentlicht
hat. Der Python-Code und alle Marktquellen bleiben unverändert.

## Ablauf und Grenzen

* `:50 UTC`: Erster Start, sofern kein Collector läuft und kein Snapshot dieser
  Stundenrunde vorliegt.
* `:55 UTC`: Kontrolle. Bei einem laufenden Job wird kein weiterer angelegt. Fehlt
  ein aktueller Snapshot nach abgeschlossenem/fehlgeschlagenem Lauf, ist ein
  Wiederholungsversuch erlaubt. Ohne ersten Lauf kann die Kontrolle diesen nachholen.
* Ziel ist die folgende volle Stunde. Nach der vollen Stunde eingetroffene alte
  Cron-Ereignisse werden als Fehler protokolliert und nicht verspätet ausgeführt.

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

Zwei Cron-Aufrufe stündlich ergeben regulär 48 kurze Worker-Ausführungen täglich.
Nur bei Bedarf wird ein GitHub-Collector gestartet; der Kontrollaufruf allein
verbraucht keine GitHub-Runner-Minuten. Der Worker benötigt keine Datenbank, KV,
Durable Objects oder kostenpflichtigen Zusatzdienste. Er ist für das Workers-Free-
Kontingent ausgelegt. GitHub-Standard-Runner bleiben für dieses öffentliche Repo
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
Nach erfolgreichem Umstieg wird ausschliesslich der `schedule`-Block im
GitHub-Collector entfernt; Push-Trigger und `workflow_dispatch` bleiben verfügbar.

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

## Manuelle Kontrolle über GitHub

`Check Cloudflare scheduler` ist ein ausschliesslich manuell startbarer Hilfsworkflow
mit den Aktionen `status` (nur lesen), `run` (manuell sammeln) und `check` (Kontrolle
mit möglichem Wiederholungsversuch). Er prüft den tatsächlich bereitgestellten
Worker von einem GitHub-Runner aus und benötigt weder Checkout noch Abhängigkeiten.
Das Repository-Secret `SCHEDULER_CONTROL_TOKEN` enthält hierfür denselben separaten
Verwaltungsschlüssel wie `CONTROL_TOKEN` im Worker. **Der GitHub-PAT bleibt nur in
Cloudflare.** Der Hilfsworkflow läuft nie automatisch stündlich und verbraucht nur
bei manueller Verwendung zusätzliche Runner-Zeit.

```bash
gh workflow run scheduler-check.yml --repo exolinodev/dot-market-monitor -f action=status
gh workflow run scheduler-check.yml --repo exolinodev/dot-market-monitor -f action=run
```
