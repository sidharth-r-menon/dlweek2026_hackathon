"""
test_transcription.py
─────────────────────
Tests Azure OpenAI gpt-4o-transcribe via the REST transcriptions endpoint.
Generates a dummy WAV file in memory (no disk I/O) and POSTs it.

Usage:
    AZURE_API_KEY=your-key python test_transcription.py
"""

import asyncio
import base64
import io
import math
import os
import struct
import wave

import httpx

# ── Only thing you need to set ────────────────────────────────────────────────
AZURE_API_KEY = os.getenv("AZURE_API_KEY", "DdYydw65G7rl2KKfxgoWf3wbvEBc0WPRwoFVCKxV8qAXcqPQU6hAJQQJ99CCACHYHv6XJ3w3AAAAACOGG6UM")

# ── Hardcoded from the API doc ────────────────────────────────────────────────
AZURE_ENDPOINT  = "https://dlweek-resource-1.cognitiveservices.azure.com"
DEPLOYMENT_NAME = "gpt-4o-transcribe"
API_VERSION     = "2025-03-01-preview"

TRANSCRIPTION_URL = (
    f"{AZURE_ENDPOINT}/openai/deployments/{DEPLOYMENT_NAME}"
    f"/audio/transcriptions?api-version={API_VERSION}"
)


# ─────────────────────────────────────────────────────────────────────────────
# Dummy WAV generator (in-memory, no disk I/O)
# Produces a 2-second 440 Hz sine wave, mono, 16-bit, 16 kHz
# ─────────────────────────────────────────────────────────────────────────────
def generate_dummy_wav(
    duration_sec: float = 2.0,
    sample_rate:  int   = 16000,
    frequency:    float = 440.0,
) -> bytes:
    num_samples = int(sample_rate * duration_sec)
    samples = [
        int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        for i in range(num_samples)
    ]
    pcm_bytes = struct.pack(f"<{num_samples}h", *samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)          # mono
        wf.setsampwidth(2)          # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Main test
# ─────────────────────────────────────────────────────────────────────────────
async def test_transcription():
    print("=" * 60)
    print("Azure gpt-4o-transcribe — REST API test")
    print("=" * 60)
    print(f"  URL        : {TRANSCRIPTION_URL}")
    print(f"  API key set: {'YES' if AZURE_API_KEY != 'YOUR_API_KEY_HERE' else 'NO ← set AZURE_API_KEY'}")
    print()

    # Step 1: generate dummy WAV
    print("[1] Generating dummy WAV (2s 440Hz sine @ 16kHz)...")
    wav_bytes = generate_dummy_wav()
    print(f"    ✓ {len(wav_bytes)} bytes generated in memory")

    # Step 2: POST to Azure
    print(f"\n[2] POSTing to Azure transcription endpoint...")
    headers = {
        "Authorization": f"Bearer {AZURE_API_KEY}",
    }

    # multipart/form-data — model field + file field
    files = {
        "file":  ("dummy_audio.wav", wav_bytes, "audio/wav"),
        "model": (None, DEPLOYMENT_NAME),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(TRANSCRIPTION_URL, headers=headers, files=files)

        print(f"    HTTP status : {resp.status_code}")
        print(f"    Response    : {resp.text[:500]}")

        if resp.status_code == 200:
            result = resp.json()
            transcript = result.get("text", "(empty)")
            print(f"\n✅ SUCCESS")
            print(f"   Transcript  : '{transcript}'")
            print(f"   (Empty/silence is expected for a sine-wave dummy file)")
        else:
            print(f"\n❌ FAILED — Azure returned {resp.status_code}")
            print(f"   Detail: {resp.text}")

    except Exception as e:
        print(f"\n❌ Request error: {e}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_transcription())