"""
FastAPI Main — Cross-Lingual Community Safety Radio backend server.

Provides REST API endpoints for the dispatcher dashboard, including:
- Processing emergency calls through the LangGraph pipeline
- Streaming pipeline updates via Server-Sent Events (SSE)
- Dispatcher actions (confirm location, dispatch, clarify)
- Demo scenario management
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

import websockets as ws_client
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from models import ProcessCallRequest, DispatcherAction, CallState
from agents.graph import run_pipeline
from demo.scenarios import list_scenarios, get_scenario
from services.callback_generator import callback_generator

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
    Proxies raw PCM16 audio (24 kHz, mono) from the dispatcher's browser
    to the Azure gpt-realtime WebSocket.

    Azure's native server-VAD detects speech boundaries; the
    conversation.item.input_audio_transcription.completed event carries
    the per-utterance transcript, which is forwarded to the frontend.
    No batch Whisper calls, no Silero VAD, no temp files.
    """
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    logger.info(f"[realtime] WS opened for session {session_id}")
    session_log(session_id, "REALTIME_WS_OPEN")

    # Build the Azure Realtime WebSocket URL
    endpoint = (
        settings.AZURE_WHISPER_ENDPOINT
        .replace("https://", "")
        .replace("http://", "")
        .rstrip("/")
    )
    azure_url = (
        f"wss://{endpoint}/openai/realtime"
        f"?deployment={settings.AZURE_WHISPER_DEPLOYMENT_NAME}"
        f"&api-version={settings.AZURE_REALTIME_API_VERSION}"
    )

    REALTIME_INSTRUCTIONS = (
        "You are a live transcription assistant for Singapore emergency calls (Police/Ambulance/Fire). "
        "Your ONLY task is to transcribe speech EXACTLY as spoken — word for word, with no paraphrasing, "
        "summarising, or adding commentary. "
        "CRITICAL: The caller may be panicking, crying, whispering, shouting, or hyperventilating. "
        "Reproduce even fragmented, incomplete, or repeated speech faithfully. "
        "MULTILINGUAL: The caller may speak Singapore English (Singlish), Mandarin (普通话/方言), "
        "Malay (Bahasa Melayu), or Tamil (தமிழ்), and WILL likely code-switch mid-sentence. "
        "Transcribe each language in its own script — do NOT transliterate Chinese to pinyin or Tamil to Roman. "
        "For Singapore Mandarin: use Simplified Chinese characters. "
        "For Singlish: preserve particles like 'lah', 'leh', 'meh', 'ah', 'lor', 'can', 'not'. "
        "NEVER generate a response, never answer questions, never add punctuation beyond what is natural. "
        "Output ONLY the transcription of what was spoken."
    )

    # Vocabulary prompt — biases the decoder toward Singapore-specific proper nouns.
    # Include terms in all 4 languages so the model recognises them regardless of which is spoken.
    TRANSCRIPTION_PROMPT = (
        # English / Singlish emergency terms
        "Singapore emergency call. "
        "Ambulance, police, fire, accident, collapsed, unconscious, bleeding, chest pain, cannot breathe. "
        "Locations: Tampines, Woodlands, Jurong East, Clementi, Bishan, Ang Mo Kio, Toa Payoh, "
        "Hougang, Sengkang, Punggol, Pasir Ris, Bedok, Queenstown, Buona Vista, Novena, "
        "Chua Chu Kang, Yishun, Sembawang, Admiralty, Dhoby Ghaut, Bugis, Orchard, "
        "Marina Bay, Raffles Place, Tanjong Pagar. "
        "Landmarks: HDB block, void deck, MRT station, bus interchange, kopitiam, hawker centre, "
        "community club, polyclinic, carpark, lift lobby, playground, RC (resident committee). "
        # Mandarin emergency terms (Simplified Chinese)
        "救护车, 警察, 消防, 出事, 晕倒, 昏迷, 流血, 胸痛, 不能呼吸, "
        "大巴窑, 义顺, 兀兰, 裕廊, 金文泰, 碧山, 宏茂桥, 淡滨尼, 勿洛, 后港, 盛港, 榜鹅, 蔡厝港, 三巴旺, "
        "组屋, 地铁站, 巴士站, 咖啡店, 小贩中心, 停车场, 走廊. "
        # Malay emergency terms
        "Ambulans, polis, kebakaran, kemalangan, pengsan, tidak sedar, berdarah, sakit dada, "
        "tidak boleh bernafas, tolong, cepat, kecemasan. "
        # Tamil emergency terms
        "ஆம்புலன்ஸ், போலீஸ், தீயணைப்பு, விபத்து, மயக்கம், நினைவிழந்தார், இரத்தம், "
        "மார்பு வலி, மூச்சு விட முடியவில்லை, உதவி, விரைவாக."
    )

    try:
        async with ws_client.connect(
            azure_url,
            additional_headers={"api-key": settings.AZURE_WHISPER_API_KEY},
            max_size=None,
        ) as azure_ws:
            logger.info(f"[realtime] Connected to Azure for session {session_id}")

            # Configure session: server VAD + transcription, no response generation
            await azure_ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": REALTIME_INSTRUCTIONS,
                    "input_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad",
                        # Lower threshold (0.3) catches distressed/quiet/panicking callers.
                        # 0.5 is too aggressive and misses speech from scared/whispering callers.
                        "threshold": 0.3,
                        "prefix_padding_ms": 500,   # capture word beginnings
                        "silence_duration_ms": 800,  # longer pause before committing — callers pause mid-sentence
                        "create_response": False,
                    },
                    "input_audio_transcription": {
                        "model": "gpt-4o-transcribe",
                        # Do NOT set "language" here — let the model auto-detect.
                        # Locking to a language kills multilingual/code-switching callers.
                        "prompt": TRANSCRIPTION_PROMPT,
                    },
                },
            }))

            # Task 1: browser → Azure (PCM16 audio forwarding)
            async def forward_audio() -> None:
                try:
                    while True:
                        raw = await websocket.receive()
                        if "bytes" in raw:
                            audio_b64 = base64.b64encode(raw["bytes"]).decode()
                            await azure_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": audio_b64,
                            }))
                        elif "text" in raw:
                            try:
                                msg = json.loads(raw["text"])
                                if msg.get("type") == "language_hint":
                                    lang = msg.get("language", "")
                                    # Store hint for callback generation only.
                                    # Do NOT lock the transcription language — gpt-4o-transcribe
                                    # auto-detects correctly and handles code-switching.
                                    # Forcing language: 'en' would destroy Mandarin/Tamil/Malay transcription.
                                    if lang:
                                        session["language_hint"] = lang
                                        session_log(session_id, "LANGUAGE_HINT", lang)
                            except Exception:
                                pass
                except (WebSocketDisconnect, Exception):
                    pass

            # Task 2: Azure → browser (transcript events)
            async def receive_events() -> None:
                try:
                    async for message in azure_ws:
                        event = json.loads(message)
                        etype = event.get("type", "")

                        if etype == "conversation.item.input_audio_transcription.completed":
                            text = event.get("transcript", "").strip()
                            if text:
                                session["transcript"] = (
                                    session.get("transcript", "") + " " + text
                                ).strip()
                                # Infer language from script if not yet detected
                                if not session.get("language_detected"):
                                    session["language_detected"] = (
                                        _infer_language(text)
                                        or session.get("language_hint", "en")
                                    )
                                session_log(session_id, "TRANSCRIPT", text)
                                try:
                                    await websocket.send_json({
                                        "type": "transcript_update",
                                        "text": text,
                                        "full_transcript": session["transcript"],
                                        "language": session.get("language_detected", "en"),
                                        "confidence": 1.0,
                                    })
                                except Exception:
                                    break

                        elif etype == "input_audio_buffer.speech_started":
                            logger.debug(f"[realtime] VAD: speech started")
                            try:
                                await websocket.send_json({"type": "speech_started"})
                            except Exception:
                                pass

                        elif etype == "input_audio_buffer.speech_stopped":
                            logger.debug(f"[realtime] VAD: speech stopped")
                            try:
                                await websocket.send_json({"type": "speech_stopped"})
                            except Exception:
                                pass

                        elif etype == "input_audio_buffer.committed":
                            logger.debug(f"[realtime] VAD: buffer committed")

                        elif etype == "error":
                            err = event.get("error", {})
                            logger.error(f"[realtime] Azure error: {err}")
                            session_log(session_id, "AZURE_ERROR", err)
                            try:
                                await websocket.send_json({"type": "error", "message": err.get("message", "Azure error")})
                            except Exception:
                                pass

                        elif etype in ("session.created", "session.updated"):
                            logger.info(f"[realtime] {etype}")
                            session_log(session_id, etype.upper())

                except Exception as e:
                    logger.error(f"[realtime] receive_events error: {e}")

            # Run both tasks; cancel the other when either finishes
            tasks = [
                asyncio.create_task(forward_audio()),
                asyncio.create_task(receive_events()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                try:
                    await t
                except Exception:
                    pass

    except WebSocketDisconnect:
        logger.info(f"[realtime] Frontend WS disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"[realtime] Connection error for session {session_id}: {e}")
        session_log(session_id, "REALTIME_CONNECT_ERROR", str(e))
    finally:
        session_log(
            session_id, "REALTIME_WS_CLOSED",
            f"final_transcript={repr(session.get('transcript', ''))[:200]}"
        )



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


def _infer_language(text: str) -> str:
    """
    Infer language from transcript text using Unicode block analysis.
    Used to auto-detect language from first transcribed utterance without
    locking the model to a fixed language up-front.
    """
    if not text:
        return ""
    # Tamil: U+0B80–U+0BFF
    if any('\u0B80' <= c <= '\u0BFF' for c in text):
        return 'ta'
    # CJK Unified Ideographs (Mandarin/Chinese)
    if any('\u4E00' <= c <= '\u9FFF' for c in text):
        return 'zh'
    # Malay uses Latin script — detect via common high-frequency Malay words
    malay_markers = {
        'saya', 'anda', 'dia', 'kami', 'kita', 'ada', 'tidak', 'tolong',
        'sakit', 'darah', 'mati', 'jatuh', 'hospital', 'ambulans', 'polis',
        'kebakaran', 'bantu', 'cepat', 'di', 'ke', 'dengan', 'dan', 'atau',
    }
    words = set(text.lower().split())
    if len(words & malay_markers) >= 2:
        return 'ms'
    return 'en'

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
