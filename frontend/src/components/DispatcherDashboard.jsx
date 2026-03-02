import React from 'react'
import TranscriptPanel from './TranscriptPanel'
import IncidentPanel from './IncidentPanel'
import CallbackPanel from './CallbackPanel'
import { RotateCcw } from 'lucide-react'

export default function DispatcherDashboard({
  callState,
  isStreaming,
  isLiveMode,
  onConfirmLocation,
  onDispatch,
  onReset,
}) {
  return (
    <div className="h-[calc(100vh-60px)] flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-dispatch-panel/50 border-b border-dispatch-border">
        <div className="flex items-center gap-4 text-xs text-slate-400">
          <span>Call ID: <span className="text-white font-mono">{callState.call_id || '—'}</span></span>
          {callState.language_detected && (
            <span>Language: <span className="text-dispatch-accent font-medium">{callState.language_detected.toUpperCase()}</span></span>
          )}
          {callState.weather_context && (
            <span>Weather: <span className="text-slate-300">{callState.weather_context}</span></span>
          )}
          {isLiveMode && (
            <span className="px-2 py-0.5 bg-green-500/20 text-green-400 border border-green-500/30 rounded text-xs">
              ⚡ Live AI Mode
            </span>
          )}
          {!isLiveMode && (
            <span className="px-2 py-0.5 bg-slate-700 text-slate-400 rounded text-xs">
              Simulation
            </span>
          )}
        </div>
        <button
          onClick={onReset}
          className="flex items-center gap-1 px-3 py-1 text-xs text-slate-400 hover:text-white
                     bg-slate-700/50 hover:bg-slate-700 rounded transition-colors"
        >
          <RotateCcw className="w-3 h-3" />
          New Call
        </button>
      </div>

      {/* Three-panel layout */}
      <div className="flex-1 grid grid-cols-3 gap-[1px] bg-dispatch-border overflow-hidden">
        {/* Panel 1: Live Transcript */}
        <div className="bg-dispatch-dark overflow-hidden">
          <TranscriptPanel
            transcript={callState.raw_transcript}
            language={callState.language_detected}
            isStreaming={isStreaming}
            processingStatus={callState.processing_status}
          />
        </div>

        {/* Panel 2: Incident Card */}
        <div className="bg-dispatch-dark overflow-hidden">
          <IncidentPanel
            incidentCard={callState.incident_card}
            locationCandidates={callState.location_candidates}
            confirmationQuestion={callState.confirmation_question}
            confirmedLocation={callState.confirmed_location}
            isDispatched={callState.is_dispatched}
            onConfirmLocation={onConfirmLocation}
            onDispatch={onDispatch}
          />
        </div>

        {/* Panel 3: Callback Script */}
        <div className="bg-dispatch-dark overflow-hidden">
          <CallbackPanel
            callbackScript={callState.callback_script}
            language={callState.language_detected}
          />
        </div>
      </div>
    </div>
  )
}
