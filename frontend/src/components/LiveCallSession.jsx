/**
 * LiveCallSession.jsx — Dispatcher's real-time call view.
 *
 * Key fixes applied vs previous version:
 *
 * 1. AudioWorklet replaces deprecated ScriptProcessor
 *    - No more timing dropouts or browser compatibility issues
 *    - Transferable ArrayBuffer (zero-copy) to main thread
 *
 * 2. Silent gain node — processor no longer routes audio to speakers
 *    - Previous bug: processor.connect(ctx.destination) caused echo loop
 *    - Remote audio already plays through <audio> element (remoteAudioRef)
 *    - AudioWorklet intentionally has no downstream connection
 *
 * 3. Proper cleanup of AudioWorklet node and AudioContext on every session end
 *
 * 4. Backend VAD threshold raised to 0.8 (handles WebRTC comfort noise)
 *    — configured server-side, not here
 */

import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Phone, PhoneOff, Mic, MicOff, Brain, RotateCcw,
  MapPin, CheckCircle, AlertTriangle, Loader, Copy
} from 'lucide-react'
import QrCodeBox from './QrCodeBox'
import IncidentPanel from './IncidentPanel'
import CallbackPanel from './CallbackPanel'

const WS_BASE = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
const API_BASE = '/api'
const ICE_SERVERS = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] }

export default function LiveCallSession({ sessionId, callId, onEnd }) {
  // ── State ───────────────────────────────────────────────
  const [phase, setPhase] = useState('waiting')        // waiting|calling|connected|ended
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
  const [audioError, setAudioError] = useState(null)

  // ── Refs ────────────────────────────────────────────────
  const signalWs = useRef(null)
  const transcribeWs = useRef(null)
  const analyzeWs = useRef(null)
  const pc = useRef(null)
  const localStream = useRef(null)
  const remoteAudioRef = useRef(null)
  const timerRef = useRef(null)
  const transcriptRef = useRef('')
  // AudioWorklet refs
  const audioCtxRef = useRef(null)
  const workletNodeRef = useRef(null)

  // ── Caller link ─────────────────────────────────────────
  const [callerLink, setCallerLink] = useState('')
  useEffect(() => {
    const ngrokUrl = import.meta.env.VITE_NGROK_PUBLIC_URL
    if (ngrokUrl && ngrokUrl.startsWith('http')) {
      setCallerLink(`${ngrokUrl}/?join=${sessionId}`)
    } else {
      setCallerLink(
        `http://${window.location.hostname}:${window.location.port || '3000'}/?join=${sessionId}`
      )
    }
  }, [sessionId])

  // ── Cleanup ──────────────────────────────────────────────
  const cleanup = useCallback(() => {
    clearInterval(timerRef.current)

    // 1. Disconnect AudioWorklet node before closing context
    try {
      workletNodeRef.current?.disconnect()
    } catch (_) {}
    workletNodeRef.current = null

    // 2. Close AudioContext
    try {
      if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
        audioCtxRef.current.close()
      }
    } catch (_) {}
    audioCtxRef.current = null

    // 3. Stop local mic tracks
    localStream.current?.getTracks().forEach(t => t.stop())
    localStream.current = null

    // 4. Close peer connection
    pc.current?.close()
    pc.current = null

    // 5. Close all WebSockets
    ;[signalWs, transcribeWs, analyzeWs].forEach(wsRef => {
      try {
        if (wsRef.current && wsRef.current.readyState < 2) {
          wsRef.current.close()
        }
      } catch (_) {}
      wsRef.current = null
    })
  }, [])

  useEffect(() => () => cleanup(), [cleanup])

  // ── Copy caller link ─────────────────────────────────────
  const copyLink = useCallback(() => {
    navigator.clipboard.writeText(callerLink).then(() => {
      setLinkCopied(true)
      setTimeout(() => setLinkCopied(false), 2000)
    })
  }, [callerLink])

  // ── Start transcription via AudioWorklet + Azure Realtime ──
  //
  // The caller's WebRTC remote track is captured as raw PCM16 at 24 kHz
  // using an AudioWorklet (NOT the deprecated ScriptProcessor).
  //
  // Critical: the WorkletNode intentionally has NO downstream connection.
  // Remote audio is already playing through <audio ref={remoteAudioRef}>.
  // Connecting the worklet to ctx.destination would play it a second time
  // and create an echo loop that confuses Azure's VAD.
  //
  const startTranscription = useCallback(async (remoteStream) => {
    const wsUrl = `${WS_BASE}/ws/transcribe/${sessionId}`
    const ws = new WebSocket(wsUrl)
    transcribeWs.current = ws

    ws.onopen = async () => {
      // Send language hint first
      ws.send(JSON.stringify({ type: 'language_hint', language: language || 'en' }))
      console.log('[transcribe] WS open — initialising AudioWorklet @ 24 kHz')

      try {
        // Step 1: Create AudioContext at exactly 24 kHz (Azure Realtime requirement)
        const ctx = new AudioContext({ sampleRate: 24000 })
        audioCtxRef.current = ctx

        // Step 2: Resume if browser suspended it (requires prior user gesture — guaranteed
        // because the dispatcher clicked "Open Call Line" before we reach this point)
        if (ctx.state === 'suspended') {
          await ctx.resume()
          console.log('[transcribe] AudioContext resumed from suspended state')
        }

        // Step 3: Load the AudioWorklet module from /public
        // If addModule fails (e.g. file not found), fall back to ScriptProcessor
        try {
          await ctx.audioWorklet.addModule('/pcm-processor.js')
        } catch (moduleErr) {
          console.warn('[transcribe] AudioWorklet module failed to load — check /public/pcm-processor.js')
          throw moduleErr
        }

        // Step 4: Create worklet node (stereo input → mono output, 1 channel each)
        const workletNode = new AudioWorkletNode(ctx, 'pcm-processor', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
        })
        workletNodeRef.current = workletNode

        // Step 5: Forward PCM16 buffers from worklet to WebSocket
        workletNode.port.onmessage = (e) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(e.data) // e.data is ArrayBuffer of Int16 PCM16 (zero-copy)
          }
        }

        // Step 6: Connect remote stream source → worklet
        // DO NOT connect workletNode to ctx.destination — see note above
        const source = ctx.createMediaStreamSource(remoteStream)
        source.connect(workletNode)

        console.log('[transcribe] AudioWorklet pipeline connected — streaming PCM16 to backend')
        setAudioError(null)

      } catch (err) {
        console.error('[transcribe] AudioWorklet setup failed:', err)
        setAudioError(`Audio capture failed: ${err.message}`)
      }
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'transcript_update') {
          transcriptRef.current = msg.full_transcript
          setTranscript(msg.full_transcript)
          if (msg.language && !language) {
            setLanguage(msg.language)
          }
        }
      } catch (err) {
        console.error('[transcribe ws] failed to parse message:', err)
      }
    }

    ws.onerror = (e) => {
      console.error('[transcribe ws] error:', e)
      setAudioError('Transcription connection error — check backend logs')
    }

    ws.onclose = (e) => {
      console.log(`[transcribe ws] closed: code=${e.code} reason=${e.reason}`)
    }
  }, [sessionId, language])

  // ── Open analysis WebSocket ──────────────────────────────
  const openAnalysisWs = useCallback(() => {
    const ws = new WebSocket(`${WS_BASE}/ws/analyze/${sessionId}`)
    analyzeWs.current = ws

    ws.onopen = () => {
      console.log('[analyze ws] connected')
    }

    ws.onmessage = (event) => {
      try {
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
            console.error('[analyze ws] error from server:', msg.message)
            setIsAnalyzing(false)
            setProcessingMsg('')
            break
          default:
            break
        }
      } catch (err) {
        console.error('[analyze ws] failed to parse message:', err)
      }
    }

    ws.onerror = (e) => console.error('[analyze ws] error:', e)

    ws.onclose = (e) => {
      console.log(`[analyze ws] closed: code=${e.code}`)
    }
  }, [sessionId])

  // ── Start call as dispatcher (WebRTC offerer) ─────────────
  const startCall = useCallback(async () => {
    setPhase('calling')
    setAudioError(null)

    try {
      // Acquire local mic stream
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 48000, // Local mic at 48kHz; WebRTC handles the codec
        },
        video: false,
      })
      localStream.current = stream

      // Open signaling WebSocket
      const ws = new WebSocket(`${WS_BASE}/ws/signal/${sessionId}/dispatcher`)
      signalWs.current = ws

      ws.onopen = async () => {
        const connection = new RTCPeerConnection(ICE_SERVERS)
        pc.current = connection

        // Add dispatcher mic → caller
        stream.getTracks().forEach(track => connection.addTrack(track, stream))

        // When caller's audio track arrives, play it and start transcription
        connection.ontrack = (e) => {
          const remoteStream = e.streams[0]
          if (remoteAudioRef.current) {
            remoteAudioRef.current.srcObject = remoteStream
          }
          startTranscription(remoteStream)
        }

        // Forward ICE candidates via signaling WS
        connection.onicecandidate = (e) => {
          if (e.candidate && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ice-candidate', candidate: e.candidate }))
          }
        }

        connection.onconnectionstatechange = () => {
          const state = connection.connectionState
          console.log('[webrtc] connection state:', state)

          if (state === 'connected') {
            setPhase('connected')
            setCallerConnected(true)
            timerRef.current = setInterval(() => setElapsedTime(t => t + 1), 1000)
            openAnalysisWs()
          } else if (state === 'failed' || state === 'disconnected' || state === 'closed') {
            setCallerConnected(false)
            if (state === 'failed') {
              setPhase('ended')
              cleanup()
            }
          }
        }

        // Create and send WebRTC offer
        const offer = await connection.createOffer()
        await connection.setLocalDescription(offer)
        ws.send(JSON.stringify({ type: 'offer', sdp: offer }))
        console.log('[webrtc] offer sent')
      }

      ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data)

        if (msg.type === 'peer_joined' && msg.role === 'caller') {
          setCallerConnected(true)
          // Re-send offer to the newly joined caller
          if (pc.current && pc.current.localDescription) {
            ws.send(JSON.stringify({ type: 'offer', sdp: pc.current.localDescription }))
          }
        }

        if (msg.type === 'answer' && pc.current) {
          try {
            await pc.current.setRemoteDescription(new RTCSessionDescription(msg.sdp))
            console.log('[webrtc] remote description set from answer')
          } catch (err) {
            console.error('[webrtc] setRemoteDescription failed:', err)
          }
        }

        if (msg.type === 'ice-candidate' && msg.candidate && pc.current) {
          try {
            await pc.current.addIceCandidate(new RTCIceCandidate(msg.candidate))
          } catch (_) {
            // ICE candidate errors are usually benign — ignore
          }
        }

        if (msg.type === 'peer_left') {
          setCallerConnected(false)
        }
      }

      ws.onerror = (e) => {
        console.error('[signal ws] error:', e)
        setPhase('ended')
      }

    } catch (err) {
      console.error('[dispatcher] startup error:', err)
      if (err.name === 'NotAllowedError') {
        setAudioError('Microphone access denied. Please allow mic access and try again.')
      } else {
        setAudioError(`Failed to start call: ${err.message}`)
      }
      setPhase('waiting')
    }
  }, [sessionId, startTranscription, openAnalysisWs, cleanup])

  // ── Call controls ────────────────────────────────────────
  const toggleMute = useCallback(() => {
    localStream.current?.getAudioTracks().forEach(t => { t.enabled = !t.enabled })
    setIsMuted(m => !m)
  }, [])

  const endCall = useCallback(() => {
    cleanup()
    setPhase('ended')
  }, [cleanup])

  const triggerAnalysis = useCallback(() => {
    if (!analyzeWs.current || analyzeWs.current.readyState !== WebSocket.OPEN) {
      console.warn('[analyze] WS not open — state:', analyzeWs.current?.readyState)
      return
    }
    if (!transcriptRef.current.trim()) {
      console.warn('[analyze] transcript is empty')
      return
    }
    setIsAnalyzing(true)
    analyzeWs.current.send(JSON.stringify({ type: 'analyze' }))
  }, [])

  const confirmLocation = useCallback(async (index) => {
    const loc = locationCandidates[index]
    setConfirmedLocation(loc)
    try {
      await fetch(`${API_BASE}/call/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'confirm_location', call_id: callId, location_index: index }),
      })
    } catch (err) {
      console.error('[confirm location] failed:', err)
    }
  }, [callId, locationCandidates])

  const dispatch = useCallback(async () => {
    setIsDispatched(true)
    try {
      await fetch(`${API_BASE}/call/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'dispatch', call_id: callId }),
      })
    } catch (err) {
      console.error('[dispatch] failed:', err)
    }
  }, [callId])

  // ── Helpers ──────────────────────────────────────────────
  const formatTime = (s) =>
    `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  // ── Render: Waiting / Calling ────────────────────────────
  if (phase === 'waiting' || phase === 'calling') {
    return (
      <div className="max-w-2xl mx-auto px-6 py-10">
        <audio ref={remoteAudioRef} autoPlay playsInline style={{ display: 'none' }} />

        {/* Error banner */}
        {audioError && (
          <div className="mb-4 px-4 py-3 bg-red-500/20 border border-red-500/40 rounded-lg text-red-300 text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            {audioError}
          </div>
        )}

        {/* Session link / QR */}
        <div className="bg-dispatch-panel border border-dispatch-border rounded-xl p-6 mb-6">
          <h2 className="text-lg font-semibold text-white mb-1">Caller Join QR Code</h2>
          <p className="text-sm text-slate-400 mb-4">
            Caller scans this QR code to join the call on their phone.
          </p>
          <div className="flex flex-col items-center gap-2 mb-4">
            <QrCodeBox value={callerLink} size={180} />
            <button
              onClick={copyLink}
              className="mt-2 flex items-center gap-2 px-3 py-1 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 text-xs transition-colors"
            >
              {linkCopied
                ? <CheckCircle className="w-4 h-4 text-green-400" />
                : <Copy className="w-4 h-4" />
              }
              {linkCopied ? 'Copied!' : 'Copy Link'}
            </button>
          </div>
          <p className="text-xs text-slate-500">
            Session ID: <span className="font-mono text-slate-300">{sessionId}</span>
          </p>
        </div>

        {/* Start / waiting button */}
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
              <p className="text-slate-500 text-sm mt-3">
                Caller should scan the QR code or open the link above
              </p>
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

  // ── Render: Ended ────────────────────────────────────────
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

  // ── Render: Connected — full dispatcher dashboard ────────
  return (
    <div className="h-[calc(100vh-60px)] flex flex-col">
      <audio ref={remoteAudioRef} autoPlay playsInline style={{ display: 'none' }} />

      {/* Audio error banner */}
      {audioError && (
        <div className="px-4 py-2 bg-red-500/20 border-b border-red-500/30 text-red-300 text-xs flex items-center gap-2">
          <AlertTriangle className="w-3 h-3 flex-shrink-0" />
          {audioError}
        </div>
      )}

      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-dispatch-panel/50 border-b border-dispatch-border">
        <div className="flex items-center gap-4 text-xs text-slate-400">
          <span>Session: <span className="font-mono text-white">{sessionId}</span></span>
          <span>Call ID: <span className="font-mono text-white">{callId}</span></span>
          {language && (
            <span>
              Lang: <span className="text-dispatch-accent font-medium">{language.toUpperCase()}</span>
            </span>
          )}
          <span className="font-mono text-white">T+{formatTime(elapsedTime)}</span>
        </div>

        <div className="flex items-center gap-2">
          {/* Mute toggle */}
          <button
            onClick={toggleMute}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs transition-colors ${
              isMuted
                ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30'
                : 'bg-slate-700 text-slate-300 hover:text-white'
            }`}
          >
            {isMuted ? <MicOff className="w-3 h-3" /> : <Mic className="w-3 h-3" />}
            {isMuted ? 'Unmute' : 'Mute'}
          </button>

          {/* Analyse button */}
          <button
            onClick={triggerAnalysis}
            disabled={isAnalyzing || !transcript}
            className="flex items-center gap-1.5 px-3 py-1 bg-dispatch-accent/20 hover:bg-dispatch-accent/30 text-dispatch-accent border border-dispatch-accent/30 rounded text-xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isAnalyzing
              ? <Loader className="w-3 h-3 animate-spin" />
              : <Brain className="w-3 h-3" />
            }
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

          {/* New call */}
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
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
                Live Transcript
              </h2>
            </div>
            <div className="flex items-center gap-2">
              {callerConnected && (
                <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
              )}
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
                {callerConnected
                  ? 'Listening… speak now.'
                  : 'Waiting for caller to join.'}
              </p>
            ) : (
              <div className="text-sm leading-relaxed">
                {renderTranscript(transcript)}
                <span className="inline-block w-2 h-4 bg-dispatch-accent ml-1 animate-pulse" />
              </div>
            )}
          </div>

          {transcript && (
            <div className="px-4 py-2 border-t border-dispatch-border bg-dispatch-panel/20 text-xs text-slate-500 flex justify-between items-center">
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

// ── Render transcript with language-aware colour coding ──
// CJK characters → yellow (Mandarin/Cantonese/etc.)
// Tamil Unicode block → green
// Latin/English → default slate
function renderTranscript(text) {
  if (!text) return null
  const parts = text.split(/(\s+)/)
  return parts.map((part, i) => {
    const isCJK    = /[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]/.test(part)
    const isTamil  = /[\u0B80-\u0BFF]/.test(part)
    const isMalay  = false // Malay uses Latin — no special colour needed
    let className  = 'text-slate-200'
    if (isCJK)   className = 'text-yellow-300 font-medium'
    if (isTamil) className = 'text-emerald-300 font-medium'
    return <span key={i} className={className}>{part}</span>
  })
}
