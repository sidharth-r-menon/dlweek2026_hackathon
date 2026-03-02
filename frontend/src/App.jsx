import React, { useState, useCallback } from 'react'
import Header from './components/Header'
import ScenarioSelector from './components/ScenarioSelector'
import DispatcherDashboard from './components/DispatcherDashboard'
import LiveCallSession from './components/LiveCallSession'
import CallerPage from './components/CallerPage'

const API_BASE = '/api'

// Detect if the page is opened by a caller (?join=SESSION_ID)
const urlParams = new URLSearchParams(window.location.search)
const JOIN_SESSION_ID = urlParams.get('join')

export default function App() {
  // ── Caller view (phone side) ────────────────────────────
  if (JOIN_SESSION_ID) {
    return <CallerPage sessionId={JOIN_SESSION_ID} />
  }

  return <DispatcherApp />
}

// ── Dispatcher view ─────────────────────────────────────────
function DispatcherApp() {
  const [callState, setCallState] = useState(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [elapsedTime, setElapsedTime] = useState(0)
  const [liveSession, setLiveSession] = useState(null)  // { sessionId, callId }

  // ── Simulation: stream pre-built scenario via SSE ────────
  const startScenario = useCallback(async (scenarioId) => {
    setIsStreaming(true)
    setElapsedTime(0)
    setCallState({
      call_id: '',
      language_detected: '',
      raw_transcript: '',
      incident_card: null,
      location_candidates: [],
      callback_script: null,
      confirmation_question: '',
      is_dispatched: false,
      weather_context: '',
      processing_status: '',
    })

    const startTime = Date.now()
    const timer = setInterval(() => {
      setElapsedTime(((Date.now() - startTime) / 1000).toFixed(1))
    }, 100)

    const handleSSEEvent = (eventType, data) => {
      setCallState(prev => {
        if (!prev) return prev
        switch (eventType) {
          case 'call_connected':      return { ...prev, call_id: data.call_id }
          case 'language_detected':   return { ...prev, language_detected: data.language }
          case 'transcript_update':   return { ...prev, raw_transcript: data.partial_transcript }
          case 'incident_card':       return { ...prev, incident_card: data.incident_card }
          case 'location_candidates': return { ...prev, location_candidates: data.candidates, confirmation_question: data.confirmation_question }
          case 'callback_script':     return { ...prev, callback_script: data.callback_script }
          case 'processing':          return { ...prev, processing_status: data.message }
          case 'pipeline_complete':   return { ...prev, processing_status: '' }
          default:                    return prev
        }
      })
    }

    try {
      const response = await fetch(`${API_BASE}/call/stream/${scenarioId}`)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = '', eventType = '', eventData = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('event: '))        eventType = line.slice(7).trim()
          else if (line.startsWith('data: '))    eventData = line.slice(6).trim()
          else if (line === '' && eventType && eventData) {
            try { handleSSEEvent(eventType, JSON.parse(eventData)) } catch (_) {}
            eventType = ''; eventData = ''
          }
        }
      }
    } catch (error) {
      // Fallback: direct API call
      try {
        const resp = await fetch(`${API_BASE}/call/process`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ demo_mode: true, demo_scenario: scenarioId }),
        })
        const result = await resp.json()
        if (result.data) setCallState(prev => ({ ...prev, ...result.data }))
      } catch (_) {}
    } finally {
      clearInterval(timer)
      setIsStreaming(false)
    }
  }, [])

  // ── Live call: start WebRTC session ──────────────────────
  const startLiveCall = useCallback((sessionId, callId) => {
    setLiveSession({ sessionId, callId })
    setCallState(null)
  }, [])

  const endLiveCall = useCallback(() => {
    setLiveSession(null)
  }, [])

  // ── Dispatcher actions (simulation only) ─────────────────
  const confirmLocation = useCallback(async (index) => {
    if (!callState?.call_id) return
    try {
      await fetch(`${API_BASE}/call/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'confirm_location', call_id: callState.call_id, location_index: index }),
      })
    } catch (_) {}
    setCallState(prev => ({ ...prev, confirmed_location: prev.location_candidates[index] }))
  }, [callState])

  const dispatch = useCallback(async () => {
    if (!callState?.call_id) return
    try {
      await fetch(`${API_BASE}/call/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'dispatch', call_id: callState.call_id }),
      })
    } catch (_) {}
    setCallState(prev => ({ ...prev, is_dispatched: true }))
  }, [callState])

  const resetCall = useCallback(() => {
    setCallState(null)
    setIsStreaming(false)
    setElapsedTime(0)
    setLiveSession(null)
  }, [])

  // ── Live session active ───────────────────────────────────
  if (liveSession) {
    return (
      <div className="min-h-screen bg-dispatch-dark">
        <Header
          isLive={true}
          isLiveMode={true}
          elapsedTime={elapsedTime}
          language=""
          isDispatched={false}
        />
        <LiveCallSession
          sessionId={liveSession.sessionId}
          callId={liveSession.callId}
          onEnd={endLiveCall}
        />
      </div>
    )
  }

  // ── Simulation dashboard or home ─────────────────────────
  return (
    <div className="min-h-screen bg-dispatch-dark">
      <Header
        isLive={isStreaming}
        isLiveMode={false}
        elapsedTime={elapsedTime}
        language={callState?.language_detected}
        isDispatched={callState?.is_dispatched}
      />
      {!callState ? (
        <ScenarioSelector
          onSelect={startScenario}
          onStartLiveCall={startLiveCall}
        />
      ) : (
        <DispatcherDashboard
          callState={callState}
          isStreaming={isStreaming}
          isLiveMode={false}
          onConfirmLocation={confirmLocation}
          onDispatch={dispatch}
          onReset={resetCall}
        />
      )}
    </div>
  )
}
