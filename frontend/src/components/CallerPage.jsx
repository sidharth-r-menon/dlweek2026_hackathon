/**
 * CallerPage — Mobile-friendly UI for the person calling in.
 *
 * Opened via: http://<dispatcher-ip>:5173/?join=SESSION_ID
 *
 * Establishes a WebRTC connection to the dispatcher, allowing
 * bidirectional audio (caller speaks, dispatcher hears and vice-versa).
 */
import React, { useEffect, useRef, useState, useCallback } from 'react'
import { Phone, PhoneOff, Mic, MicOff } from 'lucide-react'

// WebSocket goes through Vite proxy → backend (works over ngrok too)
const WS_BASE = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`

const ICE_SERVERS = {
  iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
}

export default function CallerPage({ sessionId }) {
  const [status, setStatus] = useState('waiting') // waiting|connected|ended|error
  const [isMuted, setIsMuted] = useState(false)
  const [callDuration, setCallDuration] = useState(0)

  const signalWs = useRef(null)
  const pc = useRef(null)
  const localStream = useRef(null)
  const remoteAudioRef = useRef(null)
  const timerRef = useRef(null)

  const cleanup = useCallback(() => {
    clearInterval(timerRef.current)
    localStream.current?.getTracks().forEach(t => t.stop())
    pc.current?.close()
    signalWs.current?.close()
    pc.current = null
    localStream.current = null
  }, [])

  const startCall = useCallback(async () => {
    try {
      // Get microphone with echo/noise cancellation to prevent feedback
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      })
      localStream.current = stream

      // Open signaling WS as 'caller'
      const ws = new WebSocket(`${WS_BASE}/ws/signal/${sessionId}/caller`)
      signalWs.current = ws

      ws.onopen = () => {
        setStatus('connecting')
      }

      ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data)

        if (msg.type === 'peer_joined' && msg.role === 'dispatcher') {
          // Dispatcher is already waiting — they will send the offer
          setStatus('connecting')
        }

        if (msg.type === 'offer') {
          // Create RTCPeerConnection
          const connection = new RTCPeerConnection(ICE_SERVERS)
          pc.current = connection

          // Add local audio tracks
          stream.getTracks().forEach(track => connection.addTrack(track, stream))

          // Play remote audio (dispatcher's voice)
          connection.ontrack = (e) => {
            if (remoteAudioRef.current) {
              remoteAudioRef.current.srcObject = e.streams[0]
            }
          }

          // Send ICE candidates to dispatcher via signaling
          connection.onicecandidate = (e) => {
            if (e.candidate) {
              ws.send(JSON.stringify({ type: 'ice-candidate', candidate: e.candidate }))
            }
          }

          connection.onconnectionstatechange = () => {
            const state = connection.connectionState
            if (state === 'connected') {
              setStatus('connected')
              timerRef.current = setInterval(() => setCallDuration(d => d + 1), 1000)
            } else if (state === 'failed' || state === 'disconnected') {
              setStatus('ended')
              cleanup()
            }
          }

          // Set remote description and create answer
          await connection.setRemoteDescription(new RTCSessionDescription(msg.sdp))
          const answer = await connection.createAnswer()
          await connection.setLocalDescription(answer)
          ws.send(JSON.stringify({ type: 'answer', sdp: answer }))
        }

        if (msg.type === 'ice-candidate' && msg.candidate && pc.current) {
          try {
            await pc.current.addIceCandidate(new RTCIceCandidate(msg.candidate))
          } catch (e) {
            // ignore
          }
        }

        if (msg.type === 'peer_left') {
          setStatus('ended')
          cleanup()
        }
      }

      ws.onerror = () => setStatus('error')
      ws.onclose = () => {
        if (status === 'connected') setStatus('ended')
      }

    } catch (err) {
      console.error('Caller start error:', err)
      setStatus('error')
    }
  }, [sessionId, cleanup, status])

  const endCall = useCallback(() => {
    cleanup()
    setStatus('ended')
  }, [cleanup])

  const toggleMute = useCallback(() => {
    if (localStream.current) {
      localStream.current.getAudioTracks().forEach(t => {
        t.enabled = !t.enabled
      })
      setIsMuted(m => !m)
    }
  }, [])

  useEffect(() => () => cleanup(), [cleanup])

  const formatDuration = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  return (
    <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center p-6">
      {/* Hidden audio element for dispatcher's voice */}
      <audio ref={remoteAudioRef} autoPlay playsInline style={{ display: 'none' }} />

      <div className="w-full max-w-sm text-center">
        {/* Header */}
        <div className="mb-8">
          <div className="w-16 h-16 bg-red-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
            <Phone className="w-8 h-8 text-red-400" />
          </div>
          <h1 className="text-xl font-bold text-white">Emergency Call</h1>
          <p className="text-sm text-gray-400 mt-1">Session: <span className="font-mono text-gray-300">{sessionId}</span></p>
        </div>

        {/* Status area */}
        <div className="mb-8">
          {status === 'waiting' && (
            <div>
              <p className="text-gray-300 mb-6">Press the button below to connect your microphone and join the emergency call.</p>
              <button
                onClick={startCall}
                className="w-20 h-20 bg-green-500 hover:bg-green-400 rounded-full flex items-center justify-center mx-auto shadow-lg shadow-green-500/30 active:scale-95 transition-all"
              >
                <Phone className="w-8 h-8 text-white" />
              </button>
              <p className="text-xs text-gray-500 mt-3">Tap to start call</p>
            </div>
          )}

          {status === 'connecting' && (
            <div>
              <div className="flex items-center justify-center gap-2 mb-4">
                <div className="w-3 h-3 bg-yellow-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-3 h-3 bg-yellow-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-3 h-3 bg-yellow-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <p className="text-yellow-300 font-medium">Connecting to dispatcher…</p>
              <p className="text-xs text-gray-500 mt-1">Please wait</p>
            </div>
          )}

          {status === 'connected' && (
            <div>
              <div className="flex items-center justify-center gap-2 mb-2">
                <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                <span className="text-green-400 font-medium text-sm">Connected</span>
              </div>
              <p className="text-3xl font-mono text-white mb-6">{formatDuration(callDuration)}</p>
              <p className="text-gray-300 text-sm mb-6">Speak clearly. The dispatcher can hear you.</p>
              <div className="flex items-center justify-center gap-6">
                {/* Mute toggle */}
                <button
                  onClick={toggleMute}
                  className={`w-14 h-14 rounded-full flex items-center justify-center transition-all ${
                    isMuted ? 'bg-yellow-500/20 border border-yellow-500' : 'bg-gray-700 hover:bg-gray-600'
                  }`}
                >
                  {isMuted ? <MicOff className="w-6 h-6 text-yellow-400" /> : <Mic className="w-6 h-6 text-white" />}
                </button>
                {/* End call */}
                <button
                  onClick={endCall}
                  className="w-16 h-16 bg-red-500 hover:bg-red-400 rounded-full flex items-center justify-center shadow-lg shadow-red-500/30 active:scale-95 transition-all"
                >
                  <PhoneOff className="w-7 h-7 text-white" />
                </button>
              </div>
              {isMuted && <p className="text-yellow-400 text-xs mt-4">🔇 You are muted</p>}
            </div>
          )}

          {status === 'ended' && (
            <div>
              <div className="w-16 h-16 bg-gray-700 rounded-full flex items-center justify-center mx-auto mb-4">
                <PhoneOff className="w-8 h-8 text-gray-400" />
              </div>
              <p className="text-gray-300 font-medium">Call ended</p>
              <p className="text-gray-500 text-sm mt-1">Duration: {formatDuration(callDuration)}</p>
              <button
                onClick={() => { setStatus('waiting'); setCallDuration(0) }}
                className="mt-6 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm transition-colors"
              >
                Rejoin
              </button>
            </div>
          )}

          {status === 'error' && (
            <div>
              <p className="text-red-400 font-medium mb-2">Connection failed</p>
              <p className="text-gray-500 text-sm">Check that you are on the same network as the dispatcher, then try again.</p>
              <button
                onClick={() => setStatus('waiting')}
                className="mt-4 px-4 py-2 bg-red-500/20 text-red-400 rounded-lg text-sm"
              >
                Retry
              </button>
            </div>
          )}
        </div>

        {/* Instructions */}
        {status === 'waiting' && (
          <div className="bg-gray-800 rounded-xl p-4 text-left text-xs text-gray-400 space-y-1">
            <p>• Make sure you are on the <strong className="text-gray-300">same Wi-Fi</strong> as the dispatcher</p>
            <p>• Allow microphone access when prompted</p>
            <p>• Speak clearly and loudly</p>
          </div>
        )}
      </div>
    </div>
  )
}
