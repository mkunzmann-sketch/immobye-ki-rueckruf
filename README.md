# Immobye KI-Rückruf

Ruft Interessenten kurz nach ihrer Objektanfrage zurück, stellt die Fragen aus dem Gesprächsleitfaden
und schreibt das Ergebnis als Notiz, Aufgabe und Merkmal nach Propstack.

## Ablauf

1. Alle 3 Minuten holt der Server neue **Interessenten-Anfragen** aus Propstack.
2. Pro Anfrage wird geprüft: Telefonnummer vorhanden? Merkmal „KI: nicht anrufen“? Interne Sperrliste?
   Schon einmal mit dieser Person zu diesem Objekt gesprochen? Kontakt archiviert oder DSGVO widerrufen?
3. Ca. 5 Minuten nach Eingang ruft der Assistent über Retell an – nur **Mo–Fr 9–19 Uhr, Sa 10–14 Uhr**,
   nie sonntags oder an NRW-Feiertagen.
4. Niemand dran oder Mailbox: neuer Versuch nach 2 Stunden, dann am nächsten Tag. Nach 3 Versuchen Schluss.
   Sobald ein Gespräch zustande kam, wird nie ein zweites Mal angerufen.
5. Nach dem Gespräch schickt Retell die Auswertung; der Server legt in Propstack an:

| Ergebnis | Notiz | Aufgabe | Merkmal |
|---|---|---|---|
| Qualifiziert (Besichtigung gewünscht) | ja | ja, fällig in 2 Std. | KI: qualifiziert |
| Interesse, noch kein Termin | ja | ja | – |
| Rückruf gewünscht | ja | ja | – |
| Kein Interesse mehr | ja | nein | KI: kein Interesse |
| Möchte nicht mehr angerufen werden | ja | nein | KI: nicht anrufen + Sperrliste |
| Falsche Person | ja | ja | – |
| Ohne klares Ergebnis | ja | ja | – |
| Nicht erreicht (3 Versuche) | ja | nein | – |

Zusätzlich: Zustimmung Finanzierungspartner → „KI: Finanzierungspartner ok“ + Aufgabe;
Zustimmung zur Verkaufsansprache → „KI: Verkäufer-Lead“ + Aufgabe.
Aufgaben gehen an den Betreuer des Objekts. Fällt eine Aufgabe in den Feierabend, wird sie auf den nächsten
Morgen gelegt.

## Betriebsarten (`MODE`)

- `dry` – holt Anfragen und protokolliert, ruft niemanden an, schreibt nichts nach Propstack.
- `test` – ruft nur Nummern aus `TEST_NUMBERS` an und schreibt nur für diese nach Propstack.
- `live` – Echtbetrieb.

## Einrichtung bei Render

1. Repository mit Render verbinden („New → Blueprint“ nutzt `render.yaml`).
2. Unter *Environment* eintragen: `PROPSTACK_API_KEY`, `RETELL_API_KEY`, `RETELL_AGENT_ID`,
   `RETELL_FROM_NUMBER`, `PUBLIC_BASE_URL` (die Render-Adresse), `TEST_NUMBERS`.
3. `GET /admin/propstack-check` (Header `X-Admin-Token`) – prüft die Verbindung und zeigt Nutzer- und
   Aktivitätstyp-IDs. Passende IDs bei Bedarf als `PROPSTACK_NOTE_TYPE_ID`, `PROPSTACK_TASK_TYPE_ID`,
   `PROPSTACK_DEFAULT_BROKER_ID` eintragen.
4. `POST /admin/retell-sync?publish=true` – überträgt Leitfaden, Auswertungsfelder und Webhook-Adresse in
   den Retell-Agenten.
5. `MODE=test`, dann `POST /admin/testanruf` mit `{"client_id": …, "property_id": …}` für einen
   Testkontakt mit eigener Handynummer.
6. Nach erfolgreichem Probebetrieb `MODE=live`.

## Propstack-Rechte des API-Schlüssels

Kontakte: Lesen, Anzeigen, Aktualisieren · Objekte: Lesen, Anzeigen · **Aktivitäten: Lesen, Anzeigen** ·
Notizen/Aufgaben/Termine: Lesen, Anzeigen, Erstellen, Aktualisieren · Merkmale: Lesen, Anzeigen ·
Nutzer: Lesen · Aktivitätstypen: Lesen.

## Leitfaden ändern

Gesprächstext und Auswertungsfelder stehen in `app/retell_agent.py`. Nach einer Änderung neu ausrollen und
`/admin/retell-sync?publish=true` aufrufen.

## Tests

```
pip install -r requirements-dev.txt
pytest
```
