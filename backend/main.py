"""
FastAPI Main — Cross-Lingual Community Safety Radio backend server.

Provides REST API endpoints for the dispatcher dashboard, including:
- Processing emergency calls through the LangGraph pipeline
- Streaming pipeline updates via Server-Sent Events (SSE)
- Dispatcher actions (confirm location, dispatch, clarify)
- Demo scenario management
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from models import ProcessCallRequest, DispatcherAction, CallState
from agents.graph import run_pipeline
from demo.scenarios import list_scenarios, get_scenario
from services.callback_generator import callback_generator
from services.whisper_service import whisper_service

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Session log directory ──────────────────────────────────
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


def session_log(session_id: str, event: str, data: Any = None) -> None:
    """Append a timestamped line to the per-session log file."""
    log_path = os.path.join(LOGS_DIR, f"session_{session_id}.log")
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    line = f"[{ts}] {event}"
    if data is not None:
        if isinstance(data, (dict, list)):
            line += "\n" + json.dumps(data, ensure_ascii=False, indent=2)
        else:
            line += f" | {data}"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── App ─────────────────────────────────────────────────────
app = FastAPI(
    title="Cross-Lingual Community Safety Radio",
    description="LLM-Powered Multilingual Emergency Dispatch System",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory call store (hackathon scope) ──────────────────
active_calls: Dict[str, Dict[str, Any]] = {}

# ── Live call sessions (signaling + transcription) ──────────
# Each session: { dispatcher_ws, caller_ws, transcript, call_id }
live_sessions: Dict[str, Dict[str, Any]] = {}


# ═══════════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    return {
        "service": "Cross-Lingual Community Safety Radio",
        "version": "1.0.0",
        "status": "operational",
    }


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}


# ── Scenarios ──────────────────────────────────────────────

@app.get("/api/scenarios")
async def get_scenarios():
    """List all available demo scenarios."""
    return {"scenarios": list_scenarios()}


# ── Process Call ───────────────────────────────────────────

@app.post("/api/call/process")
async def process_call(request: ProcessCallRequest):
    """
    Process an emergency call through the full LangGraph pipeline.
    In demo mode, uses simulated data. Otherwise, requires an audio file path.
    """
    try:
        result = await run_pipeline(
            audio_path=request.audio_path,
            demo_mode=request.demo_mode,
            demo_scenario=request.demo_scenario,
        )

        call_id = result.get("call_id", str(uuid.uuid4())[:8])
        active_calls[call_id] = result

        return {
            "call_id": call_id,
            "status": "processed",
            "data": _serialize_state(result),
        }

    except Exception as e:
        logger.error(f"Call processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Streaming Pipeline (SSE) ──────────────────────────────

@app.get("/api/call/stream/{scenario}")
async def stream_call(scenario: str = "mandarin_medical"):
    """
    Stream the call processing pipeline as Server-Sent Events.
    Simulates real-time progressive updates for the dispatcher dashboard.
    """

    async def event_generator():
        # Load scenario
        scenario_data = get_scenario(scenario)
        call_id = str(uuid.uuid4())[:8]

        # Phase 1: Call connected
        yield _sse_event("call_connected", {
            "call_id": call_id,
            "timestamp": "T+0.0s",
            "message": "Emergency call connected. Audio stream opened.",
        })
        await asyncio.sleep(0.5)

        # Phase 2: Language detected
        lang = scenario_data.get("simulated_language", "zh")
        yield _sse_event("language_detected", {
            "call_id": call_id,
            "timestamp": "T+0.5s",
            "language": lang,
            "message": f"Language detected: {lang}",
        })
        await asyncio.sleep(1.0)

        # Phase 3: Transcript streaming (word by word simulation)
        transcript = scenario_data.get("simulated_transcript", "")
        words = transcript.split()
        accumulated = ""
        for i, word in enumerate(words):
            accumulated += ("" if i == 0 else " ") + word
            yield _sse_event("transcript_update", {
                "call_id": call_id,
                "timestamp": f"T+{1.0 + i * 0.15:.1f}s",
                "partial_transcript": accumulated,
                "is_final": i == len(words) - 1,
            })
            await asyncio.sleep(0.15)

        # Phase 4: Incident card extracted
        incident_card = scenario_data.get("simulated_incident_card", {})
        yield _sse_event("incident_card", {
            "call_id": call_id,
            "timestamp": f"T+{1.0 + len(words) * 0.15 + 1.0:.1f}s",
            "incident_card": incident_card,
        })
        await asyncio.sleep(1.5)

        # Phase 5: Location candidates
        locations = scenario_data.get("simulated_locations", [])
        yield _sse_event("location_candidates", {
            "call_id": call_id,
            "timestamp": f"T+{1.0 + len(words) * 0.15 + 2.5:.1f}s",
            "candidates": locations,
            "confirmation_question": _generate_confirm_q(locations),
        })
        await asyncio.sleep(0.5)

        # Phase 6: Callback script
        from services.callback_generator import callback_generator
        script = callback_generator.get_demo_script(lang)
        yield _sse_event("callback_script", {
            "call_id": call_id,
            "timestamp": f"T+{1.0 + len(words) * 0.15 + 3.0:.1f}s",
            "callback_script": script.model_dump(),
        })
        await asyncio.sleep(0.5)

        # Phase 7: Pipeline complete
        yield _sse_event("pipeline_complete", {
            "call_id": call_id,
            "timestamp": f"T+{1.0 + len(words) * 0.15 + 3.5:.1f}s",
            "message": "All analysis complete. Ready for dispatcher action.",
        })

        # Store in active calls
        active_calls[call_id] = {
            "call_id": call_id,
            "language_detected": lang,
            "raw_transcript": transcript,
            "incident_card": incident_card,
            "location_candidates": locations,
            "callback_script": script.model_dump(),
            "is_dispatched": False,
        }

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Live Demo Stream (real Azure OpenAI APIs) ──────────────

@app.get("/api/call/stream/live/{scenario}")
async def stream_live_call(scenario: str = "mandarin_medical"):
    """
    Stream a live demo call using REAL Azure OpenAI (gpt-4o-transcribe + GPT-4o)
    and real location resolver APIs. Uses the scenario's known transcript so we
    don't need a real audio file, but every AI step calls the live API.
    """
    from services.crisis_llm import crisis_llm_service as _llm
    from services.location_resolver import location_resolver as _lr
    from services.callback_generator import callback_generator as _cb
    from models import LocationAnchor, IncidentCard

    async def event_generator():
        scenario_data = get_scenario(scenario)
        call_id = str(uuid.uuid4())[:8]

        # Phase 1 ── Call connected
        yield _sse_event("call_connected", {
            "call_id": call_id,
            "timestamp": "T+0.0s",
            "message": "Live call connected. Real-time AI processing active.",
        })
        await asyncio.sleep(0.5)

        # Phase 2 ── Language detected
        lang = scenario_data.get("simulated_language", "zh")
        yield _sse_event("language_detected", {
            "call_id": call_id,
            "timestamp": "T+0.5s",
            "language": lang,
            "message": f"Language detected: {lang}",
        })
        await asyncio.sleep(0.8)

        # Phase 3 ── Stream transcript word-by-word
        transcript = scenario_data.get("simulated_transcript", "")
        words = transcript.split()
        accumulated = ""
        for i, word in enumerate(words):
            accumulated += ("" if i == 0 else " ") + word
            yield _sse_event("transcript_update", {
                "call_id": call_id,
                "timestamp": f"T+{1.3 + i * 0.15:.1f}s",
                "partial_transcript": accumulated,
                "is_final": i == len(words) - 1,
            })
            await asyncio.sleep(0.15)

        t_base = 1.3 + len(words) * 0.15

        # Phase 4 ── REAL LLM incident extraction
        yield _sse_event("processing", {
            "call_id": call_id,
            "timestamp": f"T+{t_base:.1f}s",
            "message": "GPT-4o analysing transcript…",
        })
        try:
            incident_card = await asyncio.to_thread(
                _llm.extract_incident_card, transcript, lang
            )
            incident_dict = incident_card.model_dump() if hasattr(incident_card, "model_dump") else incident_card
        except Exception as e:
            logger.error(f"[live] LLM extraction failed: {e}")
            fallback = scenario_data.get("simulated_incident_card", {})
            incident_dict = fallback.model_dump() if hasattr(fallback, "model_dump") else fallback

        yield _sse_event("incident_card", {
            "call_id": call_id,
            "timestamp": f"T+{t_base + 2.0:.1f}s",
            "incident_card": incident_dict,
        })
        await asyncio.sleep(0.3)

        # Phase 5 ── REAL location resolver
        yield _sse_event("processing", {
            "call_id": call_id,
            "timestamp": f"T+{t_base + 2.3:.1f}s",
            "message": "Resolving location via OSM & Data.gov.sg…",
        })
        try:
            anchors_data = incident_dict.get("location_anchors", []) if isinstance(incident_dict, dict) else []
            anchors = [LocationAnchor(**a) if isinstance(a, dict) else a for a in anchors_data]
            candidates = await _lr.resolve(anchors) if anchors else []
            cand_dicts = [c.model_dump() if hasattr(c, "model_dump") else c for c in candidates]
            confirm_q = _lr.generate_confirmation_question(candidates)
        except Exception as e:
            logger.error(f"[live] Location resolve failed: {e}")
            raw = scenario_data.get("simulated_locations", [])
            cand_dicts = [l.model_dump() if hasattr(l, "model_dump") else l for l in raw]
            confirm_q = _generate_confirm_q(cand_dicts)

        yield _sse_event("location_candidates", {
            "call_id": call_id,
            "timestamp": f"T+{t_base + 4.0:.1f}s",
            "candidates": cand_dicts,
            "confirmation_question": confirm_q,
        })
        await asyncio.sleep(0.3)

        # Phase 6 ── REAL callback generator
        try:
            loc_name = cand_dicts[0].get("name", "") if cand_dicts else ""
            try:
                card_obj = IncidentCard(**incident_dict) if isinstance(incident_dict, dict) else incident_dict
            except Exception:
                card_obj = None
            script = await asyncio.to_thread(_cb.generate, lang, card_obj, loc_name)
        except Exception as e:
            logger.error(f"[live] Callback generation failed: {e}")
            script = _cb.get_demo_script(lang)

        yield _sse_event("callback_script", {
            "call_id": call_id,
            "timestamp": f"T+{t_base + 5.5:.1f}s",
            "callback_script": script.model_dump(),
        })

        # Phase 7 ── Complete
        yield _sse_event("pipeline_complete", {
            "call_id": call_id,
            "timestamp": f"T+{t_base + 6.0:.1f}s",
            "message": "Live AI analysis complete. Ready for dispatcher action.",
        })

        active_calls[call_id] = {
            "call_id": call_id,
            "language_detected": lang,
            "raw_transcript": transcript,
            "incident_card": incident_dict,
            "location_candidates": cand_dicts,
            "callback_script": script.model_dump(),
            "is_dispatched": False,
        }

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════
# Real-Time Live Call: Session + WebRTC Signaling + Transcription
# ══════════════════════════════════════════════════════════════

@app.post("/api/session/create")
async def create_live_session():
    """
    Create a new live call session. Returns a session_id that the dispatcher
    shares with the caller. The caller opens a link containing this ID.
    """
    session_id = str(uuid.uuid4())[:8].upper()
    live_sessions[session_id] = {
        "dispatcher_ws": None,
        "caller_ws": None,
        "transcript": "",
        "call_id": str(uuid.uuid4())[:8],
        "language_detected": "",
        "incident_card": None,
        "location_candidates": [],
        "callback_script": None,
        "is_dispatched": False,
    }
    logger.info(f"[session] Created live session: {session_id}")
    session_log(session_id, "SESSION_CREATED", {"session_id": session_id, "call_id": live_sessions[session_id]["call_id"]})
    return {"session_id": session_id, "call_id": live_sessions[session_id]["call_id"]}


@app.websocket("/ws/signal/{session_id}/{role}")
async def signal_websocket(websocket: WebSocket, session_id: str, role: str):
    """
    WebRTC signaling channel. role = 'dispatcher' | 'caller'.
    Each peer connects here; messages are forwarded to the other peer.
    Used to exchange SDP offer/answer and ICE candidates.
    """
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    session[f"{role}_ws"] = websocket
    other_role = "caller" if role == "dispatcher" else "dispatcher"

    logger.info(f"[signal] {role} joined session {session_id}")

    # Notify the other peer that this role connected
    other_ws: Optional[WebSocket] = session.get(f"{other_role}_ws")
    if other_ws:
        try:
            await other_ws.send_json({"type": "peer_joined", "role": role})
        except Exception:
            pass

    try:
        while True:
            data = await websocket.receive_json()
            # Forward all signaling messages (offer, answer, ice-candidate) to the other peer
            other_ws = session.get(f"{other_role}_ws")
            if other_ws:
                try:
                    await other_ws.send_json(data)
                except Exception:
                    pass
    except WebSocketDisconnect:
        session[f"{role}_ws"] = None
        logger.info(f"[signal] {role} disconnected from session {session_id}")
        # Notify other peer
        other_ws = session.get(f"{other_role}_ws")
        if other_ws:
            try:
                await other_ws.send_json({"type": "peer_left", "role": role})
            except Exception:
                pass


@app.websocket("/ws/transcribe/{session_id}")
async def transcribe_websocket(websocket: WebSocket, session_id: str):
    """
    Receives raw audio chunks (webm/opus) from the dispatcher's browser
    (captured from the caller's WebRTC remote track), transcribes each chunk
    via gpt-4o-transcribe, and streams transcript updates back.
    Also forwards transcript updates to the dispatcher's signaling WS.
    """
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    logger.info(f"[transcribe] WS opened for session {session_id}")
    session_log(session_id, "TRANSCRIBE_WS_OPEN")

    try:
        chunk_index = 0
        while True:
            raw = await websocket.receive()

            if "bytes" in raw:
                audio_bytes = raw["bytes"]
            elif "text" in raw:
                # Control message (e.g. language hint)
                try:
                    msg = json.loads(raw["text"])
                    if msg.get("type") == "language_hint":
                        session["language_hint"] = msg.get("language", "")
                        session_log(session_id, "LANGUAGE_HINT", msg.get("language", ""))
                except Exception:
                    pass
                continue
            else:
                continue

            chunk_index += 1
            chunk_label = f"chunk_{chunk_index:04d}"

            # Skip chunks that are too small — likely empty/corrupt (< 1.5KB)
            if not audio_bytes or len(audio_bytes) < 1500:
                logger.debug(f"[transcribe] skipping tiny chunk ({len(audio_bytes)} bytes)")
                session_log(session_id, f"{chunk_label} SKIPPED (too small)", f"{len(audio_bytes)} bytes")
                continue

            # Write to a temp file for the transcription API
            # Detect format from magic bytes: WAV starts with RIFF, WebM with \x1a\x45
            suffix = ".wav" if audio_bytes[:4] == b'RIFF' else ".webm"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name

            try:
                lang_hint = session.get("language_hint") or "en"
                transcript_so_far = session.get("transcript", "")
                session_log(session_id, f"{chunk_label} SENDING TO WHISPER", f"{len(audio_bytes)} bytes | lang_hint={lang_hint}")
                text, lang, conf = await asyncio.to_thread(
                    whisper_service.transcribe, tmp_path, lang_hint
                )
                session_log(session_id, f"{chunk_label} WHISPER RESULT", f"text={repr(text)} | lang={lang} | conf={conf:.2f}")

                # ── Deduplication ─────────────────────────────────────────────
                # Two cases to catch:
                # 1. Full-transcript echo: result is suspiciously long (> ~120 chars
                #    for a 3s chunk at ~150wpm) and already exists in the transcript.
                # 2. Tail overlap: result exactly matches the last N chars of the
                #    transcript (chunk was silent, Whisper re-generated last words).
                if text.strip() and transcript_so_far:
                    t = text.strip()
                    tail = transcript_so_far[-max(len(t) + 20, 80):].strip()
                    tail_lower = tail.lower()
                    t_lower = t.lower()
                    # Skip if the result appears at the very end of the transcript
                    # (tail-match: new text IS what was already transcribed last)
                    is_tail_echo = tail_lower.endswith(t_lower) or t_lower in tail_lower[-len(t_lower) - 10:]
                    # Skip if result is very long AND already fully contained
                    # (full-transcript echo during silence)
                    is_full_echo = len(t) > 80 and t_lower in transcript_so_far.lower()
                    if is_tail_echo or is_full_echo:
                        session_log(session_id, f"{chunk_label} DEDUP_SKIP",
                                    f"tail_echo={is_tail_echo} full_echo={is_full_echo} | '{t[:60]}...'")
                        text = ""

                if text.strip():
                    session["transcript"] = (session.get("transcript", "") + " " + text).strip()
                    if lang and not session.get("language_detected"):
                        session["language_detected"] = lang

                    payload = {
                        "type": "transcript_update",
                        "text": text.strip(),
                        "full_transcript": session["transcript"],
                        "language": lang,
                        "confidence": round(conf, 2),
                    }
                    session_log(session_id, f"{chunk_label} TRANSCRIPT_UPDATE", f"text={repr(text.strip())} | full_len={len(session['transcript'])}")
                    await websocket.send_json(payload)

            except Exception as e:
                logger.error(f"[transcribe] chunk error: {e}")
                session_log(session_id, f"{chunk_label} ERROR", str(e))
                await websocket.send_json({"type": "error", "message": str(e)})
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    except WebSocketDisconnect:
        logger.info(f"[transcribe] WS closed for session {session_id}")
        session_log(session_id, "TRANSCRIBE_WS_CLOSED", f"final_transcript={repr(session.get('transcript', ''))[:200]}")


@app.websocket("/ws/analyze/{session_id}")
async def analyze_websocket(websocket: WebSocket, session_id: str):
    """
    Dispatcher triggers AI analysis of the accumulated transcript.
    Runs incident extraction → location resolution → callback generation
    and streams results back in real-time.
    """
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    from services.crisis_llm import crisis_llm_service as _llm
    from services.location_resolver import location_resolver as _lr
    from services.callback_generator import callback_generator as _cb
    from models import LocationAnchor, IncidentCard

    try:
        while True:
            msg = await websocket.receive_json()

            if msg.get("type") != "analyze":
                continue

            transcript = session.get("transcript", "").strip()
            lang = session.get("language_detected", "en")

            if not transcript:
                await websocket.send_json({"type": "error", "message": "No transcript yet"})
                continue

            session_log(session_id, "ANALYZE_TRIGGERED", {"transcript_len": len(transcript), "lang": lang, "transcript": transcript})

            # — Incident extraction —
            await websocket.send_json({"type": "processing", "message": "GPT-4o extracting incident details…"})
            try:
                incident_card = await asyncio.to_thread(_llm.extract_incident_card, transcript, lang)
                incident_dict = incident_card.model_dump() if hasattr(incident_card, "model_dump") else incident_card
                session["incident_card"] = incident_dict
                session_log(session_id, "INCIDENT_CARD", incident_dict)
            except Exception as e:
                logger.error(f"[analyze] LLM failed: {e}")
                session_log(session_id, "INCIDENT_CARD_ERROR", str(e))
                incident_dict = {}

            await websocket.send_json({"type": "incident_card", "incident_card": incident_dict})

            # — Location resolution —
            await websocket.send_json({"type": "processing", "message": "Resolving location via OSM & Data.gov.sg…"})
            try:
                anchors_data = incident_dict.get("location_anchors", []) if isinstance(incident_dict, dict) else []
                anchors = [LocationAnchor(**a) if isinstance(a, dict) else a for a in anchors_data]
                candidates = await _lr.resolve(anchors) if anchors else []
                cand_dicts = [c.model_dump() if hasattr(c, "model_dump") else c for c in candidates]
                confirm_q = _lr.generate_confirmation_question(candidates)
                session["location_candidates"] = cand_dicts
                session_log(session_id, "LOCATION_CANDIDATES", cand_dicts)
            except Exception as e:
                logger.error(f"[analyze] location failed: {e}")
                session_log(session_id, "LOCATION_ERROR", str(e))
                cand_dicts, confirm_q = [], ""

            await websocket.send_json({
                "type": "location_candidates",
                "candidates": cand_dicts,
                "confirmation_question": confirm_q,
            })

            # — Callback script —
            await websocket.send_json({"type": "processing", "message": "Generating dispatcher callback phrases…"})
            try:
                loc_name = cand_dicts[0].get("name", "") if cand_dicts else ""
                try:
                    card_obj = IncidentCard(**incident_dict) if isinstance(incident_dict, dict) else incident_dict
                except Exception:
                    card_obj = None
                script = await asyncio.to_thread(_cb.generate, lang, card_obj, loc_name)
                session["callback_script"] = script.model_dump()
            except Exception as e:
                logger.error(f"[analyze] callback failed: {e}")
                script = _cb.get_demo_script(lang)
                session["callback_script"] = script.model_dump()

            await websocket.send_json({
                "type": "callback_script",
                "callback_script": session["callback_script"],
            })
            await websocket.send_json({"type": "analysis_complete"})

            # Store in active_calls for dispatcher action endpoints
            active_calls[session["call_id"]] = {**session, "is_dispatched": False}

    except WebSocketDisconnect:
        logger.info(f"[analyze] WS closed for session {session_id}")


# ── Dispatcher Actions ─────────────────────────────────────

@app.post("/api/call/action")
async def dispatcher_action(action: DispatcherAction):
    """Handle dispatcher actions: confirm location, dispatch, clarify."""
    call = active_calls.get(action.call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    if action.action == "confirm_location":
        idx = action.location_index or 0
        candidates = call.get("location_candidates", [])
        if idx < len(candidates):
            confirmed = candidates[idx]
            call["confirmed_location"] = confirmed
            return {"status": "location_confirmed", "location": confirmed}
        raise HTTPException(status_code=400, detail="Invalid location index")

    elif action.action == "dispatch":
        call["is_dispatched"] = True
        return {
            "status": "dispatched",
            "call_id": action.call_id,
            "message": "Emergency services dispatched.",
            "incident": call.get("incident_card", {}),
            "location": call.get("confirmed_location", call.get("location_candidates", [{}])[0] if call.get("location_candidates") else {}),
        }

    elif action.action == "clarify":
        call["dispatcher_notes"] = action.dispatcher_response or ""
        return {
            "status": "clarification_noted",
            "message": "Dispatcher input recorded. Re-analysis would occur in production.",
        }

    elif action.action == "update_notes":
        call["dispatcher_notes"] = action.notes or ""
        return {"status": "notes_updated"}

    raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")


@app.get("/api/call/{call_id}")
async def get_call(call_id: str):
    """Get the current state of an active call."""
    call = active_calls.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"call_id": call_id, "data": _serialize_state(call)}


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    serialized = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event_type}\ndata: {serialized}\n\n"


def _serialize_state(state: dict) -> dict:
    """Ensure the state dict is JSON-serialisable."""
    result = {}
    for key, value in state.items():
        if hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        elif isinstance(value, list):
            result[key] = [
                v.model_dump() if hasattr(v, "model_dump") else v for v in value
            ]
        else:
            result[key] = value
    return result


def _generate_confirm_q(locations) -> str:
    """Generate a confirmation question from location candidates."""
    if not locations:
        return "Could you ask the caller for more location details?"
    top = locations[0]
    name = top.get("name") if isinstance(top, dict) else top.name
    score = top.get("score", 0) if isinstance(top, dict) else top.score
    return f"Are you near {name}? Just say yes or no. ({int(score)}% confidence)"


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
