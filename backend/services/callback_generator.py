"""
Callback Script Generator — produces phonetic phrases a monolingual
dispatcher can read aloud to maintain rapport with the caller.

Uses Azure OpenAI to generate culturally appropriate, phonetically
spelled phrases in the caller's detected language.
"""

import json
import logging
from typing import Optional
from openai import AzureOpenAI
from config import settings
from models import CallbackScript, CallbackPhrase, IncidentCard

logger = logging.getLogger(__name__)

CALLBACK_SYSTEM_PROMPT = """You generate emergency dispatcher callback phrases.

Given a detected caller language and an incident context, produce exactly 4 phrases
that a monolingual English-speaking dispatcher can READ ALOUD to the caller.

Each phrase must include:
1. The phrase in the caller's native script
2. A phonetic pronunciation guide using English approximations (NOT IPA)
3. The English translation

The 4 required phrases (by ID):
- P1: REASSURANCE — "Help is on the way, stay calm."
- P2: STAY WITH VICTIM — "Stay with them, do not move them."
- P3: LOCATION CONFIRM — "Are you near [LOCATION]? Say yes or no." (use the location from context)
- P4: KEEP LINE OPEN — "Stay on the phone, do not hang up."

For the phonetic guide, write it so that an English speaker reading it aloud
would produce sounds close to the target language. Use hyphens between syllables.
Example: 救护车马上到 → "Jee-oo-hoo chuh mah-shahng dow"

OUTPUT FORMAT (strict JSON, no markdown):
{
  "language": "ISO 639-1 code",
  "phrases": [
    {
      "phrase_id": "P1",
      "purpose": "Reassurance",
      "english": "Help is on the way, stay calm.",
      "native_script": "native script text here",
      "phonetic": "phonetic guide here",
      "language": "ISO 639-1"
    }
  ]
}
"""


class CallbackGenerator:
    """Generates phonetic callback scripts for dispatchers."""

    # Pre-built scripts for demo / offline mode
    DEMO_SCRIPTS = {
        "zh": CallbackScript(
            language="zh",
            phrases=[
                CallbackPhrase(
                    phrase_id="P1",
                    purpose="Reassurance",
                    english="Help is on the way, stay calm.",
                    native_script="救护车马上到，请保持冷静",
                    phonetic="Jee-oo-hoo chuh mah-shahng dow, ching bow-chir lung-jing",
                    language="zh",
                ),
                CallbackPhrase(
                    phrase_id="P2",
                    purpose="Stay with victim",
                    english="Stay with them, do not move them.",
                    native_script="请留在他身边，不要移动他",
                    phonetic="Ching lee-oh dzai tah shun-bee-en, boo-yow yee-dong tah",
                    language="zh",
                ),
                CallbackPhrase(
                    phrase_id="P3",
                    purpose="Location confirm",
                    english="Are you near Geylang Lorong 38? Say yes or no.",
                    native_script="你在Geylang Lorong 38附近吗？请说'是'或'不是'",
                    phonetic="Nee dzai Geylang Lorong 38 foo-jin mah? Ching shwoh 'shir' hwoh 'boo-shir'",
                    language="zh",
                ),
                CallbackPhrase(
                    phrase_id="P4",
                    purpose="Keep line open",
                    english="Stay on the phone, do not hang up.",
                    native_script="请留在电话线上，不要挂电话",
                    phonetic="Ching lee-oh dzai dee-en-hwah shee-en-shahng, boo-yow gwah dee-en-hwah",
                    language="zh",
                ),
            ],
        ),
        "ms": CallbackScript(
            language="ms",
            phrases=[
                CallbackPhrase(
                    phrase_id="P1",
                    purpose="Reassurance",
                    english="Help is on the way, stay calm.",
                    native_script="Bantuan sedang dalam perjalanan, tenang.",
                    phonetic="Bahn-too-ahn seh-dahng dah-lahm per-jah-lah-nahn, teh-nahng",
                    language="ms",
                ),
                CallbackPhrase(
                    phrase_id="P2",
                    purpose="Stay with victim",
                    english="Stay with them, do not move them.",
                    native_script="Tinggal bersama mereka, jangan gerakkan mereka.",
                    phonetic="Ting-gahl ber-sah-mah meh-reh-kah, jah-ngahn geh-rahk-kahn meh-reh-kah",
                    language="ms",
                ),
                CallbackPhrase(
                    phrase_id="P3",
                    purpose="Location confirm",
                    english="Are you near the location? Say yes or no.",
                    native_script="Adakah anda berhampiran? Cakap ya atau tidak.",
                    phonetic="Ah-dah-kah ahn-dah ber-hahm-pee-rahn? Chah-kahp yah ah-tow tee-dahk",
                    language="ms",
                ),
                CallbackPhrase(
                    phrase_id="P4",
                    purpose="Keep line open",
                    english="Stay on the phone, do not hang up.",
                    native_script="Kekal di talian, jangan letak telefon.",
                    phonetic="Keh-kahl dee tah-lee-ahn, jah-ngahn leh-tahk teh-leh-fohn",
                    language="ms",
                ),
            ],
        ),
        "ta": CallbackScript(
            language="ta",
            phrases=[
                CallbackPhrase(
                    phrase_id="P1",
                    purpose="Reassurance",
                    english="Help is on the way, stay calm.",
                    native_script="உதவி வருகிறது, அமைதியாக இருங்கள்",
                    phonetic="Oo-thah-vee vah-roo-kee-rah-thoo, ah-my-thee-yah-kah ee-roong-kahl",
                    language="ta",
                ),
                CallbackPhrase(
                    phrase_id="P2",
                    purpose="Stay with victim",
                    english="Stay with them, do not move them.",
                    native_script="அவர்களுடன் இருங்கள், அவர்களை நகர்த்தாதீர்கள்",
                    phonetic="Ah-var-kah-loo-dahn ee-roong-kahl, ah-var-kah-lai nah-kar-thah-theer-kahl",
                    language="ta",
                ),
                CallbackPhrase(
                    phrase_id="P3",
                    purpose="Location confirm",
                    english="Are you near the location? Say yes or no.",
                    native_script="நீங்கள் அருகில் இருக்கிறீர்களா? ஆம் அல்லது இல்லை என்று சொல்லுங்கள்",
                    phonetic="Neeng-kahl ah-roo-kil ee-rook-kee-reer-kah-lah? Ahm ahl-lah-thoo ill-lai en-droo sol-loong-kahl",
                    language="ta",
                ),
                CallbackPhrase(
                    phrase_id="P4",
                    purpose="Keep line open",
                    english="Stay on the phone, do not hang up.",
                    native_script="தொலைபேசியில் இருங்கள், வைக்காதீர்கள்",
                    phonetic="Thoh-lai-pay-see-yil ee-roong-kahl, vai-kah-theer-kahl",
                    language="ta",
                ),
            ],
        ),
    }

    def __init__(self):
        self.client = AzureOpenAI(
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        )
        self.deployment = settings.AZURE_OPENAI_DEPLOYMENT_NAME

    def generate(
        self,
        language: str,
        incident_card: Optional[IncidentCard] = None,
        location_context: str = "",
    ) -> CallbackScript:
        """
        Generate a callback script with 4 phonetic phrases.

        Args:
            language: ISO 639-1 language code of the caller.
            incident_card: Optional incident context for phrase customisation.
            location_context: Location string to insert into P3.

        Returns:
            CallbackScript with 4 phrases.
        """
        # For English callers, no phonetic script needed
        if language == "en":
            return CallbackScript(
                language="en",
                phrases=[
                    CallbackPhrase(phrase_id="P1", purpose="Reassurance",
                                   english="Help is on the way, stay calm.",
                                   native_script="Help is on the way, stay calm.",
                                   phonetic="", language="en"),
                    CallbackPhrase(phrase_id="P2", purpose="Stay with victim",
                                   english="Stay with them, do not move them.",
                                   native_script="Stay with them, do not move them.",
                                   phonetic="", language="en"),
                    CallbackPhrase(phrase_id="P3", purpose="Location confirm",
                                   english=f"Are you near {location_context or 'the location'}? Say yes or no.",
                                   native_script=f"Are you near {location_context or 'the location'}? Say yes or no.",
                                   phonetic="", language="en"),
                    CallbackPhrase(phrase_id="P4", purpose="Keep line open",
                                   english="Stay on the phone, do not hang up.",
                                   native_script="Stay on the phone, do not hang up.",
                                   phonetic="", language="en"),
                ],
            )

        # Try LLM generation
        try:
            return self._generate_via_llm(language, incident_card, location_context)
        except Exception as e:
            logger.warning(f"LLM callback generation failed, using demo script: {e}")
            # Fall back to pre-built demo scripts
            return self.DEMO_SCRIPTS.get(language, self.DEMO_SCRIPTS.get("zh"))

    def _generate_via_llm(
        self,
        language: str,
        incident_card: Optional[IncidentCard],
        location_context: str,
    ) -> CallbackScript:
        """Generate callback script via Azure OpenAI."""
        context_parts = [f"Caller language: {language}"]
        if incident_card:
            context_parts.append(f"Incident type: {incident_card.incident_type}")
            context_parts.append(f"Medical urgency: {incident_card.medical_urgency}")
        if location_context:
            context_parts.append(f"Best location match: {location_context}")

        user_message = "\n".join(context_parts)

        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[
                {"role": "system", "content": CALLBACK_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )

        raw_json = response.choices[0].message.content.strip()
        data = json.loads(raw_json)

        phrases = []
        for p in data.get("phrases", []):
            phrases.append(
                CallbackPhrase(
                    phrase_id=p.get("phrase_id", "P1"),
                    purpose=p.get("purpose", ""),
                    english=p.get("english", ""),
                    native_script=p.get("native_script", ""),
                    phonetic=p.get("phonetic", ""),
                    language=p.get("language", language),
                )
            )

        return CallbackScript(language=data.get("language", language), phrases=phrases)

    def get_demo_script(self, language: str) -> CallbackScript:
        """Return a pre-built demo script (no API call)."""
        return self.DEMO_SCRIPTS.get(language, self.DEMO_SCRIPTS.get("zh"))


# Singleton
callback_generator = CallbackGenerator()
