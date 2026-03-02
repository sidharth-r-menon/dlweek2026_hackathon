import React from 'react'
import {
  Shield,
  MapPin,
  AlertTriangle,
  CheckCircle,
  HelpCircle,
  Send,
  ChevronRight,
} from 'lucide-react'

const URGENCY_STYLES = {
  critical: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/40', label: 'CRITICAL' },
  urgent: { bg: 'bg-orange-500/20', text: 'text-orange-400', border: 'border-orange-500/40', label: 'URGENT' },
  'non-urgent': { bg: 'bg-yellow-500/20', text: 'text-yellow-400', border: 'border-yellow-500/40', label: 'NON-URGENT' },
  unknown: { bg: 'bg-slate-500/20', text: 'text-slate-400', border: 'border-slate-500/40', label: 'UNKNOWN' },
}

const CONFIDENCE_COLORS = {
  HIGH: 'text-green-400',
  MEDIUM: 'text-yellow-400',
  LOW: 'text-orange-400',
  UNRESOLVED: 'text-red-400',
}

export default function IncidentPanel({
  incidentCard,
  locationCandidates,
  confirmationQuestion,
  confirmedLocation,
  isDispatched,
  onConfirmLocation,
  onDispatch,
}) {
  const urgencyStyle = incidentCard
    ? URGENCY_STYLES[incidentCard.medical_urgency] || URGENCY_STYLES.unknown
    : URGENCY_STYLES.unknown

  return (
    <div className="h-full flex flex-col">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-dispatch-border bg-dispatch-panel/30">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-dispatch-accent" />
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
            Incident Card
          </h2>
        </div>
        {incidentCard && (
          <span className={`px-2 py-0.5 text-xs font-bold rounded ${urgencyStyle.bg} ${urgencyStyle.text} ${urgencyStyle.border} border`}>
            {urgencyStyle.label}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {!incidentCard ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-slate-500 text-sm italic">
              Analysing transcript...
            </p>
          </div>
        ) : (
          <>
            {/* Incident Type */}
            <Field
              label="Incident Type"
              value={incidentCard.incident_type}
              confidence={incidentCard.confidence?.incident_type}
              icon={<AlertTriangle className="w-4 h-4" />}
            />

            {/* Victim */}
            <Field
              label="Victim"
              value={incidentCard.victim_description}
              confidence={incidentCard.confidence?.victim_description}
            />

            {/* Medical Urgency */}
            <div className={`p-3 rounded-lg border ${urgencyStyle.bg} ${urgencyStyle.border}`}>
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400 uppercase">Medical Urgency</span>
                <span className={`text-sm font-bold ${urgencyStyle.text}`}>
                  {urgencyStyle.label}
                </span>
              </div>
            </div>

            {/* Threat */}
            {incidentCard.threat_present !== null && incidentCard.threat_present !== undefined && (
              <Field
                label="Threat Present"
                value={incidentCard.threat_present ? 'YES — Active threat' : 'No threat detected'}
                confidence={incidentCard.confidence?.threat_present}
              />
            )}

            {/* Caller Info */}
            <div className="grid grid-cols-2 gap-2">
              <MiniField label="Caller Language" value={incidentCard.caller_language} />
              <MiniField label="Emotional State" value={incidentCard.caller_emotional_state?.toUpperCase()} />
            </div>

            {/* Location Candidates */}
            <div className="border-t border-dispatch-border pt-4">
              <div className="flex items-center gap-2 mb-3">
                <MapPin className="w-4 h-4 text-dispatch-accent" />
                <span className="text-sm font-semibold text-slate-300">Location</span>
              </div>

              {confirmedLocation ? (
                <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/30">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-green-400" />
                    <span className="text-sm text-green-300 font-medium">CONFIRMED</span>
                  </div>
                  <p className="text-sm text-white mt-1">{confirmedLocation.name}</p>
                  <p className="text-xs text-slate-400">{confirmedLocation.address}</p>
                </div>
              ) : locationCandidates && locationCandidates.length > 0 ? (
                <div className="space-y-2">
                  {locationCandidates.map((loc, idx) => (
                    <button
                      key={idx}
                      onClick={() => onConfirmLocation(idx)}
                      className="w-full text-left p-3 rounded-lg bg-dispatch-panel/50
                                 border border-dispatch-border hover:border-dispatch-accent
                                 transition-colors group"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex-1">
                          <p className="text-sm text-white font-medium">{loc.name}</p>
                          <p className="text-xs text-slate-400 mt-0.5">{loc.address}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <ConfidenceBadge score={loc.score} />
                          <ChevronRight className="w-4 h-4 text-slate-500 group-hover:text-dispatch-accent" />
                        </div>
                      </div>
                    </button>
                  ))}
                  {confirmationQuestion && (
                    <div className="p-2 bg-blue-500/10 border border-blue-500/20 rounded-lg mt-2">
                      <div className="flex items-start gap-2">
                        <HelpCircle className="w-4 h-4 text-blue-400 mt-0.5 shrink-0" />
                        <p className="text-xs text-blue-300">{confirmationQuestion}</p>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-500 italic">Resolving location...</p>
              )}
            </div>

            {/* Missing Info */}
            {incidentCard.missing_critical_info?.length > 0 && (
              <div className="border-t border-dispatch-border pt-3">
                <span className="text-xs text-slate-400 uppercase">Missing Info</span>
                <ul className="mt-1 space-y-1">
                  {incidentCard.missing_critical_info.map((info, i) => (
                    <li key={i} className="text-xs text-red-400 flex items-center gap-1">
                      <span className="w-1 h-1 bg-red-400 rounded-full" />
                      {info}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Suggested Questions */}
            {incidentCard.suggested_clarifying_questions?.length > 0 && (
              <div className="border-t border-dispatch-border pt-3">
                <span className="text-xs text-slate-400 uppercase">Ask Caller</span>
                <ul className="mt-1 space-y-1">
                  {incidentCard.suggested_clarifying_questions.map((q, i) => (
                    <li key={i} className="text-xs text-blue-400 flex items-center gap-1">
                      <HelpCircle className="w-3 h-3 shrink-0" />
                      {q}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      {/* Dispatch Button */}
      {incidentCard && (
        <div className="p-4 border-t border-dispatch-border">
          {isDispatched ? (
            <div className="w-full py-3 rounded-lg bg-green-500/20 border border-green-500/40 text-center">
              <div className="flex items-center justify-center gap-2">
                <CheckCircle className="w-5 h-5 text-green-400" />
                <span className="text-green-400 font-bold">DISPATCHED</span>
              </div>
              <p className="text-xs text-green-300/70 mt-1">Emergency services en route</p>
            </div>
          ) : (
            <button
              onClick={onDispatch}
              className="w-full py-3 rounded-lg bg-red-600 hover:bg-red-500
                         text-white font-bold text-sm uppercase tracking-wider
                         transition-colors flex items-center justify-center gap-2
                         shadow-lg shadow-red-600/20"
            >
              <Send className="w-4 h-4" />
              DISPATCH EMERGENCY SERVICES
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function Field({ label, value, confidence, icon }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1">
          {icon && <span className="text-slate-400">{icon}</span>}
          <span className="text-xs text-slate-400 uppercase">{label}</span>
        </div>
        {confidence && (
          <span className={`text-xs font-medium ${CONFIDENCE_COLORS[confidence]}`}>
            {confidence}
          </span>
        )}
      </div>
      <p className="text-sm text-white">{value || '—'}</p>
    </div>
  )
}

function MiniField({ label, value }) {
  return (
    <div className="p-2 bg-dispatch-panel/30 rounded">
      <span className="text-[10px] text-slate-500 uppercase block">{label}</span>
      <span className="text-xs text-slate-300">{value || '—'}</span>
    </div>
  )
}

function ConfidenceBadge({ score }) {
  const pct = Math.min(Math.round(score), 100)
  let color = 'text-green-400'
  if (pct < 50) color = 'text-red-400'
  else if (pct < 75) color = 'text-yellow-400'

  return (
    <span className={`text-xs font-bold ${color}`}>
      {pct}%
    </span>
  )
}
