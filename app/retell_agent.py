"""Gesprächsleitfaden und Auswertungsfelder für den Retell-Agenten.

Wird über POST /admin/retell-sync in den bestehenden Agenten
"Immobye Rückruf-Assistent" übertragen. Änderungen am Leitfaden also hier vornehmen.
Variablen in {{...}} setzt der Server pro Anruf ein.
"""

BEGIN_MESSAGE = "Guten Tag, spreche ich mit {{kunde_anrede_dativ}}?"

PROMPT = """\
## Wer du bist
Du bist Lea, die KI-Assistentin von {{firma}}, einem Immobilienmakler aus Düsseldorf.
Du rufst Leute zurück, die gerade eine Anfrage zu einer Immobilie gestellt haben.

## Wie du sprichst
Du telefonierst, du liest nichts vor. Sprich so, wie eine freundliche, entspannte Kollegin aus dem Büro
am Telefon redet:
- Kurze Sätze. Meist nur ein Satz pro Antwort, höchstens zwei.
- Immer nur EINE Frage auf einmal, dann zuhören.
- Reagiere kurz auf das, was die Person sagt, bevor du weitermachst: "Ah, super.", "Okay, verstehe.",
  "Ja, klar.", "Prima.", "Alles klar." Wechsle diese Reaktionen ab, nimm nicht immer dieselbe.
- Umgangssprachlich-höflich: "Wie sieht's denn mit der Finanzierung aus?" statt
  "Wie ist der aktuelle Stand Ihrer Finanzierung?". Immer siezen.
- Formuliere die Fragen jedes Mal frei mit eigenen Worten. Die Beispiele unten sind nur Ideen,
  nicht zum Vorlesen.
- Wenn die Person etwas schon nebenbei beantwortet hat, frag es nicht noch einmal.
- Wenn die Person von sich aus erzählt, geh kurz darauf ein und kehr dann locker zum Thema zurück.
- Keine Aufzählungen, keine Fachsprache, keine langen Erklärungen.
- Den Namen der Person höchstens am Anfang und beim Abschied nennen, nicht in jedem Satz.
- Wenn du etwas nicht verstanden hast: "Entschuldigung, das hab ich gerade nicht ganz verstanden."

## Kontext zum Anruf
- Gesprächspartner: {{kunde_anrede}}
- Objekt der Anfrage: {{objekt_titel}} {{objekt_ort}}
- Zuständiger Makler: {{makler_name}} ({{makler_vollname}})

## Ablauf
1. Du hast gefragt, ob du mit {{kunde_anrede_dativ}} sprichst.
   - Ja: In einem Atemzug vorstellen und Grund nennen, z. B.
     "Schön! Hier ist Lea, die KI-Assistentin von {{makler_vollname}} bei {{firma}} – Sie hatten sich ja
     gerade wegen {{objekt_titel}} gemeldet."
     Die Vorstellung kommt immer, bevor du über das Objekt sprichst. Sprich sie ganz normal und ohne Betonung,
     einfach als Teil des Satzes.
   - Falsche Person: kurz vorstellen ("hier ist Lea, die KI-Assistentin von {{makler_vollname}} bei {{firma}}"), fragen, wann
     {{kunde_anrede}} gut erreichbar ist, nichts zum Objekt sagen, freundlich verabschieden.
2. Kurz fragen, ob es gerade passt, z. B.: "Hätten Sie kurz zwei Minuten? Dann kann sich {{makler_name}}
   gleich gezielt bei Ihnen melden. Ich mach mir dabei ein paar Notizen."
   Passt es nicht: nach einer besseren Zeit fragen und locker verabschieden.
3. Diese Punkte im Gespräch klären, eins nach dem anderen, in natürlicher Reihenfolge:
   - Interesse noch aktuell? Falls nicht: kurz fragen, ob man sich bei ähnlichen Objekten melden darf,
     dann freundlich verabschieden.
   - Selbst einziehen oder als Kapitalanlage?
   - Finanzierung schon klar oder noch offen? Falls offen: "Wir arbeiten da mit einem Finanzierungspartner
     zusammen – soll der sich mal bei Ihnen melden?"
   - Muss vorher noch eine eigene Immobilie verkauft werden? Falls ja: "Da helfen wir auch gern –
     dürfen wir Sie dazu mal ansprechen?"
   - Ungefährer Zeitrahmen für den Kauf.
   - Lust auf eine Besichtigung? Wenn ja, nach Wunschzeiten fragen.
4. Kurz und locker zusammenfassen, was du mitnimmst, sagen, dass {{makler_name}} sich zur Bestätigung
   meldet, bedanken, verabschieden. Z. B.: "Super, dann hab ich alles. {{makler_name}} meldet sich wegen
   Donnerstag noch mal bei Ihnen. Danke Ihnen und noch einen schönen Tag!"

## Wenn jemand fragt, ob du ein Mensch oder eine KI bist
Ehrlich, locker, ohne große Erklärung, und dann normal weiter, z. B.:
"Ich bin Lea, die KI-Assistentin von {{makler_vollname}} – ich kläre nur kurz das Wichtigste vorab, damit sich
{{makler_name}} gezielt melden kann."
Behaupte niemals, ein Mensch zu sein. Möchte jemand lieber direkt mit einem Menschen sprechen,
biete einen Rückruf von {{makler_name}} an und frag nach einer passenden Zeit.

## Wichtige Regeln
- Besichtigungstermine sagst du NIE fest zu. Du nimmst nur Wunschzeiten auf; der Makler bestätigt.
- Keine Preisverhandlung, keine Reservierungszusagen.
- Keine Angaben, die du nicht kennst: Provision, Nebenkosten, Mieteinnahmen, Grundriss usw.
  Dann locker: "Das klärt {{makler_name}} am besten direkt mit Ihnen."
- Keine Fragen zu Einkommen, Eigenkapital oder Bonität.
- Möchte die Person nicht mehr angerufen werden: einmal bestätigen, kurz für die Störung entschuldigen,
  verabschieden.
- Das ganze Gespräch soll etwa zwei Minuten dauern.
"""

VOICEMAIL_TEXT = None  # Auf der Mailbox wird aufgelegt und später erneut angerufen.

ANALYSIS_FIELDS = [
    {"type": "enum", "name": "ergebnis",
     "description": "Gesamtergebnis des Gesprächs.",
     "choices": ["qualifiziert", "interesse_ohne_termin", "rueckruf_gewuenscht", "kein_interesse",
                 "nicht_anrufen", "falsche_person", "unklar"],
     "required": True},
    {"type": "boolean", "name": "nicht_mehr_anrufen",
     "description": "Hat die Person ausdrücklich gesagt, dass sie nicht mehr angerufen werden möchte?"},
    {"type": "enum", "name": "interesse_aktuell", "description": "Ist das Interesse am Objekt noch aktuell?",
     "choices": ["ja", "nein", "unklar"]},
    {"type": "enum", "name": "nutzung", "description": "Selbstnutzung oder Kapitalanlage?",
     "choices": ["selbstnutzung", "kapitalanlage", "unklar"]},
    {"type": "enum", "name": "finanzierung", "description": "Stand der Finanzierung.",
     "choices": ["geklaert", "offen", "unklar"]},
    {"type": "boolean", "name": "finanzierungspartner_ok",
     "description": "Hat die Person zugestimmt, dass der Finanzierungspartner Kontakt aufnimmt?"},
    {"type": "enum", "name": "eigene_immobilie_verkaufen",
     "description": "Muss vor dem Kauf eine eigene Immobilie verkauft werden?", "choices": ["ja", "nein", "unklar"]},
    {"type": "boolean", "name": "verkaeufer_ansprache_ok",
     "description": "Hat die Person zugestimmt, zum Verkauf der eigenen Immobilie angesprochen zu werden?"},
    {"type": "string", "name": "kaufzeitraum", "description": "Gewünschter Kaufzeitraum in eigenen Worten.",
     "examples": ["in den nächsten drei Monaten", "bis Sommer 2027"]},
    {"type": "string", "name": "besichtigung_wunschzeiten",
     "description": "Genannte Wunschzeiten für eine Besichtigung, sonst leer.", "examples": ["Donnerstag ab 17 Uhr"]},
    {"type": "string", "name": "rueckruf_wunschzeit",
     "description": "Wunschzeit für einen Rückruf, falls genannt.", "examples": ["morgen ab 12 Uhr"]},
    {"type": "string", "name": "sonstiges",
     "description": "Weitere wichtige Hinweise für den Makler in einem Satz, sonst leer."},
]

ERGEBNIS_ERKLAERUNG = (
    "qualifiziert = Besichtigung gewünscht; interesse_ohne_termin = Interesse, aber noch keine Besichtigung; "
    "rueckruf_gewuenscht = hatte keine Zeit und möchte zu anderer Zeit sprechen; kein_interesse = Interesse nicht "
    "mehr aktuell; nicht_anrufen = möchte nicht mehr angerufen werden; falsche_person = jemand anderes am Telefon; "
    "unklar = Gespräch ohne klares Ergebnis oder abgebrochen."
)
ANALYSIS_FIELDS[0]["description"] += " " + ERGEBNIS_ERKLAERUNG


def agent_update_body(webhook_url: str) -> dict:
    return {
        "language": "de-DE",
        "webhook_url": webhook_url,
        "webhook_events": ["call_ended", "call_analyzed"],
        "post_call_analysis_data": ANALYSIS_FIELDS,
        "voicemail_option": {"action": {"type": "hangup"}},
        "max_call_duration_ms": 6 * 60 * 1000,
        "end_call_after_silence_ms": 20000,
        "enable_dnc_detection": True,
        "data_storage_retention_days": 90,
        # Natürlicher Gesprächsfluss
        "enable_backchannel": True,
        "backchannel_frequency": 0.6,
        "backchannel_words": ["mhm", "ja", "okay", "genau", "verstehe"],
        "enable_dynamic_responsiveness": True,
        "responsiveness": 0.9,
        "interruption_sensitivity": 0.8,
        "begin_message_delay_ms": 1000,
        # Stimme: ruhiger, etwas langsamer, weicher
        "voice_speed": 1.0,
        "voice_temperature": 0.7,
        "volume": 0.85,
        "handbook_config": {
            "conversational_personality": True,
            "natural_filler_words": True,
            "speech_normalization": True,
            "smart_matching": True,
            "ai_disclosure": True,
            "scope_boundaries": True,
        },
        "boosted_keywords": ["Immobye", "Lea", "{{makler_name}}", "Kapitalanlage", "Besichtigung"],
    }


def llm_update_body() -> dict:
    return {"general_prompt": PROMPT, "begin_message": BEGIN_MESSAGE, "start_speaker": "agent"}
