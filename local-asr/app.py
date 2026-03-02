# server.py
import asyncio
import io
import json
import logging
import math
import os
import struct
import wave
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from websockets import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

# paste things here

# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return FileResponse("index.html")


def rest_headers() -> dict:
    """Auth header for REST calls — Bearer token as per the API doc."""
    return {"Authorization": f"Bearer {AZURE_API_KEY}"}


def ws_headers() -> dict:
    """Auth header for WebSocket connections — api-key style."""
    return {"api-key": AZURE_API_KEY}


# ─────────────────────────────────────────────────────────────────────────────
# DUMMY AUDIO GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _generate_dummy_wav(
    duration_sec: float = 2.0,
    sample_rate:  int   = 16000,
    frequency:    float = 440.0,
) -> bytes:
    """
    Generate a mono 16-bit PCM WAV in memory (no disk I/O).
    Produces a 440 Hz sine-wave tone — accepted by the API without errors.
    The model returns an empty/silence transcript, which is expected.
    """
    num_samples = int(sample_rate * duration_sec)
    samples = [
        int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        for i in range(num_samples)
    ]
    pcm_bytes = struct.pack(f"<{num_samples}h", *samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)           # mono
        wf.setsampwidth(2)           # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# TEST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/test/credentials")
async def test_credentials():
    """
    [TEST 1] Credential check — POST /test/credentials

    Hits the Azure deployments list REST endpoint with the configured API key.
    Does NOT open a WebSocket. Returns 200 on success, 4xx/5xx on failure.

    curl -X POST http://localhost:8000/test/credentials
    """
    if not AZURE_API_KEY:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "step": "config",
                "message": "AZURE_API_KEY is not set.",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(DEPLOYMENTS_URL, headers=rest_headers())

        if resp.status_code == 200:
            deployments = [d.get("id") for d in resp.json().get("data", [])]
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ok",
                    "step": "credentials",
                    "message": "Azure credentials are valid.",
                    "endpoint": AZURE_BASE_URL,
                    "api_version": REST_API_VERSION,
                    "deployments_found": deployments,
                },
            )
        else:
            return JSONResponse(
                status_code=resp.status_code,
                content={
                    "status": "error",
                    "step": "credentials",
                    "http_status": resp.status_code,
                    "message": "Azure rejected the request.",
                    "detail": resp.text[:500],
                },
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "step": "credentials",
                "message": f"Request failed: {e}",
            },
        )


@app.post("/test/transcription")
async def test_transcription():
    """
    [TEST 2] End-to-end transcription check — POST /test/transcription

    Steps:
      1. Generates a 2-second 440 Hz sine-wave WAV in memory (no disk I/O).
      2. POSTs it to the Azure REST transcriptions endpoint as multipart/form-data.
      3. Returns a structured JSON report with each step result.

    curl -X POST http://localhost:8000/test/transcription
    """
    report = []

    def step(name: str, status: str, detail: str = ""):
        entry = {"step": name, "status": status, "detail": detail}
        report.append(entry)
        logging.info("[transcription-test] %s", entry)

    # ── Step 1: generate dummy WAV ────────────────────────────────────────────
    try:
        wav_bytes = _generate_dummy_wav(duration_sec=2.0, sample_rate=16000)
        step("generate_audio", "ok", f"{len(wav_bytes)} bytes WAV (2s 440Hz sine @ 16kHz)")
    except Exception as e:
        step("generate_audio", "error", str(e))
        return JSONResponse(status_code=500, content={"status": "error", "report": report})

    # ── Step 2: POST to Azure REST transcription endpoint ─────────────────────
    try:
        files = {
            "file":  ("dummy_audio.wav", wav_bytes, "audio/wav"),
            "model": (None, DEPLOYMENT_NAME),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TRANSCRIPTION_URL,
                headers=rest_headers(),   # Authorization: Bearer <key>
                files=files,
            )

        step(
            "http_post",
            "ok" if resp.status_code == 200 else "error",
            f"HTTP {resp.status_code}",
        )

        if resp.status_code == 200:
            result = resp.json()
            transcript = result.get("text", "")
            step(
                "transcription_result",
                "ok",
                f"transcript='{transcript}' "
                f"(empty/silence is expected for a sine-wave dummy file)",
            )
            overall = "ok"
        else:
            step("transcription_result", "error", resp.text[:500])
            overall = "error"

    except Exception as e:
        step("http_post", "error", str(e))
        overall = "error"

    return JSONResponse(
        status_code=200 if overall == "ok" else 502,
        content={
            "status": overall,
            "model": DEPLOYMENT_NAME,
            "endpoint": TRANSCRIPTION_URL,
            "report": report,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# REALTIME WEBSOCKET TRANSCRIPTION
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/transcribe")
async def ws_transcribe(client_ws: WebSocket):
    """
    1) Accept client WebSocket
    2) Connect to Azure OpenAI Realtime WS (intent=transcription)
    3) Relay audio chunks & control messages
    4) Send transcripts back to client
    """
    await client_ws.accept()

    if not AZURE_API_KEY:
        await client_ws.send_json(
            {"type": "error", "message": "AZURE_API_KEY not set"}
        )
        await client_ws.close()
        return

    try:
        async with ws_connect(
            AZURE_REALTIME_WS,
            additional_headers=ws_headers(),
        ) as openai_ws:

            # Send transcription session configuration
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "transcription_session.update",
                        "session": {
                            "input_audio_format": "pcm16",
                            "input_audio_transcription": {
                                "model": DEPLOYMENT_NAME,
                                "prompt": "",
                                # "language": "en",
                            },
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 500,
                            },
                            "input_audio_noise_reduction": {"type": "near_field"},
                            "include": ["item.input_audio_transcription.logprobs"],
                        },
                    }
                )
            )

            # Task A: forward messages from client -> Azure OpenAI
            async def pump_client_to_openai():
                """
                Expected from client:
                - {"type":"start"}           -> optional: begins a user turn
                - {"type":"audio","b64":...} -> PCM16 mono 16k, base64
                - {"type":"commit"}          -> finalize current buffer (VAD stop or user stop)
                - {"type":"stop"}            -> request a response now
                - {"type":"end"}             -> end session
                """
                while True:
                    msg = await client_ws.receive_text()
                    data = json.loads(msg)
                    t = data.get("type")

                    if t == "audio":
                        # Append audio chunk to the current input buffer
                        await openai_ws.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": data["b64"],  # base64-encoded PCM16
                                }
                            )
                        )

            # Task B: forward events from Azure OpenAI -> client
            async def pump_openai_to_client():
                """
                We forward relevant response events back to client.
                Typical events:
                  - response.output_text.delta / completed (streamed transcripts)
                  - response.completed
                  - error
                """
                while True:
                    raw = await openai_ws.recv()
                    print(raw)
                    try:
                        event = json.loads(raw)
                    except Exception:
                        event = {"type": "raw", "data": raw}

                    etype = event.get("type", "")
                    # Relay everything; the client filters what it needs
                    await client_ws.send_json(event)

            await asyncio.gather(pump_client_to_openai(), pump_openai_to_client())

    except (WebSocketDisconnect, ConnectionClosedOK):
        pass
    except ConnectionClosedError as e:
        logging.exception("Azure OpenAI WS closed with error: %s", e)
        try:
            await client_ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    except Exception as e:
        logging.exception("Server error: %s", e)
        try:
            await client_ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        await client_ws.close()