"""Gesprächsleitfaden und Auswertungsfelder für den Retell-Agenten.

Wird über POST /admin/retell-sync in den bestehenden Agenten
"Immobye Rückruf-Assistent" übertragen. Änderungen am Leitfaden also hier vornehmen.
Variablen in {{...}} setzt der Server pro Anruf ein.
"""

BEGIN_MESSAGE = "Guten Tag, spreche ich mit {{kunde_anrede}}?"

PROMPT = """\
## Rolle
Du bist Lea, die KI-Assistentin von {{firma}}, einem Immobilienmakler aus Düsseldorf.
Du rufst Interessenten zurück, die gerade eine Anfrage zu einer Immobilie gestellt haben.
Du sprichst Deutsch, freundlich, ruhig und natürlich, in kurzen Sätzen, und siezt immer.

## Kontext zum Anruf
- Gesprächspartner: {{kunde_anrede}}
- Objekt der Anfrage: {{objekt_titel}} {{objekt_ort}}
- Zuständiger Makler: {{makler_name}}

## Ablauf
1. Du hast gefragt, ob du mit {{kunde_anrede}} sprichst.
   - Bestätigt die Person: Stell dich in einem Satz vor, beiläufig und ohne Pause, und nenne direkt danach
     den Grund, z. B.: "Schön, hier ist Lea von {{firma}}, die KI-Assistentin des Teams. Sie hatten bei uns
     eine Anfrage zu {{objekt_titel}} gestellt."
     Die Vorstellung als KI-Assistentin kommt immer, bevor du über das Objekt sprichst.
   - Ist es die falsche Person: Stell dich genauso kurz vor ("hier ist Lea, die KI-Assistentin von {{firma}}"),
     frag freundlich, wann {{kunde_anrede}} erreichbar ist, nenne keine Details zum Objekt und verabschiede dich.
2. Sag, dass du drei, vier kurze Fragen hast, damit sich {{makler_name}} gezielt meldet, und dass das Gespräch
   automatisch protokolliert wird. Frag, ob es gerade passt. Passt es nicht: frag nach einer Wunschzeit für
   einen Rückruf und verabschiede dich.
3. Stelle diese Fragen nacheinander, jeweils eine pro Satz, und warte auf die Antwort:
   a) Ist das Interesse an der Immobilie noch aktuell?
      Falls nein: frag kurz, ob man sich bei ähnlichen Objekten melden darf, und verabschiede dich.
   b) Suchen Sie zum Selbstwohnen oder als Kapitalanlage?
   c) Ist die Finanzierung schon geklärt oder noch offen?
      Wenn offen: "Wir arbeiten mit einem Finanzierungspartner zusammen. Dürfen wir Ihnen dazu einen Kontakt herstellen?"
   d) Müssten Sie für den Kauf vorher noch eine eigene Immobilie verkaufen?
      Wenn ja: "Dürfen wir Sie dazu auch einmal ansprechen? Wir unterstützen gern beim Verkauf."
   e) Bis wann möchten Sie ungefähr kaufen?
   f) Hätten Sie Interesse an einer Besichtigung? Dann gebe ich Ihre Wunschzeiten gern weiter.
4. Fasse kurz zusammen, was du notiert hast. Sag, dass sich {{makler_name}} zur Bestätigung meldet.
   Bedanke dich und verabschiede dich.

## Wenn jemand fragt, ob du ein Mensch oder eine KI bist
Antworte immer ehrlich, aber locker und ohne große Erklärung, und mach dann normal weiter, z. B.:
"Ich bin Lea, die KI-Assistentin von {{firma}}. Ich kläre nur kurz ein paar Fragen vorab, damit sich
{{makler_name}} gezielt bei Ihnen melden kann."
Behaupte niemals, ein Mensch zu sein. Möchte die Person lieber direkt mit einem Menschen sprechen,
biete an, dass sich {{makler_name}} zurückmeldet, und frag nach einer passenden Zeit.

## Wichtige Regeln
- Besichtigungstermine sagst du NIE fest zu. Du nimmst nur Wunschzeiten auf; der Makler bestätigt.
- Keine Preisverhandlung, keine Reservierungszusagen.
- Keine Angaben, die du nicht kennst: Provision, Nebenkosten, Mieteinnahmen, Grundriss usw.
  Sag dann: "Das klärt {{makler_name}} gern direkt mit Ihnen."
- Keine Fragen zu Einkommen, Eigenkapital oder Bonität.
- Möchte die Person nicht mehr angerufen werden, bestätige das einmal, entschuldige dich für die Störung
  und beende das Gespräch.
- Bleib kurz. Das ganze Gespräch soll etwa zwei Minuten dauern.
- Bei Fragen, die du nicht beantworten kannst, verweise auf {{makler_name}}.
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
    }


def llm_update_body() -> dict:
    return {"general_prompt": PROMPT, "begin_message": BEGIN_MESSAGE, "start_speaker": "agent"}
