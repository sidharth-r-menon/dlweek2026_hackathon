"""
Demo Scenarios — pre-built simulated emergency call data for
demonstration without requiring live audio or API keys.

Each scenario includes a simulated transcript, pre-built incident card,
location candidates, and callback script — mirroring what the real
pipeline would produce.
"""

from models import (
    IncidentCard,
    LocationAnchor,
    LocationCandidate,
    ConfidenceRatings,
)

# ═══════════════════════════════════════════════════════════════
# Scenario 1: Mandarin Medical Emergency (flagship demo)
# ═══════════════════════════════════════════════════════════════

MANDARIN_MEDICAL = {
    "simulated_transcript": (
        "他... 他倒下了 bleeding I think... 我不知道怎么说... "
        "the temple... 大伯公庙 near my house... please come fast... "
        "he not moving... oh god... 5 years old no wait he old man... "
        "aiyo... still breathing or not I don't know..."
    ),
    "simulated_language": "zh",
    "simulated_incident_card": IncidentCard(
        incident_type="Medical Emergency — Collapse/Unconsciousness",
        victim_description="Elderly male (self-correction applied: discard '5 years old')",
        medical_urgency="critical",
        threat_present=None,
        location_anchors=[
            LocationAnchor(text="大伯公庙", type="poi", language="zh"),
            LocationAnchor(text="near my house", type="relative", language="en"),
        ],
        caller_language="Mandarin/Hokkien + English code-switch",
        caller_emotional_state="panic",
        confidence=ConfidenceRatings(
            incident_type="HIGH",
            victim_description="MEDIUM",
            medical_urgency="HIGH",
            location="LOW",
            threat_present="UNRESOLVED",
        ),
        missing_critical_info=["exact address", "victim breathing status"],
        suggested_clarifying_questions=[
            "Is the person breathing?",
            "Which area are you in — Geylang, Tampines, Jurong?",
        ],
    ),
    "simulated_locations": [
        LocationCandidate(
            name="Tua Pek Kong Temple",
            address="30 Geylang Lorong 38, Singapore 398665",
            latitude=1.3147,
            longitude=103.8880,
            score=87,
            source="osm",
        ),
        LocationCandidate(
            name="Tua Pek Kong Temple",
            address="36 Pagoda Street, Chinatown, Singapore 059195",
            latitude=1.2832,
            longitude=103.8448,
            score=44,
            source="osm",
        ),
        LocationCandidate(
            name="Tua Pek Kong Temple",
            address="6 Jurong West St 64, Singapore 648348",
            latitude=1.3400,
            longitude=103.7020,
            score=31,
            source="datagovsg",
        ),
    ],
}


# ═══════════════════════════════════════════════════════════════
# Scenario 2: Malay Fire Emergency
# ═══════════════════════════════════════════════════════════════

MALAY_FIRE = {
    "simulated_transcript": (
        "Tolong tolong! Api... fire lah the building... "
        "dekat Masjid Sultan... smoke everywhere cannot see... "
        "ada orang dalam... people inside still... cepat cepat! "
        "Arab Street area lah... the shop beside mosque..."
    ),
    "simulated_language": "ms",
    "simulated_incident_card": IncidentCard(
        incident_type="Fire Emergency — Building Fire with Trapped Occupants",
        victim_description="Unknown number of people trapped inside burning building",
        medical_urgency="critical",
        threat_present=None,
        location_anchors=[
            LocationAnchor(text="Masjid Sultan", type="poi", language="ms"),
            LocationAnchor(text="Arab Street", type="street", language="en"),
            LocationAnchor(text="the shop beside mosque", type="relative", language="en"),
        ],
        caller_language="Malay + English code-switch (Singlish markers)",
        caller_emotional_state="panic",
        confidence=ConfidenceRatings(
            incident_type="HIGH",
            victim_description="LOW",
            medical_urgency="HIGH",
            location="MEDIUM",
            threat_present="UNRESOLVED",
        ),
        missing_critical_info=["number of trapped persons", "exact shop unit"],
        suggested_clarifying_questions=[
            "How many people are inside?",
            "Which shop — can you see the shop name?",
        ],
    ),
    "simulated_locations": [
        LocationCandidate(
            name="Masjid Sultan",
            address="3 Muscat Street, Singapore 198833",
            latitude=1.3025,
            longitude=103.8594,
            score=92,
            source="osm",
        ),
        LocationCandidate(
            name="Arab Street",
            address="Arab Street, Kampong Glam, Singapore",
            latitude=1.3022,
            longitude=103.8597,
            score=65,
            source="osm",
        ),
    ],
}


# ═══════════════════════════════════════════════════════════════
# Scenario 3: Tamil Road Accident
# ═══════════════════════════════════════════════════════════════

TAMIL_ACCIDENT = {
    "simulated_transcript": (
        "ஐயோ ஐயோ... accident ah... Little India area... "
        "car hit the uncle... Serangoon Road near Mustafa Centre... "
        "blood coming from the head... he lying on road... "
        "cannot move... very bad... please send ambulance fast..."
    ),
    "simulated_language": "ta",
    "simulated_incident_card": IncidentCard(
        incident_type="Road Traffic Accident — Pedestrian Hit",
        victim_description="Male ('uncle'), head injury with active bleeding, lying on road, immobile",
        medical_urgency="critical",
        threat_present=None,
        location_anchors=[
            LocationAnchor(text="Little India", type="district", language="en"),
            LocationAnchor(text="Serangoon Road", type="street", language="en"),
            LocationAnchor(text="Mustafa Centre", type="poi", language="en"),
        ],
        caller_language="Tamil + English code-switch",
        caller_emotional_state="distressed",
        confidence=ConfidenceRatings(
            incident_type="HIGH",
            victim_description="HIGH",
            medical_urgency="HIGH",
            location="HIGH",
            threat_present="UNRESOLVED",
        ),
        missing_critical_info=["victim consciousness status"],
        suggested_clarifying_questions=[
            "Is the person conscious? Can he speak?",
        ],
    ),
    "simulated_locations": [
        LocationCandidate(
            name="Mustafa Centre",
            address="145 Syed Alwi Road, Singapore 207704",
            latitude=1.3104,
            longitude=103.8554,
            score=94,
            source="osm",
        ),
        LocationCandidate(
            name="Serangoon Road / Mustafa",
            address="Serangoon Road, Little India, Singapore",
            latitude=1.3102,
            longitude=103.8551,
            score=78,
            source="osm",
        ),
    ],
}


# ═══════════════════════════════════════════════════════════════
# Scenario 4: English/Singlish Violence Report
# ═══════════════════════════════════════════════════════════════

SINGLISH_VIOLENCE = {
    "simulated_transcript": (
        "Hello police ah... got fight outside the kopitiam... "
        "Tampines Block 201 there... someone use bottle hit the other guy... "
        "the guy bleeding from the arm... still fighting now... "
        "got like 3-4 people involved lah... very scary..."
    ),
    "simulated_language": "en",
    "simulated_incident_card": IncidentCard(
        incident_type="Violence — Public Altercation with Weapon (Glass Bottle)",
        victim_description="Male victim, bleeding from arm (bottle wound), fight ongoing",
        medical_urgency="urgent",
        threat_present=True,
        location_anchors=[
            LocationAnchor(text="kopitiam", type="poi", language="en"),
            LocationAnchor(text="Tampines Block 201", type="block", language="en"),
        ],
        caller_language="English (Singlish)",
        caller_emotional_state="distressed",
        confidence=ConfidenceRatings(
            incident_type="HIGH",
            victim_description="MEDIUM",
            medical_urgency="MEDIUM",
            location="HIGH",
            threat_present="HIGH",
        ),
        missing_critical_info=["exact kopitiam name or unit"],
        suggested_clarifying_questions=[
            "Is the fight still happening right now?",
        ],
    ),
    "simulated_locations": [
        LocationCandidate(
            name="Block 201 Kopitiam",
            address="201 Tampines Street 21, Singapore 522201",
            latitude=1.3525,
            longitude=103.9445,
            score=90,
            source="datagovsg",
        ),
    ],
}


# ═══════════════════════════════════════════════════════════════
# Scenario Registry
# ═══════════════════════════════════════════════════════════════

SCENARIOS = {
    "mandarin_medical": MANDARIN_MEDICAL,
    "malay_fire": MALAY_FIRE,
    "tamil_accident": TAMIL_ACCIDENT,
    "singlish_violence": SINGLISH_VIOLENCE,
}


def get_scenario(name: str) -> dict:
    """
    Return a demo scenario by name. Defaults to mandarin_medical.
    """
    scenario = SCENARIOS.get(name, MANDARIN_MEDICAL)

    # Serialise Pydantic models so they work with the graph state dict
    result = {}
    for key, value in scenario.items():
        if hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        elif isinstance(value, list):
            result[key] = [
                v.model_dump() if hasattr(v, "model_dump") else v for v in value
            ]
        else:
            result[key] = value
    return result


def list_scenarios() -> list:
    """Return a list of available scenario names and descriptions."""
    return [
        {"id": "mandarin_medical", "name": "Mandarin Medical Emergency", "description": "Elderly man collapses near Tua Pek Kong Temple — Mandarin/English code-switch"},
        {"id": "malay_fire", "name": "Malay Fire Emergency", "description": "Building fire near Masjid Sultan — Malay/English code-switch"},
        {"id": "tamil_accident", "name": "Tamil Road Accident", "description": "Pedestrian hit near Mustafa Centre — Tamil/English code-switch"},
        {"id": "singlish_violence", "name": "Singlish Violence Report", "description": "Public fight at Tampines Block 201 — English/Singlish"},
    ]
