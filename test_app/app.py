import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
import asyncio
import websockets
import json
import base64
import pyaudio
import os
import queue
import threading
import time
from dotenv import load_dotenv
from openai import AzureOpenAI

# --- Configuration & State ---
load_dotenv()

TRANSCRIPTION_ENDPOINT = os.getenv("AZURE_TRANSCRIPTION_ENDPOINT")
TRANSCRIPTION_API_KEY = os.getenv("AZURE_TRANSCRIPTION_API_KEY")
TRANSCRIPTION_DEPLOYMENT = os.getenv("AZURE_TRANSCRIPTION_DEPLOYMENT")
REALTIME_API_VERSION = os.getenv("AZURE_REALTIME_API_VERSION", "2025-03-01-preview")

CHAT_ENDPOINT = os.getenv("AZURE_CHAT_ENDPOINT")
CHAT_API_KEY = os.getenv("AZURE_CHAT_API_KEY")
CHAT_DEPLOYMENT = os.getenv("AZURE_CHAT_DEPLOYMENT")
CHAT_API_VERSION = os.getenv("AZURE_CHAT_API_VERSION", "2024-08-01-preview")

if "transcript_queue" not in st.session_state:
    st.session_state.transcript_queue = queue.Queue()
if "is_recording" not in st.session_state:
    st.session_state.is_recording = False
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""
if "corrected_text" not in st.session_state:
    st.session_state.corrected_text = ""

# --- Post-Processing ---
chat_client = AzureOpenAI(
    api_key=CHAT_API_KEY,  
    api_version=CHAT_API_VERSION,
    azure_endpoint=CHAT_ENDPOINT
)

def fix_transcript(raw_text: str) -> str:
    system_prompt = """
    You are a real-time transcription correction assistant. 
    Your task is to correct any spelling discrepancies in the transcribed text, 
    especially technical terms or product names (e.g., ZyntriQix, Digique Plus, PULSE).
    Add necessary punctuation and capitalization. Output ONLY the corrected text.
    """
    try:
        response = chat_client.chat.completions.create(
            model=CHAT_DEPLOYMENT,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f" [GPT Error: {e}] "

# --- Audio Capture & WebSocket Logic ---
async def stream_audio_and_transcribe():
    print("\n--- Starting Audio Stream ---")
    
    try:
        print("🎙️ Initializing PyAudio...")
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 24000 
        CHUNK = 2048
        
        audio = pyaudio.PyAudio()
        stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
        print("✅ Microphone active!")
    except Exception as e:
        error_msg = f"Microphone Error: {e}"
        print(f"❌ {error_msg}")
        st.session_state.transcript_queue.put({"raw": f"**[Error]** {error_msg}", "fixed": ""})
        st.session_state.is_recording = False
        return

    ws_base = TRANSCRIPTION_ENDPOINT.replace("https://", "wss://")
    ws_url = f"{ws_base}/openai/realtime?api-version={REALTIME_API_VERSION}&deployment={TRANSCRIPTION_DEPLOYMENT}"
    headers = {"api-key": TRANSCRIPTION_API_KEY}
    
    print(f"🔗 Connecting to WebSocket: {ws_url}")
    
    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            print("✅ WebSocket Connected Successfully!")
            
            session_update = {
                "type": "session.update",
                "session": {
                    "input_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500
                    },
                    "input_audio_transcription": {
                        "model": "whisper-1" # Azure requires this specific string to enable the transcript events
                    }
                }
            }
            await ws.send(json.dumps(session_update))
            print("📤 Sent session update payload.")

            async def send_audio():
                print("🔊 Sending audio chunks...")
                while st.session_state.is_recording:
                    try:
                        data = stream.read(CHUNK, exception_on_overflow=False)
                        base64_audio = base64.b64encode(data).decode('utf-8')
                        payload = {"type": "input_audio_buffer.append", "audio": base64_audio}
                        await ws.send(json.dumps(payload))
                        await asyncio.sleep(0.01)
                    except Exception as e:
                        print(f"❌ Send Audio Error: {e}")
                        break
            
            async def receive_transcripts():
                print("👂 Listening for server responses...")
                while st.session_state.is_recording:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        data = json.loads(message)
                        
                        # DEBUG: Print every event type received from Azure
                        print(f"📥 Received event: {data.get('type')}")
                        
                        # Check for Azure errors
                        if data.get("type") == "error":
                            err_msg = data.get("error", {}).get("message", "Unknown error")
                            print(f"❌ Azure Error: {err_msg}")
                            st.session_state.transcript_queue.put({"raw": f"**[Azure Error]** {err_msg}", "fixed": ""})
                        
                        # Check for transcription
                        elif data.get("type") in ["conversation.item.created", "conversation.item.input_audio_transcription.completed", "transcription.completed"]:
                            
                            # Extract text depending on payload shape
                            raw_text = ""
                            if "transcript" in data:
                                raw_text = data["transcript"]
                            else:
                                content = data.get("item", {}).get("content", [])
                                for block in content:
                                    if block.get("type") in ["text", "transcript"]:
                                        raw_text = block.get("text") or block.get("transcript", "")
                            
                            if raw_text.strip():
                                print(f"📝 Raw Text Detected: {raw_text}")
                                fixed_text = fix_transcript(raw_text)
                                st.session_state.transcript_queue.put({"raw": raw_text, "fixed": fixed_text})

                    except asyncio.TimeoutError:
                        continue 
                    except websockets.exceptions.ConnectionClosed as e:
                        print(f"❌ WebSocket Closed: {e}")
                        st.session_state.transcript_queue.put({"raw": "**[WebSocket Disconnected]**", "fixed": ""})
                        break
                    except Exception as e:
                        print(f"❌ Receive Error: {e}")
                        break

            await asyncio.gather(send_audio(), receive_transcripts())
            
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        st.session_state.transcript_queue.put({"raw": f"**[Connection Error]** {e}", "fixed": ""})
    finally:
        print("🛑 Cleaning up audio stream...")
        stream.stop_stream()
        stream.close()
        audio.terminate()
        st.session_state.is_recording = False # Auto-stop UI loop

def run_async_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(stream_audio_and_transcribe())

# --- Streamlit UI ---
st.set_page_config(page_title="Azure Realtime Transcription", layout="wide")
st.title("🎙️ Azure Real-Time Transcription + GPT-4o")

col1, col2 = st.columns(2)
with col1:
    st.subheader("Raw Transcription")
    raw_container = st.empty()
with col2:
    st.subheader("Corrected (GPT-4o)")
    fixed_container = st.empty()

start_col, stop_col = st.columns([1, 10])
with start_col:
    if st.button("Start Recording"):
        if not st.session_state.is_recording:
            st.session_state.is_recording = True
            t = threading.Thread(target=run_async_loop, daemon=True)
            add_script_run_ctx(t)
            t.start()
            st.rerun()

with stop_col:
    if st.button("Stop"):
        st.session_state.is_recording = False
        st.rerun()

if st.session_state.is_recording:
    st.info("Recording... Speak into your microphone. Check your terminal for logs.")
    
    while st.session_state.is_recording:
        try:
            item = st.session_state.transcript_queue.get_nowait()
            st.session_state.raw_text += item["raw"] + " "
            st.session_state.corrected_text += item["fixed"] + " "
            
            raw_container.markdown(f"> {st.session_state.raw_text}")
            fixed_container.markdown(f"**> {st.session_state.corrected_text}**")
        except queue.Empty:
            pass
        
        time.sleep(0.1)

if not st.session_state.is_recording:
    raw_container.markdown(f"> {st.session_state.raw_text}")
    fixed_container.markdown(f"**> {st.session_state.corrected_text}**")