"""
Configuration — loads environment variables and provides typed settings.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Azure OpenAI (LLM) ─────────────────────────────────
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    AZURE_OPENAI_DEPLOYMENT_NAME: str = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

    # ── Azure OpenAI Transcription (gpt-4o-transcribe) ───────
    AZURE_WHISPER_API_KEY: str = os.getenv("AZURE_WHISPER_API_KEY", "")
    AZURE_WHISPER_ENDPOINT: str = os.getenv("AZURE_WHISPER_ENDPOINT", "")
    AZURE_WHISPER_API_VERSION: str = os.getenv("AZURE_WHISPER_API_VERSION", "2025-01-01-preview")
    AZURE_WHISPER_DEPLOYMENT_NAME: str = os.getenv("AZURE_WHISPER_DEPLOYMENT_NAME", "gpt-4o-transcribe")

    # ── External APIs ──────────────────────────────────────
    DATAGOVSG_API_URL: str = os.getenv("DATAGOVSG_API_URL", "https://data.gov.sg/api/action/datastore_search")
    NOMINATIM_BASE_URL: str = os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
    OPENMETEO_API_URL: str = os.getenv("OPENMETEO_API_URL", "https://api.open-meteo.com/v1/forecast")

    # ── Server ─────────────────────────────────────────────
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"


settings = Settings()
