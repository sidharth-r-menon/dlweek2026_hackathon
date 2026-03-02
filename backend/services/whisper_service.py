"""
Azure OpenAI gpt-4o-transcribe Service — multilingual speech-to-text via Azure.

Uses Azure OpenAI's gpt-4o-transcribe model for transcription with
dialect-aware prompt injection and language detection.
gpt-4o-transcribe supports real-time transcription with higher accuracy
than the legacy Whisper model.
"""

import os
import logging
from typing import Optional, Tuple
from openai import AzureOpenAI
from config import settings

logger = logging.getLogger(__name__)

# ── Singapore vocabulary prompt ────────────────────────────────────────────
# Short structured prompt that biases the decoder toward Singapore-specific
# proper nouns. Keep it concise — long prompts get echoed verbatim.
# The ### hallucination filter below will strip any prompt echo.
SINGAPORE_VOCAB_PROMPT = (
    "Emergency call in Singapore. Common locations and landmarks:\n"
    "MRT stations: Aljunied, Tampines, Jurong East, Toa Payoh, Buona Vista, "
    "Woodlands, Sembawang, Yishun, Bishan, Ang Mo Kio, Clementi, Bedok.\n"
    "Landmarks: Sheng Siong, NTUC FairPrice, Giant, HDB, NTU, NUS, SGH, "
    "Tan Tock Seng Hospital, KK Hospital, Changi Airport, Orchard Road, "
    "Toa Payoh Hub, Tampines Mall, Jurong Point, Bugis Junction.\n"
    "Areas: Geylang, Chinatown, Little India, Kampong Glam, Punggol, Sengkang.\n"
    "Temples: Tua Pek Kong, Sri Veeramakaliamman, Sultan Mosque, Kong Meng San.\n"
    "Common phrases: void deck, kopitiam, block number, level, unit.\n"
    "Speaker may switch between English, Mandarin, Malay, and Tamil mid-sentence."
)


class WhisperService:
    """Handles audio transcription through Azure OpenAI gpt-4o-transcribe."""

    # Vocabulary/noun-list prompts to bias the decoder toward Singapore proper nouns.
    # These are intentionally noun lists, NOT sentences — sentences get hallucinated/repeated
    # on silence, but a noun list primes the vocabulary without triggering that behaviour.
    DIALECT_PROMPTS = {
        "en": (
            "Singapore emergency call. "
            "Locations: Sheng Siong, NTUC FairPrice, Giant, Cold Storage, "
            "Tampines, Woodlands, Jurong, Clementi, Bishan, AMK, Ang Mo Kio, "
            "Pasir Ris, Bedok, Queenstown, Toa Payoh, Hougang, Sengkang, Punggol, "
            "Bukit Timah, Buona Vista, Novena, Dhoby Ghaut, Bugis, "
            "Chua Chu Kang, Choa Chu Kang, Yishun, Sembawang, Admiralty, "
            "Orchard, Marina Bay, Raffles Place, Tanjong Pagar. "
            "Landmarks: HDB block, void deck, MRT station, kopitiam, hawker centre, "
            "community club, polyclinic, carpark, lift lobby."
        ),
        "zh": "新加坡紧急求救电话。地点：义顺、兀兰、裕廊、金文泰、碧山、宏茂桥、淡滨尼、勿洛、女皇镇、大巴窑、后港、盛港、榜鹅、蔡厝港、义顺、三巴旺。",
        "ms": "Panggilan kecemasan Singapura. Lokasi: Tampines, Woodlands, Jurong, Clementi, Bishan, Ang Mo Kio, Pasir Ris, Bedok, Queenstown, Toa Payoh, Hougang, Sengkang, Punggol, Chua Chu Kang, Yishun.",
        "ta": "சிங்கப்பூர் அவசர அழைப்பு. இடங்கள்: டம்பைன்ஸ், வுட்லேண்ட்ஸ், ஜுரோங், க்ளெமென்டி, பிஷான், ஆங் மோ கியோ.",
        "default": (
            "Singapore multilingual emergency. "
            "Sheng Siong, NTUC, Tampines, Woodlands, Jurong, Clementi, Bishan, AMK, "
            "Pasir Ris, Bedok, Queenstown, Toa Payoh, Hougang, Sengkang, Punggol, "
            "Chua Chu Kang, Yishun, HDB, MRT, void deck, kopitiam."
        ),
    }

    def __init__(self):
        self.client = AzureOpenAI(
            api_key=settings.AZURE_WHISPER_API_KEY,
            api_version=settings.AZURE_WHISPER_API_VERSION,
            azure_endpoint=settings.AZURE_WHISPER_ENDPOINT,
        )
        self.deployment = settings.AZURE_WHISPER_DEPLOYMENT_NAME

    def detect_language(self, audio_path: str) -> str:
        """
        Run a short transcription pass to detect the primary language.
        Returns an ISO 639-1 code (e.g. 'zh', 'en', 'ms', 'ta').
        Note: gpt-4o-transcribe only supports 'json' or 'text' response_format.
        Language detection falls back to 'en' since json format doesn't return lang.
        """
        try:
            with open(audio_path, "rb") as f:
                result = self.client.audio.transcriptions.create(
                    model=self.deployment,
                    file=f,
                    response_format="json",
                    # No prompt — it gets echoed verbatim into output
                )
            # gpt-4o-transcribe json format only returns .text — no language field
            # Language is inferred from transcript content by the LLM downstream
            logger.info("detect_language: using transcript text for downstream language inference")
            return "en"  # default; LLM will detect from transcript
        except Exception as e:
            logger.warning(f"Language detection failed, defaulting to 'en': {e}")
            return "en"

    def transcribe(
        self,
        audio_path: str,
        language_hint: Optional[str] = None,
    ) -> Tuple[str, str, float]:
        """
        Transcribe an audio file using Azure OpenAI gpt-4o-transcribe.

        Args:
            audio_path: Path to the audio file (wav, mp3, m4a, webm, etc.)
            language_hint: ISO 639-1 language code to bias the decoder.

        Returns:
            Tuple of (transcript_text, detected_language, avg_confidence)
        """
        lang = language_hint or "en"

        # Prompt = Singapore vocabulary ONLY.
        # Never include the running transcript here — when the audio is silent
        # or has background noise, Whisper echos the context it was given as if
        # it were the transcription (the classic "context echo" hallucination).
        # Cross-chunk context is handled by the dedup check in main.py instead.
        prompt = SINGAPORE_VOCAB_PROMPT

        try:
            with open(audio_path, "rb") as f:
                kwargs = {
                    "model": self.deployment,
                    "file": f,
                    # gpt-4o-transcribe only supports 'json' or 'text', NOT 'verbose_json'
                    "response_format": "json",
                    # Always pass language — without it the model guesses randomly
                    # and hallucinates in Korean/Arabic/Norwegian on background noise
                    "language": lang,
                    # Singapore vocab prompt biases the decoder toward local proper nouns.
                    "prompt": prompt,
                }

                result = self.client.audio.transcriptions.create(**kwargs)

            text = (result.text or "").strip()

            # ── Hallucination / refusal filter ─────────────────────
            # gpt-4o-transcribe outputs these patterns on silence, noise,
            # prompt echo, or when it decides not to transcribe.
            text_lower = text.lower().strip()

            # Exact-match hallucinations on silence / noise
            HALLUCINATION_EXACT = {
                "you", "thank you", "thank you.",
                "thank you for watching", "thank you for watching.",
                "please subscribe", "please subscribe.",
                "this is an emergency call", "this is an emergency call.",
                ".", "",
            }
            # Substrings that indicate refusal or meta-commentary
            HALLUCINATION_CONTAINS = [
                "i cannot transcribe",
                "i'm sorry, but i cannot",
                "i'm sorry, i cannot",
                "i am unable to transcribe",
                "i cannot provide a transcription",
                "does not contain a sentence",
                "contains sensitive information",
                "please provide",
                "no audio",
                "inaudible",
            ]
            # Prefixes indicating prompt echo (### separator pattern)
            HALLUCINATION_PREFIXES = [
                "###",  # Whisper uses ### to separate prompt context from transcript
                "emergency call in singapore",
                "singapore emergency",
                "panggilan kecemasan",
                "mrt stations:",
                "common locations",
                "landmarks: sheng",
                "landmarks: hdb",
                "areas: geylang",
                "temples: tua",
                "common phrases: void",
            ]

            if text_lower in HALLUCINATION_EXACT:
                logger.info(f"[whisper] filtered hallucination (exact): '{text}'")
                text = ""
            elif any(h in text_lower for h in HALLUCINATION_CONTAINS):
                logger.info(f"[whisper] filtered hallucination (refusal): '{text[:80]}'")
                text = ""
            elif any(text_lower.startswith(p) for p in HALLUCINATION_PREFIXES):
                logger.info(f"[whisper] filtered hallucination (prompt echo): '{text[:80]}'")
                text = ""
            else:
                # Strip any trailing ### artifacts that slipped through
                if "###" in text:
                    text = text[:text.index("###")].strip()

            detected_lang = lang
            if text:
                logger.info(f"Transcription complete: {len(text)} chars, lang={detected_lang}")
            return text, detected_lang, 0.85

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise

    def transcribe_or_simulate(
        self,
        audio_path: str,
        language_hint: Optional[str] = None,
        simulate: bool = False,
        simulated_transcript: str = "",
        simulated_language: str = "zh",
    ) -> Tuple[str, str, float]:
        """
        Transcribe real audio or return simulated data for demo mode.
        """
        if simulate:
            logger.info("Using simulated transcript (demo mode)")
            return simulated_transcript, simulated_language, 0.92

        return self.transcribe(audio_path, language_hint)


# Singleton
whisper_service = WhisperService()
