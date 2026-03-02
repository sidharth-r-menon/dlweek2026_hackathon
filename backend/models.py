"""
Pydantic models — shared data structures used across the entire pipeline.
"""

from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# ── Location ────────────────────────────────────────────────

class LocationAnchor(BaseModel):
    """A spatial reference extracted from the transcript."""
    text: str = Field(..., description="Raw text as spoken by the caller")
    type: Literal["poi", "street", "landmark", "district", "mrt", "block", "relative"] = "poi"
    language: str = Field("en", description="ISO 639-1 language code")


class LocationCandidate(BaseModel):
    """A resolved location with a confidence score."""
    name: str
    address: str
    latitude: float = 0.0
    longitude: float = 0.0
    score: float = Field(0.0, ge=0, le=100, description="Confidence 0-100")
    source: str = Field("osm", description="osm | datagovsg | hdb | onemap")


# ── Incident Card ───────────────────────────────────────────

class ConfidenceRatings(BaseModel):
    incident_type: Literal["HIGH", "MEDIUM", "LOW", "UNRESOLVED"] = "UNRESOLVED"
    victim_description: Literal["HIGH", "MEDIUM", "LOW", "UNRESOLVED"] = "UNRESOLVED"
    medical_urgency: Literal["HIGH", "MEDIUM", "LOW", "UNRESOLVED"] = "UNRESOLVED"
    location: Literal["HIGH", "MEDIUM", "LOW", "UNRESOLVED"] = "UNRESOLVED"
    threat_present: Literal["HIGH", "MEDIUM", "LOW", "UNRESOLVED"] = "UNRESOLVED"


class IncidentCard(BaseModel):
    """Structured crisis information extracted by the LLM."""
    incident_type: str = "UNKNOWN"
    victim_description: str = ""
    medical_urgency: Literal["critical", "urgent", "non-urgent", "unknown"] = "unknown"
    threat_present: Optional[bool] = None
    location_anchors: List[LocationAnchor] = Field(default_factory=list)
    caller_language: str = ""
    caller_emotional_state: Literal["calm", "distressed", "panic", "incoherent"] = "calm"
    confidence: ConfidenceRatings = Field(default_factory=ConfidenceRatings)
    missing_critical_info: List[str] = Field(default_factory=list)
    suggested_clarifying_questions: List[str] = Field(default_factory=list)


# ── Callback Script ─────────────────────────────────────────

class CallbackPhrase(BaseModel):
    """A single phonetic phrase the dispatcher can read aloud."""
    phrase_id: str = Field(..., description="P1-P4 identifier")
    purpose: str
    english: str
    native_script: str = ""
    phonetic: str = ""
    language: str = "en"


class CallbackScript(BaseModel):
    """Complete callback script for the detected language."""
    language: str = "en"
    phrases: List[CallbackPhrase] = Field(default_factory=list)


# ── Pipeline State ──────────────────────────────────────────

class CallState(BaseModel):
    """Full pipeline state passed through the LangGraph nodes."""
    call_id: str = ""
    raw_audio_path: str = ""
    language_detected: str = ""
    raw_transcript: str = ""
    incident_card: IncidentCard = Field(default_factory=IncidentCard)
    location_candidates: List[LocationCandidate] = Field(default_factory=list)
    callback_script: CallbackScript = Field(default_factory=CallbackScript)
    confirmation_question: str = ""
    is_dispatched: bool = False
    dispatcher_notes: str = ""
    weather_context: str = ""
    error: str = ""


# ── API Request / Response ──────────────────────────────────

class ProcessCallRequest(BaseModel):
    audio_path: str = Field("", description="Path to audio file or '' for demo mode")
    demo_mode: bool = Field(True, description="Use simulated call data")
    demo_scenario: str = Field("mandarin_medical", description="Which demo scenario to run")


class DispatcherAction(BaseModel):
    action: Literal["confirm_location", "dispatch", "clarify", "update_notes"]
    call_id: str
    location_index: Optional[int] = None
    dispatcher_response: Optional[str] = None
    notes: Optional[str] = None
