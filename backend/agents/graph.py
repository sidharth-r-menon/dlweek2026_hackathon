"""
LangGraph Agent Graph — stateful multi-node pipeline that orchestrates
the full call-processing workflow:

  Transcribe → Extract → (Locate ‖ Callback) → Output

Each node is a discrete processing step with defined input/output.
Location resolution and callback script generation run in parallel
once the incident card is produced.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict

from langgraph.graph import StateGraph, END
from models import (
    CallState,
    IncidentCard,
    LocationCandidate,
    CallbackScript,
)
from services.whisper_service import whisper_service
from services.crisis_llm import crisis_llm_service
from services.location_resolver import location_resolver
from services.callback_generator import callback_generator
from services.weather_service import weather_service

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Graph Nodes — each takes and returns a state dict
# ═══════════════════════════════════════════════════════════════

def transcribe_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node 1: Transcribe audio via Azure OpenAI Whisper.
    In demo mode, returns a simulated transcript.
    """
    logger.info("[Node] transcribe_node — start")

    audio_path = state.get("raw_audio_path", "")
    simulate = state.get("demo_mode", True)
    simulated_transcript = state.get("simulated_transcript", "")
    simulated_language = state.get("simulated_language", "zh")

    transcript, lang, confidence = whisper_service.transcribe_or_simulate(
        audio_path=audio_path,
        simulate=simulate,
        simulated_transcript=simulated_transcript,
        simulated_language=simulated_language,
    )

    logger.info(f"[Node] transcribe_node — done: lang={lang}, len={len(transcript)}")
    return {
        **state,
        "raw_transcript": transcript,
        "language_detected": lang,
        "transcription_confidence": confidence,
    }


def crisis_extraction_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node 2: Extract structured incident card from transcript
    using the Crisis LLM Engine (Azure OpenAI).
    """
    logger.info("[Node] crisis_extraction_node — start")

    transcript = state.get("raw_transcript", "")
    language = state.get("language_detected", "en")

    if state.get("demo_mode") and state.get("simulated_incident_card"):
        # Use pre-built demo card
        incident_card = state["simulated_incident_card"]
        logger.info("[Node] crisis_extraction_node — using simulated card")
    else:
        incident_card = crisis_llm_service.extract_incident_card(transcript, language)

    logger.info(f"[Node] crisis_extraction_node — done: type={incident_card.incident_type}")
    return {
        **state,
        "incident_card": incident_card.model_dump() if hasattr(incident_card, "model_dump") else incident_card,
    }


async def location_resolve_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node 3: Resolve location anchors to ranked address candidates.
    Runs in parallel with the callback script node.
    """
    logger.info("[Node] location_resolve_node — start")

    incident_data = state.get("incident_card", {})
    if isinstance(incident_data, dict):
        anchors_data = incident_data.get("location_anchors", [])
    else:
        anchors_data = incident_data.location_anchors if hasattr(incident_data, "location_anchors") else []

    from models import LocationAnchor
    anchors = []
    for a in anchors_data:
        if isinstance(a, dict):
            anchors.append(LocationAnchor(**a))
        else:
            anchors.append(a)

    if state.get("demo_mode") and state.get("simulated_locations"):
        candidates = state["simulated_locations"]
        logger.info("[Node] location_resolve_node — using simulated locations")
    elif anchors:
        candidates = await location_resolver.resolve(anchors)
    else:
        candidates = []

    # Generate confirmation question
    cand_objects = []
    for c in candidates:
        if isinstance(c, dict):
            cand_objects.append(LocationCandidate(**c))
        else:
            cand_objects.append(c)

    confirmation_q = location_resolver.generate_confirmation_question(cand_objects)

    # Fetch weather for top candidate
    weather_ctx = ""
    if cand_objects:
        top = cand_objects[0]
        weather_ctx = await weather_service.get_weather_context(top.latitude, top.longitude)

    serialized = [c.model_dump() if hasattr(c, "model_dump") else c for c in cand_objects]

    logger.info(f"[Node] location_resolve_node — done: {len(serialized)} candidates")
    return {
        **state,
        "location_candidates": serialized,
        "confirmation_question": confirmation_q,
        "weather_context": weather_ctx,
    }


def callback_script_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node 4: Generate phonetic callback script for the dispatcher.
    Runs in parallel with the location resolution node.
    """
    logger.info("[Node] callback_script_node — start")

    language = state.get("language_detected", "en")
    incident_data = state.get("incident_card", {})

    # Derive location context for P3 phrase
    loc_candidates = state.get("location_candidates", [])
    location_context = ""
    if loc_candidates:
        top = loc_candidates[0]
        location_context = top.get("name", "") if isinstance(top, dict) else top.name

    if isinstance(incident_data, dict):
        from models import IncidentCard
        try:
            incident_card = IncidentCard(**incident_data)
        except Exception:
            incident_card = None
    else:
        incident_card = incident_data

    if state.get("demo_mode"):
        script = callback_generator.get_demo_script(language)
    else:
        script = callback_generator.generate(language, incident_card, location_context)

    logger.info(f"[Node] callback_script_node — done: {len(script.phrases)} phrases")
    return {
        **state,
        "callback_script": script.model_dump(),
    }


def output_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Final node: Assemble the complete dispatcher output.
    """
    logger.info("[Node] output_node — assembling dispatcher output")

    if not state.get("call_id"):
        state["call_id"] = str(uuid.uuid4())[:8]

    return state


# ═══════════════════════════════════════════════════════════════
# Build the Graph
# ═══════════════════════════════════════════════════════════════

def build_agent_graph() -> StateGraph:
    """
    Construct the LangGraph state graph:

        transcribe → extract → locate (parallel with callback) → output
                              → callback ──────────────────────→ output
    """
    graph = StateGraph(dict)

    # Add nodes
    graph.add_node("transcribe", transcribe_node)
    graph.add_node("extract", crisis_extraction_node)
    graph.add_node("locate", location_resolve_node)
    graph.add_node("callback", callback_script_node)
    graph.add_node("output", output_node)

    # Define edges
    graph.set_entry_point("transcribe")
    graph.add_edge("transcribe", "extract")
    # Use conditional edges to branch to both 'locate' and 'callback' in parallel
    def extract_branch(state):
        # Always run both branches in parallel
        return ["locate", "callback"]
    graph.add_conditional_edges("extract", extract_branch)
    graph.add_edge("locate", "output")
    graph.add_edge("callback", "output")
    graph.add_edge("output", END)

    return graph.compile()


# Pre-built compiled graph
agent_graph = build_agent_graph()


async def run_pipeline(
    audio_path: str = "",
    demo_mode: bool = True,
    demo_scenario: str = "mandarin_medical",
) -> Dict[str, Any]:
    """
    Execute the full agent pipeline end-to-end.

    Args:
        audio_path: Path to audio file. Empty for demo mode.
        demo_mode: If True, uses simulated transcript and data.
        demo_scenario: Which demo scenario to load.

    Returns:
        Complete CallState dict with all fields populated.
    """
    from demo.scenarios import get_scenario

    initial_state = {
        "call_id": str(uuid.uuid4())[:8],
        "raw_audio_path": audio_path,
        "demo_mode": demo_mode,
        "raw_transcript": "",
        "language_detected": "",
        "incident_card": {},
        "location_candidates": [],
        "callback_script": {},
        "confirmation_question": "",
        "is_dispatched": False,
        "dispatcher_notes": "",
        "weather_context": "",
        "error": "",
    }

    # Load demo data if in demo mode
    if demo_mode:
        scenario = get_scenario(demo_scenario)
        initial_state.update(scenario)

    # Run the graph
    try:
        result = await agent_graph.ainvoke(initial_state)
        return result
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        initial_state["error"] = str(e)
        return initial_state
