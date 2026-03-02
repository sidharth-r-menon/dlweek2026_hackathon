"""
Crisis LLM Service — Azure OpenAI-powered crisis speech analysis.

Extracts structured incident information from raw, fragmented,
emotionally distressed, multilingual transcripts using a
specialised prompt architecture.
"""

import json
import logging
from typing import Optional
from openai import AzureOpenAI
from config import settings
from models import IncidentCard, LocationAnchor, ConfidenceRatings

logger = logging.getLogger(__name__)

# ── System prompt — Crisis Linguistics Engine ────────────────

CRISIS_SYSTEM_PROMPT = """You are a crisis speech analyst for an emergency dispatch system.
You receive raw, fragmented, emotionally distressed speech transcripts
that may be code-switched across multiple languages.

CRITICAL — TRANSCRIPTION ERROR TOLERANCE:
The text you receive is from a speech recognition model that frequently mishears
Singapore-specific proper nouns. You MUST interpret location names liberally based
on phonetic similarity to known Singapore places. Examples of common mishearings:
- "sheng shon" / "shen song" / "sheng song" → Sheng Siong (supermarket)
- "end tuck" / "en tuck" → NTUC FairPrice (supermarket)
- "cow way" / "chua chu" → Chua Chu Kang
- "toa pa yok" / "da ba yao" → Toa Payoh
- "ang mo" / "ong mo kio" → Ang Mo Kio / AMK
- "sem ba wang" / "sembarang" → Sembawang
- "pung gol" / "pung gore" → Punggol
- "sen kang" → Sengkang
- "boo kit" / "bukit" → Bukit (Bukit Timah, Bukit Batok, etc.)
- "void deck" → void deck (ground floor open space in HDB block)
- "HDB" → HDB (public housing block)
When you see phonetically plausible but garbled text, attempt to recover the
intended Singapore location/landmark rather than marking it UNRESOLVED.

EXTRACTION RULES:
1. SELF-CORRECTIONS: If a speaker corrects themselves, use ONLY the corrected value.
   Example: '5 years old no wait old man' → victim_age: elderly

2. REPETITION: Treat repeated content as emphasis, not duplicate information.
   Example: 'blood blood so much blood' → bleeding: confirmed (high confidence)

3. INCOMPLETE SENTENCES: Extract what is stated; mark the rest as UNRESOLVED.
   Never infer unstated information. Flag uncertainty explicitly.

4. CODE-SWITCHING: Treat mixed-language input as a single semantic unit.
   Extract meaning across language boundaries without privileging one language.

5. LOCATION ANCHORS: Extract every spatial reference, no matter how vague.
   Include: street names, landmarks, Chinese/Malay/Tamil POI names, MRT stations,
   block numbers, district names, directional clues ('opposite', 'behind', 'near').

6. CONFIDENCE LEVELS: Rate each field as HIGH / MEDIUM / LOW / UNRESOLVED.
   Be conservative — LOW is better than a wrong HIGH.

OUTPUT FORMAT (strict JSON — no markdown fences, just raw JSON):
{
  "incident_type": "string describing incident type",
  "victim_description": "string describing victim",
  "medical_urgency": "critical | urgent | non-urgent | unknown",
  "threat_present": true | false | null,
  "location_anchors": [
    { "text": "string", "type": "poi|street|landmark|district|mrt|block|relative", "language": "ISO 639-1 code" }
  ],
  "caller_language": "string",
  "caller_emotional_state": "calm | distressed | panic | incoherent",
  "confidence": {
    "incident_type": "HIGH|MEDIUM|LOW|UNRESOLVED",
    "victim_description": "HIGH|MEDIUM|LOW|UNRESOLVED",
    "medical_urgency": "HIGH|MEDIUM|LOW|UNRESOLVED",
    "location": "HIGH|MEDIUM|LOW|UNRESOLVED",
    "threat_present": "HIGH|MEDIUM|LOW|UNRESOLVED"
  },
  "missing_critical_info": ["list of missing but critical fields"],
  "suggested_clarifying_questions": ["max 2 short questions for dispatcher to ask"]
}
"""


class CrisisLLMService:
    """Azure OpenAI-powered crisis information extraction."""

    def __init__(self):
        self.client = AzureOpenAI(
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        )
        self.deployment = settings.AZURE_OPENAI_DEPLOYMENT_NAME

    def extract_incident_card(self, transcript: str, language: str = "en") -> IncidentCard:
        """
        Analyse a raw transcript and return a structured IncidentCard.

        Args:
            transcript: Raw text from Whisper transcription.
            language: Detected language code for context.

        Returns:
            IncidentCard with structured crisis fields.
        """
        user_message = (
            f"Detected language: {language}\n\n"
            f"RAW TRANSCRIPT:\n{transcript}\n\n"
            "Extract the crisis information following the rules above. "
            "Return ONLY valid JSON, no markdown."
        )

        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": CRISIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )

            raw_json = response.choices[0].message.content.strip()
            data = json.loads(raw_json)
            return self._parse_incident(data)

        except json.JSONDecodeError as e:
            logger.error(f"LLM returned invalid JSON: {e}")
            return IncidentCard(error=f"JSON parse error: {e}")
        except Exception as e:
            logger.error(f"Crisis extraction failed: {e}")
            raise

    def extract_with_clarification(
        self,
        original_transcript: str,
        dispatcher_response: str,
        previous_card: IncidentCard,
        language: str = "en",
    ) -> IncidentCard:
        """
        Re-extract after the dispatcher provides clarifying information.
        Injects the dispatcher answer and re-processes with full context.
        """
        enriched_transcript = (
            f"{original_transcript}\n\n"
            f"[DISPATCHER FOLLOW-UP ANSWER]: {dispatcher_response}\n\n"
            f"PREVIOUS EXTRACTION (update as needed):\n{previous_card.model_dump_json(indent=2)}"
        )
        return self.extract_incident_card(enriched_transcript, language)

    # ── Internal helpers ────────────────────────────────────

    def _parse_incident(self, data: dict) -> IncidentCard:
        """Convert raw LLM JSON output into a validated IncidentCard."""
        # Parse location anchors
        anchors = []
        for a in data.get("location_anchors", []):
            anchors.append(
                LocationAnchor(
                    text=a.get("text", ""),
                    type=a.get("type", "poi"),
                    language=a.get("language", "en"),
                )
            )

        # Parse confidence
        conf_data = data.get("confidence", {})
        confidence = ConfidenceRatings(
            incident_type=conf_data.get("incident_type", "UNRESOLVED"),
            victim_description=conf_data.get("victim_description", "UNRESOLVED"),
            medical_urgency=conf_data.get("medical_urgency", "UNRESOLVED"),
            location=conf_data.get("location", "UNRESOLVED"),
            threat_present=conf_data.get("threat_present", "UNRESOLVED"),
        )

        # Map medical urgency safely
        urgency = data.get("medical_urgency", "unknown")
        if urgency not in ("critical", "urgent", "non-urgent", "unknown"):
            urgency = "unknown"

        # Map emotional state safely
        emotional = data.get("caller_emotional_state", "calm")
        if emotional not in ("calm", "distressed", "panic", "incoherent"):
            emotional = "distressed"

        return IncidentCard(
            incident_type=data.get("incident_type", "UNKNOWN"),
            victim_description=data.get("victim_description", ""),
            medical_urgency=urgency,
            threat_present=data.get("threat_present"),
            location_anchors=anchors,
            caller_language=data.get("caller_language", ""),
            caller_emotional_state=emotional,
            confidence=confidence,
            missing_critical_info=data.get("missing_critical_info", []),
            suggested_clarifying_questions=data.get("suggested_clarifying_questions", []),
        )


# Singleton
crisis_llm_service = CrisisLLMService()
