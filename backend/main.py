"""
FastAPI Main — Cross-Lingual Community Safety Radio backend server.

Key fixes in this version:
  1. VAD threshold raised to 0.8 — prevents WebRTC comfort noise from
     triggering Azure's server-VAD and causing silence hallucinations.
  2. Strict session.created → session.update → session.updated handshake
     before accepting any audio — eliminates the restart race condition.
  3. All unhandled Azure events logged to session file for diagnostics.
  4. input_audio_transcription.failed events surfaced to frontend.
  5. Graceful cleanup: pending asyncio tasks cancelled on WS disconnect.
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import websockets as ws_client
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from models import ProcessCallRequest, DispatcherAction, CallState, LocationAnchor, IncidentCard
from agents.graph import run_pipeline
from demo.scenarios import list_scenarios, get_scenario
from services.callback_generator import callback_generator

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Session log directory ─────────────────────────────────────────────────────
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


# ── App ───────────────────────────────────────────────────────────────────────
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

# ── In-memory stores (hackathon scope) ───────────────────────────────────────
active_calls: Dict[str, Dict[str, Any]] = {}
live_sessions: Dict[str, Dict[str, Any]] = {}


# ═════════════════════════════════════════════════════════════════════════════
# Basic endpoints
# ═════════════════════════════════════════════════════════════════════════════

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


@app.get("/api/scenarios")
async def get_scenarios():
    return {"scenarios": list_scenarios()}


# ── Process Call ──────────────────────────────────────────────────────────────

@app.post("/api/call/process")
async def process_call(request: ProcessCallRequest):
    try:
        result = await run_pipeline(
            audio_path=request.audio_path,
            demo_mode=request.demo_mode,
            demo_scenario=request.demo_scenario,
        )
        call_id = result.get("call_id", str(uuid.uuid4())[:8])
        active_calls[call_id] = result
        return {"call_id": call_id, "status": "processed", "data": _serialize_state(result)}
    except Exception as e:
        logger.error(f"Call processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Demo SSE stream (simulated) ───────────────────────────────────────────────

@app.get("/api/call/stream/{scenario}")
async def stream_call(scenario: str = "mandarin_medical"):
    async def event_generator():
        scenario_data = get_scenario(scenario)
        call_id = str(uuid.uuid4())[:8]

        yield _sse_event("call_connected", {"call_id": call_id, "timestamp": "T+0.0s"})
        await asyncio.sleep(0.5)

        lang = scenario_data.get("simulated_language", "zh")
        yield _sse_event("language_detected", {"call_id": call_id, "language": lang})
        await asyncio.sleep(1.0)

        transcript = scenario_data.get("simulated_transcript", "")
        words = transcript.split()
        accumulated = ""
        for i, word in enumerate(words):
            accumulated += ("" if i == 0 else " ") + word
            yield _sse_event("transcript_update", {
                "call_id": call_id,
                "partial_transcript": accumulated,
                "is_final": i == len(words) - 1,
            })
            await asyncio.sleep(0.15)

        incident_card = scenario_data.get("simulated_incident_card", {})
        yield _sse_event("incident_card", {"call_id": call_id, "incident_card": incident_card})
        await asyncio.sleep(1.5)

        locations = scenario_data.get("simulated_locations", [])
        yield _sse_event("location_candidates", {
            "call_id": call_id,
            "candidates": locations,
            "confirmation_question": _generate_confirm_q(locations),
        })
        await asyncio.sleep(0.5)

        script = callback_generator.get_demo_script(lang)
        yield _sse_event("callback_script", {
            "call_id": call_id,
            "callback_script": script.model_dump(),
        })
        await asyncio.sleep(0.5)

        yield _sse_event("pipeline_complete", {"call_id": call_id})

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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── Live demo SSE (real APIs) ─────────────────────────────────────────────────

@app.get("/api/call/stream/live/{scenario}")
async def stream_live_call(scenario: str = "mandarin_medical"):
    from services.crisis_llm import crisis_llm_service as _llm
    from services.location_resolver import location_resolver as _lr
    from services.callback_generator import callback_generator as _cb

    async def event_generator():
        scenario_data = get_scenario(scenario)
        call_id = str(uuid.uuid4())[:8]

        yield _sse_event("call_connected", {"call_id": call_id, "timestamp": "T+0.0s"})
        await asyncio.sleep(0.5)

        lang = scenario_data.get("simulated_language", "zh")
        yield _sse_event("language_detected", {"call_id": call_id, "language": lang})
        await asyncio.sleep(0.8)

        transcript = scenario_data.get("simulated_transcript", "")
        words = transcript.split()
        accumulated = ""
        for i, word in enumerate(words):
            accumulated += ("" if i == 0 else " ") + word
            yield _sse_event("transcript_update", {
                "call_id": call_id,
                "partial_transcript": accumulated,
                "is_final": i == len(words) - 1,
            })
            await asyncio.sleep(0.15)

        t_base = 1.3 + len(words) * 0.15

        yield _sse_event("processing", {"call_id": call_id, "message": "GPT-4o analysing transcript…"})
        try:
            incident_card = await asyncio.to_thread(_llm.extract_incident_card, transcript, lang)
            incident_dict = incident_card.model_dump() if hasattr(incident_card, "model_dump") else incident_card
        except Exception as e:
            logger.error(f"[live] LLM extraction failed: {e}")
            fallback = scenario_data.get("simulated_incident_card", {})
            incident_dict = fallback.model_dump() if hasattr(fallback, "model_dump") else fallback

        yield _sse_event("incident_card", {"call_id": call_id, "incident_card": incident_dict})
        await asyncio.sleep(0.3)

        yield _sse_event("processing", {"call_id": call_id, "message": "Resolving location via OSM & Data.gov.sg…"})
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
            "candidates": cand_dicts,
            "confirmation_question": confirm_q,
        })
        await asyncio.sleep(0.3)

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
            "callback_script": script.model_dump(),
        })
        yield _sse_event("pipeline_complete", {"call_id": call_id})

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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ═════════════════════════════════════════════════════════════════════════════
# Live session management
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/api/session/create")
async def create_live_session():
    session_id = str(uuid.uuid4())[:8].upper()
    live_sessions[session_id] = {
        "dispatcher_ws": None,
        "caller_ws": None,
        "transcript": "",
        "call_id": str(uuid.uuid4())[:8],
        "language_detected": "",
        "language_hint": "en",
        "incident_card": None,
        "location_candidates": [],
        "callback_script": None,
        "is_dispatched": False,
    }
    logger.info(f"[session] Created: {session_id}")
    session_log(session_id, "SESSION_CREATED", {
        "session_id": session_id,
        "call_id": live_sessions[session_id]["call_id"],
    })
    return {"session_id": session_id, "call_id": live_sessions[session_id]["call_id"]}


# ── WebRTC signaling ──────────────────────────────────────────────────────────

@app.websocket("/ws/signal/{session_id}/{role}")
async def signal_websocket(websocket: WebSocket, session_id: str, role: str):
    """WebRTC signaling channel. role = 'dispatcher' | 'caller'."""
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    session[f"{role}_ws"] = websocket
    other_role = "caller" if role == "dispatcher" else "dispatcher"

    logger.info(f"[signal] {role} joined session {session_id}")

    other_ws: Optional[WebSocket] = session.get(f"{other_role}_ws")
    if other_ws:
        try:
            await other_ws.send_json({"type": "peer_joined", "role": role})
        except Exception:
            pass

    try:
        while True:
            data = await websocket.receive_json()
            other_ws = session.get(f"{other_role}_ws")
            if other_ws:
                try:
                    await other_ws.send_json(data)
                except Exception:
                    pass
    except WebSocketDisconnect:
        session[f"{role}_ws"] = None
        logger.info(f"[signal] {role} disconnected from {session_id}")
        other_ws = session.get(f"{other_role}_ws")
        if other_ws:
            try:
                await other_ws.send_json({"type": "peer_left", "role": role})
            except Exception:
                pass


# ── Transcription proxy WebSocket ─────────────────────────────────────────────

# Azure Realtime VAD config
# ─────────────────────────────────────────────────────────────────────────────
# threshold: 0.8 (NOT 0.5)
#   WebRTC injects "comfort noise" — a low-level synthetic signal that fills
#   silence so the call doesn't feel dead. At 0.5, Azure's VAD detects this
#   comfort noise as speech and hallucinates transcription. At 0.8, only
#   genuine human speech crosses the threshold.
#
# silence_duration_ms: 600
#   Slightly longer than the default 500ms — gives callers a natural pause
#   before the utterance is committed. Reduces false splits mid-sentence.
#
# prefix_padding_ms: 200
#   Includes 200ms of audio before speech onset — captures the first consonant
#   of an utterance, which is often clipped at lower values.
# ─────────────────────────────────────────────────────────────────────────────
AZURE_VAD_CONFIG = {
    "type": "server_vad",
    "threshold": 0.8,
    "prefix_padding_ms": 200,
    "silence_duration_ms": 600,
    "create_response": False,  # transcription only — no GPT-4o response generation
}

REALTIME_INSTRUCTIONS = (
    "You are a live transcription assistant for Singapore emergency calls. "
    "Transcribe the caller's speech accurately, word for word. "
    "The caller may speak English, Mandarin (Singapore accent), Hokkien, Cantonese, "
    "Malay, or Tamil, and may code-switch between languages mid-sentence. "
    "Do NOT generate any responses, suggestions, or commentary — only transcribe. "
    "Common Singapore locations: Tampines, Woodlands, Jurong, Bishan, Ang Mo Kio, "
    "Clementi, Bedok, Toa Payoh, Yishun, Sengkang, Punggol, Chua Chu Kang, "
    "Sembawang, Novena, Dhoby Ghaut, Bugis, Orchard, Marina Bay, Geylang, Serangoon. "
    "Common Singapore terms: HDB, void deck, MRT, LRT, kopitiam, hawker centre, "
    "polyclinic, SAF, SCDF, SPF."
)

REALTIME_TRANSCRIPTION_PROMPT = (
    "Singapore emergency call. Caller may code-switch between English, Mandarin, "
    "Hokkien, Malay, Tamil. "
    "Locations: Tampines, Woodlands, Jurong, Bishan, Ang Mo Kio, Clementi, Bedok, "
    "Toa Payoh, Yishun, Sengkang, Punggol, Chua Chu Kang, HDB, void deck, MRT, kopitiam."
)


@app.websocket("/ws/transcribe/{session_id}")
async def transcribe_websocket(websocket: WebSocket, session_id: str):
    """
    Proxies raw PCM16 audio (24 kHz, mono) from the dispatcher's browser
    to the Azure GPT-4o Realtime WebSocket.

    Handshake sequence (strictly enforced):
      1. Connect to Azure Realtime WS
      2. Wait for session.created
      3. Send session.update (VAD config + transcription config)
      4. Wait for session.updated
      5. Signal frontend: audio_pipeline_ready
      6. Begin forwarding PCM16 audio chunks
      7. Forward transcripts back to frontend on completion events

    Why strict handshake:
      Sending audio before session.updated causes Azure to process it with
      default settings (threshold 0.5, no custom prompt). On session restart
      the previous WS closes but Azure may still be processing — a new WS
      connecting immediately can receive events out of order. Waiting for
      session.updated on each new connection eliminates the race condition.
    """
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    logger.info(f"[realtime] WS opened — session {session_id}")
    session_log(session_id, "REALTIME_WS_OPEN")

    # Build Azure Realtime WebSocket URL
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

    try:
        async with ws_client.connect(
            azure_url,
            additional_headers={"api-key": settings.AZURE_WHISPER_API_KEY},
            max_size=None,
            ping_interval=20,    # keep connection alive
            ping_timeout=30,
        ) as azure_ws:
            logger.info(f"[realtime] Connected to Azure — session {session_id}")

            # ── Step 1: Wait for session.created ─────────────────────────────
            # Azure emits this immediately on connection. We MUST receive it
            # before sending session.update — sending before this event is
            # silently ignored on some Azure deployments.
            session_created_received = False
            while not session_created_received:
                raw = await asyncio.wait_for(azure_ws.recv(), timeout=10.0)
                event = json.loads(raw)
                etype = event.get("type", "")
                session_log(session_id, f"AZURE_INIT:{etype}")

                if etype == "session.created":
                    session_created_received = True
                    logger.info(f"[realtime] session.created — sending session.update")
                elif etype == "error":
                    err = event.get("error", {})
                    session_log(session_id, "AZURE_INIT_ERROR", err)
                    raise RuntimeError(f"Azure error before session.created: {err}")
                # else: skip any other early events (conversation.created etc.)

            # ── Step 2: Configure session ─────────────────────────────────────
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": REALTIME_INSTRUCTIONS,
                    "input_audio_format": "pcm16",
                    "turn_detection": AZURE_VAD_CONFIG,
                    "input_audio_transcription": {
                        "model": "whisper-1",
                        "prompt": REALTIME_TRANSCRIPTION_PROMPT,
                    },
                },
            }
            await azure_ws.send(json.dumps(session_config))
            logger.info(f"[realtime] session.update sent — waiting for session.updated")

            # ── Step 3: Wait for session.updated ─────────────────────────────
            # Only after this confirmation is Azure ready to receive audio.
            # Skipping this wait causes partial audio to be processed with
            # wrong VAD settings — especially harmful on session restart.
            session_ready = False
            while not session_ready:
                raw = await asyncio.wait_for(azure_ws.recv(), timeout=10.0)
                event = json.loads(raw)
                etype = event.get("type", "")
                session_log(session_id, f"AZURE_PREREADY:{etype}")

                if etype == "session.updated":
                    session_ready = True
                    logger.info(f"[realtime] session.updated — pipeline ready for audio")
                elif etype == "error":
                    err = event.get("error", {})
                    session_log(session_id, "AZURE_CONFIG_ERROR", err)
                    raise RuntimeError(f"Azure error during session.update: {err}")
                # else: skip (conversation.created etc.)

            session_log(session_id, "AUDIO_PIPELINE_READY")

            # Notify frontend that we're ready to receive audio
            try:
                await websocket.send_json({"type": "pipeline_ready"})
            except Exception:
                pass

            # ── Task A: Browser → Azure (forward PCM16 audio) ────────────────
            async def forward_audio() -> None:
                try:
                    while True:
                        raw = await websocket.receive()

                        if "bytes" in raw:
                            # Raw PCM16 bytes from AudioWorklet — base64 encode and forward
                            audio_b64 = base64.b64encode(raw["bytes"]).decode()
                            await azure_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": audio_b64,
                            }))

                        elif "text" in raw:
                            # Control messages from frontend (language hints, etc.)
                            try:
                                msg = json.loads(raw["text"])
                                msg_type = msg.get("type")

                                if msg_type == "language_hint":
                                    lang = msg.get("language", "en")
                                    session["language_hint"] = lang
                                    session_log(session_id, "LANGUAGE_HINT", lang)
                                    logger.info(f"[realtime] Language hint: {lang}")

                                    # Update transcription language mid-session
                                    # Must include full input_audio_transcription to preserve prompt
                                    await azure_ws.send(json.dumps({
                                        "type": "session.update",
                                        "session": {
                                            "input_audio_transcription": {
                                                "model": "whisper-1",
                                                "language": lang,
                                                "prompt": REALTIME_TRANSCRIPTION_PROMPT,
                                            }
                                        },
                                    }))

                                elif msg_type == "ping":
                                    # Keepalive from frontend — no action needed
                                    pass

                            except (json.JSONDecodeError, KeyError):
                                pass

                except WebSocketDisconnect:
                    logger.info(f"[realtime] Frontend disconnected — stopping audio forward")
                except Exception as e:
                    logger.error(f"[realtime] forward_audio error: {e}")

            # ── Task B: Azure → Browser (forward transcripts + VAD events) ───
            async def receive_events() -> None:
                try:
                    async for message in azure_ws:
                        event = json.loads(message)
                        etype = event.get("type", "")

                        # ── Final utterance transcript (primary output) ───────
                        if etype == "conversation.item.input_audio_transcription.completed":
                            text = event.get("transcript", "").strip()
                            if text:
                                # Append to session transcript
                                session["transcript"] = (
                                    session.get("transcript", "") + " " + text
                                ).strip()

                                # Record language if not yet set
                                if not session.get("language_detected"):
                                    session["language_detected"] = session.get("language_hint", "en")

                                session_log(session_id, "TRANSCRIPT", text)
                                logger.info(f"[realtime] TRANSCRIPT: {text[:80]}")

                                try:
                                    await websocket.send_json({
                                        "type": "transcript_update",
                                        "text": text,
                                        "full_transcript": session["transcript"],
                                        "language": session.get("language_detected", "en"),
                                        "confidence": 1.0,
                                    })
                                except Exception:
                                    break  # Frontend disconnected

                        # ── Transcription failed ──────────────────────────────
                        elif etype == "conversation.item.input_audio_transcription.failed":
                            err = event.get("error", {})
                            logger.error(f"[realtime] Transcription FAILED: {err}")
                            session_log(session_id, "TRANSCRIPTION_FAILED", err)
                            try:
                                await websocket.send_json({
                                    "type": "transcription_error",
                                    "error": str(err),
                                })
                            except Exception:
                                pass

                        # ── VAD events (informational) ────────────────────────
                        elif etype == "input_audio_buffer.speech_started":
                            logger.info(f"[realtime] VAD: speech started")
                            session_log(session_id, "VAD_SPEECH_STARTED")
                            try:
                                await websocket.send_json({"type": "speech_started"})
                            except Exception:
                                pass

                        elif etype == "input_audio_buffer.speech_stopped":
                            logger.info(f"[realtime] VAD: speech stopped")
                            session_log(session_id, "VAD_SPEECH_STOPPED")
                            try:
                                await websocket.send_json({"type": "speech_stopped"})
                            except Exception:
                                pass

                        elif etype == "input_audio_buffer.committed":
                            logger.info(f"[realtime] VAD: buffer committed — transcription triggered")
                            session_log(session_id, "VAD_BUFFER_COMMITTED")

                        # ── Session updates (e.g. after language_hint) ────────
                        elif etype == "session.updated":
                            logger.info(f"[realtime] session.updated (mid-session)")
                            session_log(session_id, "SESSION.UPDATED")

                        # ── Azure errors ──────────────────────────────────────
                        elif etype == "error":
                            err = event.get("error", {})
                            logger.error(f"[realtime] Azure error event: {err}")
                            session_log(session_id, "AZURE_ERROR", err)
                            try:
                                await websocket.send_json({
                                    "type": "azure_error",
                                    "error": str(err),
                                })
                            except Exception:
                                pass

                        # ── All other Azure events — log for diagnostics ──────
                        else:
                            session_log(session_id, f"AZURE_EVENT:{etype}")
                            logger.debug(f"[realtime] unhandled Azure event: {etype}")

                except Exception as e:
                    logger.error(f"[realtime] receive_events error: {e}")

            # ── Run both tasks concurrently ───────────────────────────────────
            # When either task finishes (e.g. frontend disconnects), cancel the other.
            tasks = [
                asyncio.create_task(forward_audio(), name="forward_audio"),
                asyncio.create_task(receive_events(), name="receive_events"),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            # Log which task finished first for diagnostics
            for task in done:
                if task.exception():
                    logger.error(f"[realtime] Task {task.get_name()} raised: {task.exception()}")
                else:
                    logger.info(f"[realtime] Task {task.get_name()} completed cleanly")

            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    except asyncio.TimeoutError:
        logger.error(f"[realtime] Timeout waiting for Azure handshake — session {session_id}")
        session_log(session_id, "REALTIME_HANDSHAKE_TIMEOUT")
        try:
            await websocket.send_json({
                "type": "azure_error",
                "error": "Azure connection timed out during handshake",
            })
        except Exception:
            pass

    except WebSocketDisconnect:
        logger.info(f"[realtime] Frontend WS disconnected — session {session_id}")

    except Exception as e:
        logger.error(f"[realtime] Unexpected error — session {session_id}: {e}")
        session_log(session_id, "REALTIME_CONNECT_ERROR", str(e))
        try:
            await websocket.send_json({"type": "azure_error", "error": str(e)})
        except Exception:
            pass

    finally:
        final_transcript = session.get("transcript", "")
        session_log(
            session_id,
            "REALTIME_WS_CLOSED",
            f"final_transcript_len={len(final_transcript)} | preview={repr(final_transcript[:100])}",
        )
        logger.info(f"[realtime] WS closed — session {session_id}")


# ── Analysis WebSocket ────────────────────────────────────────────────────────

@app.websocket("/ws/analyze/{session_id}")
async def analyze_websocket(websocket: WebSocket, session_id: str):
    """
    Dispatcher triggers AI analysis of accumulated transcript.
    Runs: incident extraction → location resolution → callback generation.
    Streams each result back as it completes.
    """
    await websocket.accept()
    if session_id not in live_sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    session = live_sessions[session_id]
    from services.crisis_llm import crisis_llm_service as _llm
    from services.location_resolver import location_resolver as _lr
    from services.callback_generator import callback_generator as _cb

    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") != "analyze":
                continue

            transcript = session.get("transcript", "").strip()
            lang = session.get("language_detected", "en")

            if not transcript:
                await websocket.send_json({"type": "error", "message": "No transcript available yet"})
                continue

            session_log(session_id, "ANALYZE_TRIGGERED", {
                "transcript_len": len(transcript),
                "lang": lang,
                "transcript": transcript,
            })

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

            # — Callback script generation —
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

            # Persist to active_calls for dispatcher action endpoints
            active_calls[session["call_id"]] = {**session, "is_dispatched": False}

    except WebSocketDisconnect:
        logger.info(f"[analyze] WS closed — session {session_id}")


# ── Dispatcher Actions ────────────────────────────────────────────────────────

@app.post("/api/call/action")
async def dispatcher_action(action: DispatcherAction):
    call = active_calls.get(action.call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    if action.action == "confirm_location":
        idx = action.location_index or 0
        candidates = call.get("location_candidates", [])
        if idx < len(candidates):
            call["confirmed_location"] = candidates[idx]
            return {"status": "location_confirmed", "location": candidates[idx]}
        raise HTTPException(status_code=400, detail="Invalid location index")

    elif action.action == "dispatch":
        call["is_dispatched"] = True
        return {
            "status": "dispatched",
            "call_id": action.call_id,
            "message": "Emergency services dispatched.",
            "incident": call.get("incident_card", {}),
            "location": call.get(
                "confirmed_location",
                call.get("location_candidates", [{}])[0] if call.get("location_candidates") else {},
            ),
        }

    elif action.action == "clarify":
        call["dispatcher_notes"] = action.dispatcher_response or ""
        return {"status": "clarification_noted"}

    elif action.action == "update_notes":
        call["dispatcher_notes"] = action.notes or ""
        return {"status": "notes_updated"}

    raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")


@app.get("/api/call/{call_id}")
async def get_call(call_id: str):
    call = active_calls.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"call_id": call_id, "data": _serialize_state(call)}


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _sse_event(event_type: str, data: dict) -> str:
    serialized = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event_type}\ndata: {serialized}\n\n"


def _serialize_state(state: dict) -> dict:
    result = {}
    for key, value in state.items():
        if hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        elif isinstance(value, list):
            result[key] = [v.model_dump() if hasattr(v, "model_dump") else v for v in value]
        else:
            result[key] = value
    return result


def _generate_confirm_q(locations) -> str:
    if not locations:
        return "Could you ask the caller for more location details?"
    top = locations[0]
    name = top.get("name") if isinstance(top, dict) else getattr(top, "name", "unknown")
    score = top.get("score", 0) if isinstance(top, dict) else getattr(top, "score", 0)
    return f"Are you near {name}? Just say yes or no. ({int(score)}% confidence)"


# ═════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
