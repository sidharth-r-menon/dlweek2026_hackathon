/**
 * LiveCallSession — Dispatcher's real-time call view.
 *
 * Responsibilities:
 * 1. WebRTC signaling (creates the offer, waits for caller)
 * 2. Audio: plays caller's voice through speakers, sends dispatcher's mic
 * 3. Audio capture: takes caller's remote track → chunks → transcription WS
 * 4. Transcription WS: receives incremental text, shows live in transcript panel
 * 5. Analysis WS: on demand → GPT-4o incident card + location + callback
 * 6. Dispatcher can confirm location, dispatch, and end call
 */
import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Phone, PhoneOff, Mic, MicOff, Brain, RotateCcw,
  MapPin, CheckCircle, AlertTriangle, Loader, Copy, QrCode
} from 'lucide-react'
import QrCodeBox from './QrCodeBox'
import IncidentPanel from './IncidentPanel'
import CallbackPanel from './CallbackPanel'

// WebSocket goes through Vite proxy → backend (works over ngrok too)
const WS_BASE = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
const API_BASE = '/api'
const ICE_SERVERS = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] }
const CHUNK_INTERVAL_MS = 3000  // 3-second non-overlapping chunks
                                 // Cross-chunk context is provided by sending the
                                 // rolling transcript tail as the Whisper prompt (server-side)

export default function LiveCallSession({ sessionId, callId, onEnd }) {
  const [phase, setPhase] = useState('waiting')  // waiting|calling|connected|ended
  const [callerConnected, setCallerConnected] = useState(false)
  const [isMuted, setIsMuted] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [language, setLanguage] = useState('')
  const [elapsedTime, setElapsedTime] = useState(0)
  const [processingMsg, setProcessingMsg] = useState('')
  const [incidentCard, setIncidentCard] = useState(null)
  const [locationCandidates, setLocationCandidates] = useState([])
  const [confirmationQuestion, setConfirmationQuestion] = useState('')
  const [callbackScript, setCallbackScript] = useState(null)
  const [confirmedLocation, setConfirmedLocation] = useState(null)
  const [isDispatched, setIsDispatched] = useState(false)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [linkCopied, setLinkCopied] = useState(false)

  const signalWs = useRef(null)
  const transcribeWs = useRef(null)
  const analyzeWs = useRef(null)
  const pc = useRef(null)
  const localStream = useRef(null)
  const remoteAudioRef = useRef(null)
  const mediaRecorder = useRef(null)
  const timerRef = useRef(null)
  const transcriptRef = useRef('')

  // Use device IP/hostname for caller link
  const [callerLink, setCallerLink] = useState('')
  useEffect(() => {
    const ngrokUrl = import.meta.env.VITE_NGROK_PUBLIC_URL
    if (ngrokUrl && ngrokUrl.startsWith('http')) {
      setCallerLink(`${ngrokUrl}/?join=${sessionId}`)
    } else {
      setCallerLink(`http://${window.location.hostname}:${window.location.port || '3000'}/?join=${sessionId}`)
    }
  }, [sessionId])

  // ── Cleanup ──────────────────────────────────────────────
  const cleanup = useCallback(() => {
    clearInterval(timerRef.current)
    if (transcribeWs.current?._recordInterval) {
      clearInterval(transcribeWs.current._recordInterval)
    }
    if (mediaRecorder.current?.state === 'recording') mediaRecorder.current.stop()
    // Disconnect Web Audio nodes and close AudioContext (if any were created)
    try {
      transcribeWs.current?._processor?.disconnect()
      transcribeWs.current?._source?.disconnect()
      transcribeWs.current?._audioCtx?.close()
    } catch (_) {}
    localStream.current?.getTracks().forEach(t => t.stop())
    pc.current?.close()
    signalWs.current?.close()
    transcribeWs.current?.close()
    analyzeWs.current?.close()
    pc.current = null
    localStream.current = null
    mediaRecorder.current = null
  }, [])

  useEffect(() => () => cleanup(), [cleanup])

  // ── Copy caller link ─────────────────────────────────────
  const copyLink = useCallback(() => {
    navigator.clipboard.writeText(callerLink).then(() => {
      setLinkCopied(true)
      setTimeout(() => setLinkCopied(false), 2000)
    })
  }, [callerLink])

  // ── Start streaming audio chunks to transcription WS ────
  const startTranscription = useCallback((remoteStream) => {
    const wsUrl = `${WS_BASE}/ws/transcribe/${sessionId}`
    const ws = new WebSocket(wsUrl)
    transcribeWs.current = ws

    ws.onopen = () => {
      // Always send a language hint — without it gpt-4o-transcribe hallucinates
      // in random languages (Korean, Arabic etc.) on background noise.
      ws.send(JSON.stringify({ type: 'language_hint', language: language || 'en' }))

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm'

      // ── Non-overlapping stop/start recording ───────────────────────────
      // Each chunk is a complete standalone WebM file (no fragments).
      // Cross-chunk context is provided on the server side by passing the
      // rolling transcript tail as the Whisper prompt= parameter — this is
      // Whisper's intended streaming design and costs zero extra audio bandwidth.
      let intervalId = null

      const recordChunk = () => {
        const recorder = new MediaRecorder(remoteStream, { mimeType })
        const chunks = []

        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data)
        }

        recorder.onstop = () => {
          if (chunks.length > 0 && ws.readyState === WebSocket.OPEN) {
            const blob = new Blob(chunks, { type: mimeType })
            if (blob.size > 1000) ws.send(blob)  // skip silent/empty chunks
          }
        }

        recorder.start()
        mediaRecorder.current = recorder
        setTimeout(() => { if (recorder.state === 'recording') recorder.stop() }, CHUNK_INTERVAL_MS)
      }

      recordChunk()
      intervalId = setInterval(recordChunk, CHUNK_INTERVAL_MS + 100)
      transcribeWs.current._recordInterval = intervalId
    }

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'transcript_update') {
        transcriptRef.current = msg.full_transcript
        setTranscript(msg.full_transcript)
        if (msg.language && !language) setLanguage(msg.language)
      }
    }

    ws.onerror = (e) => console.error('[transcribe ws] error', e)
  }, [sessionId, language])

  // ── Open analysis WS ─────────────────────────────────────
  const openAnalysisWs = useCallback(() => {
    const ws = new WebSocket(`${WS_BASE}/ws/analyze/${sessionId}`)
    analyzeWs.current = ws

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      switch (msg.type) {
        case 'processing':
          setProcessingMsg(msg.message)
          break
        case 'incident_card':
          setIncidentCard(msg.incident_card)
          setProcessingMsg('')
          break
        case 'location_candidates':
          setLocationCandidates(msg.candidates || [])
          setConfirmationQuestion(msg.confirmation_question || '')
          setProcessingMsg('')
          break
        case 'callback_script':
          setCallbackScript(msg.callback_script)
          setProcessingMsg('')
          break
        case 'analysis_complete':
          setIsAnalyzing(false)
          setProcessingMsg('')
          break
        case 'error':
          setIsAnalyzing(false)
          setProcessingMsg('')
          break
        default:
          break
      }
    }
  }, [sessionId])

  // ── Start call as dispatcher (WebRTC offerer) ────────────
  const startCall = useCallback(async () => {
    setPhase('calling')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      })
      localStream.current = stream

      // Signaling WS
      const ws = new WebSocket(`${WS_BASE}/ws/signal/${sessionId}/dispatcher`)
      signalWs.current = ws

      ws.onopen = async () => {
        // Create RTCPeerConnection
        const connection = new RTCPeerConnection(ICE_SERVERS)
        pc.current = connection

        // Add local audio tracks (dispatcher mic → caller)
        stream.getTracks().forEach(track => connection.addTrack(track, stream))

        // Play caller's audio
        connection.ontrack = (e) => {
          const remoteStream = e.streams[0]
          if (remoteAudioRef.current) {
            remoteAudioRef.current.srcObject = remoteStream
          }
          // Start transcription of caller's audio
          startTranscription(remoteStream)
        }

        // Forward ICE candidates
        connection.onicecandidate = (e) => {
          if (e.candidate && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ice-candidate', candidate: e.candidate }))
          }
        }

        connection.onconnectionstatechange = () => {
          const state = connection.connectionState
          if (state === 'connected') {
            setPhase('connected')
            setCallerConnected(true)
            timerRef.current = setInterval(() => setElapsedTime(t => t + 1), 1000)
            openAnalysisWs()
          } else if (state === 'failed' || state === 'disconnected') {
            setCallerConnected(false)
            setPhase('ended')
            cleanup()
          }
        }

        // Create & send offer
        const offer = await connection.createOffer()
        await connection.setLocalDescription(offer)
        ws.send(JSON.stringify({ type: 'offer', sdp: offer }))
      }

      ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data)

        if (msg.type === 'peer_joined' && msg.role === 'caller') {
          setCallerConnected(true)
          // If we already have a peer connection established, we're good
          // Otherwise, re-send the offer
          if (pc.current && pc.current.localDescription) {
            ws.send(JSON.stringify({ type: 'offer', sdp: pc.current.localDescription }))
          }
        }

        if (msg.type === 'answer' && pc.current) {
          await pc.current.setRemoteDescription(new RTCSessionDescription(msg.sdp))
        }

        if (msg.type === 'ice-candidate' && msg.candidate && pc.current) {
          try {
            await pc.current.addIceCandidate(new RTCIceCandidate(msg.candidate))
          } catch (_) { /* ignore */ }
        }

        if (msg.type === 'peer_left') {
          setCallerConnected(false)
        }
      }

      ws.onerror = () => setPhase('ended')

    } catch (err) {
      console.error('[dispatcher] WebRTC error:', err)
      setPhase('ended')
    }
  }, [sessionId, startTranscription, openAnalysisWs, cleanup])

  const toggleMute = useCallback(() => {
    localStream.current?.getAudioTracks().forEach(t => { t.enabled = !t.enabled })
    setIsMuted(m => !m)
  }, [])

  const endCall = useCallback(() => {
    cleanup()
    setPhase('ended')
  }, [cleanup])

  const triggerAnalysis = useCallback(() => {
    if (!analyzeWs.current || analyzeWs.current.readyState !== WebSocket.OPEN) return
    setIsAnalyzing(true)
    analyzeWs.current.send(JSON.stringify({ type: 'analyze' }))
  }, [])

  const confirmLocation = useCallback(async (index) => {
    setConfirmedLocation(locationCandidates[index])
    try {
      await fetch(`${API_BASE}/call/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'confirm_location', call_id: callId, location_index: index }),
      })
    } catch (_) {}
  }, [callId, locationCandidates])

  const dispatch = useCallback(async () => {
    setIsDispatched(true)
    try {
      await fetch(`${API_BASE}/call/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'dispatch', call_id: callId }),
      })
    } catch (_) {}
  }, [callId])

  const formatTime = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  // ── Render ───────────────────────────────────────────────

  // Waiting for call to start
  if (phase === 'waiting' || phase === 'calling') {
    return (
      <div className="max-w-2xl mx-auto px-6 py-10">
        <audio ref={remoteAudioRef} autoPlay playsInline style={{ display: 'none' }} />

        {/* Session link card with QR code */}
        <div className="bg-dispatch-panel border border-dispatch-border rounded-xl p-6 mb-6">
          <h2 className="text-lg font-semibold text-white mb-1">Caller Join QR Code</h2>
          <p className="text-sm text-slate-400 mb-4">
            Caller scans this QR code to join the call on their phone.<br />
            Both devices must be on the same Wi-Fi network.
          </p>
          <div className="flex flex-col items-center gap-2 mb-4">
            <QrCodeBox value={callerLink} size={180} />
            <button
              onClick={copyLink}
              className="mt-2 px-3 py-1 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 text-xs"
              title="Copy link"
            >
              {linkCopied ? <CheckCircle className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4 text-slate-400" />}
              <span className="ml-2">Copy Link</span>
            </button>
          </div>
          <p className="text-xs text-slate-500">
            Session ID: <span className="font-mono text-slate-300">{sessionId}</span>
          </p>
        </div>

        {/* Start call button */}
        <div className="text-center">
          {phase === 'waiting' && (
            <button
              onClick={startCall}
              className="inline-flex items-center gap-3 px-8 py-4 bg-green-500 hover:bg-green-400 text-white rounded-xl font-semibold text-lg shadow-lg shadow-green-500/30 transition-all active:scale-95"
            >
              <Phone className="w-6 h-6" />
              Open Call Line
            </button>
          )}
          {phase === 'calling' && (
            <div>
              <div className="inline-flex items-center gap-3 px-8 py-4 bg-yellow-500/20 border border-yellow-500/40 rounded-xl text-yellow-300">
                <Loader className="w-5 h-5 animate-spin" />
                Waiting for caller to join…
              </div>
              <p className="text-slate-500 text-sm mt-3">Scan the QR code above with the caller's phone</p>
            </div>
          )}
          <button
            onClick={onEnd}
            className="block mx-auto mt-4 text-slate-500 hover:text-slate-300 text-sm transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    )
  }

  if (phase === 'ended') {
    return (
      <div className="max-w-lg mx-auto px-6 py-20 text-center">
        <PhoneOff className="w-12 h-12 text-slate-500 mx-auto mb-4" />
        <h2 className="text-xl font-semibold text-white mb-2">Call Ended</h2>
        <p className="text-slate-400 mb-6">Duration: {formatTime(elapsedTime)}</p>
        <button
          onClick={onEnd}
          className="px-6 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg text-sm transition-colors"
        >
          Back to Home
        </button>
      </div>
    )
  }

  // Connected — full dashboard
  return (
    <div className="h-[calc(100vh-60px)] flex flex-col">
      <audio ref={remoteAudioRef} autoPlay playsInline style={{ display: 'none' }} />

      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-dispatch-panel/50 border-b border-dispatch-border">
        <div className="flex items-center gap-4 text-xs text-slate-400">
          <span>Session: <span className="font-mono text-white">{sessionId}</span></span>
          <span>Call ID: <span className="font-mono text-white">{callId}</span></span>
          {language && <span>Lang: <span className="text-dispatch-accent font-medium">{language.toUpperCase()}</span></span>}
          <span className="font-mono text-white">T+{formatTime(elapsedTime)}</span>
        </div>
        <div className="flex items-center gap-2">
          {/* Mute toggle */}
          <button
            onClick={toggleMute}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs transition-colors ${
              isMuted ? 'bg-yellow-500/20 text-yellow-400' : 'bg-slate-700 text-slate-300 hover:text-white'
            }`}
          >
            {isMuted ? <MicOff className="w-3 h-3" /> : <Mic className="w-3 h-3" />}
            {isMuted ? 'Unmute' : 'Muted Off'}
          </button>

          {/* Analyze button */}
          <button
            onClick={triggerAnalysis}
            disabled={isAnalyzing || !transcript}
            className="flex items-center gap-1.5 px-3 py-1 bg-dispatch-accent/20 hover:bg-dispatch-accent/30 text-dispatch-accent border border-dispatch-accent/30 rounded text-xs transition-colors disabled:opacity-40"
          >
            {isAnalyzing ? <Loader className="w-3 h-3 animate-spin" /> : <Brain className="w-3 h-3" />}
            {isAnalyzing ? 'Analysing…' : 'Run AI Analysis'}
          </button>

          {/* End call */}
          <button
            onClick={endCall}
            className="flex items-center gap-1.5 px-3 py-1 bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/30 rounded text-xs transition-colors"
          >
            <PhoneOff className="w-3 h-3" />
            End Call
          </button>

          {/* Back / reset */}
          <button
            onClick={onEnd}
            className="flex items-center gap-1 px-3 py-1 text-xs text-slate-400 hover:text-white bg-slate-700/50 hover:bg-slate-700 rounded transition-colors"
          >
            <RotateCcw className="w-3 h-3" />
            New Call
          </button>
        </div>
      </div>

      {/* Three-panel layout */}
      <div className="flex-1 grid grid-cols-3 gap-[1px] bg-dispatch-border overflow-hidden">

        {/* Panel 1 — Live Transcript */}
        <div className="bg-dispatch-dark overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-dispatch-border bg-dispatch-panel/30">
            <div className="flex items-center gap-2">
              <Mic className="w-4 h-4 text-dispatch-accent" />
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">Live Transcript</h2>
            </div>
            <div className="flex items-center gap-2">
              {callerConnected && <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />}
              <span className={`text-xs ${callerConnected ? 'text-green-400' : 'text-yellow-400'}`}>
                {callerConnected ? 'Caller on line' : 'Waiting…'}
              </span>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            {processingMsg && (
              <div className="flex items-center gap-2 mb-3 text-dispatch-accent text-sm">
                <Loader className="w-3 h-3 animate-spin" />
                {processingMsg}
              </div>
            )}
            {!transcript ? (
              <p className="text-slate-500 text-sm italic">
                {callerConnected ? 'Listening… speak now.' : 'Waiting for caller to join.'}
              </p>
            ) : (
              <div className="text-sm leading-relaxed">
                {renderTranscript(transcript)}
                <span className="inline-block w-2 h-4 bg-dispatch-accent ml-1 animate-pulse" />
              </div>
            )}
          </div>

          {transcript && (
            <div className="px-4 py-2 border-t border-dispatch-border bg-dispatch-panel/20 text-xs text-slate-500 flex justify-between">
              <span>{transcript.split(/\s+/).filter(Boolean).length} words</span>
              <button
                onClick={triggerAnalysis}
                disabled={isAnalyzing}
                className="text-dispatch-accent hover:text-white disabled:opacity-40 transition-colors"
              >
                {isAnalyzing ? 'Analysing…' : 'Analyse now →'}
              </button>
            </div>
          )}
        </div>

        {/* Panel 2 — Incident Card + Location */}
        <div className="bg-dispatch-dark overflow-hidden">
          <IncidentPanel
            incidentCard={incidentCard}
            locationCandidates={locationCandidates}
            confirmationQuestion={confirmationQuestion}
            confirmedLocation={confirmedLocation}
            isDispatched={isDispatched}
            onConfirmLocation={confirmLocation}
            onDispatch={dispatch}
          />
        </div>

        {/* Panel 3 — Callback Script */}
        <div className="bg-dispatch-dark overflow-hidden">
          <CallbackPanel callbackScript={callbackScript} language={language} />
        </div>
      </div>
    </div>
  )
}

// Highlight CJK / Tamil characters differently from English
function renderTranscript(text) {
  if (!text) return null
  const parts = text.split(/(\s+)/)
  return parts.map((part, i) => {
    const isCJK = /[\u4e00-\u9fff\u3400-\u4dbf]/.test(part)
    const isTamil = /[\u0B80-\u0BFF]/.test(part)
    let className = 'text-slate-200'
    if (isCJK) className = 'text-yellow-300 font-medium'
    else if (isTamil) className = 'text-emerald-300 font-medium'
    return <span key={i} className={className}>{part}</span>
  })
}
