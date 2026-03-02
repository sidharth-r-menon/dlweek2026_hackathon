import React, { useState } from 'react'
import { Play, Globe, AlertTriangle, Car, Flame, Siren, Zap, FlaskConical, Phone, Wifi } from 'lucide-react'

const SCENARIOS = [
  {
    id: 'mandarin_medical',
    name: 'Mandarin Medical Emergency',
    description: 'Elderly man collapses near Tua Pek Kong Temple â€” Mandarin/English code-switch',
    icon: AlertTriangle,
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    languages: ['ZH', 'EN'],
  },
  {
    id: 'malay_fire',
    name: 'Malay Fire Emergency',
    description: 'Building fire near Masjid Sultan â€” Malay/English code-switch',
    icon: Flame,
    color: 'text-orange-400',
    bgColor: 'bg-orange-500/10',
    borderColor: 'border-orange-500/30',
    languages: ['MS', 'EN'],
  },
  {
    id: 'tamil_accident',
    name: 'Tamil Road Accident',
    description: 'Pedestrian hit near Mustafa Centre â€” Tamil/English code-switch',
    icon: Car,
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    languages: ['TA', 'EN'],
  },
  {
    id: 'singlish_violence',
    name: 'Singlish Violence Report',
    description: 'Public fight at Tampines Block 201 â€” English/Singlish',
    icon: Siren,
    color: 'text-purple-400',
    bgColor: 'bg-purple-500/10',
    borderColor: 'border-purple-500/30',
    languages: ['EN'],
  },
]

export default function ScenarioSelector({ onSelect, onStartLiveCall }) {
  const [loading, setLoading] = useState(null)
  const [activeTab, setActiveTab] = useState('simulation')
  const [creatingSession, setCreatingSession] = useState(false)

  const handleSelect = (id) => {
    setLoading(id)
    onSelect(id, 'simulation')
  }

  const handleStartLiveCall = async () => {
    setCreatingSession(true)
    try {
      const res = await fetch('/api/session/create', { method: 'POST' })
      const data = await res.json()
      onStartLiveCall(data.session_id, data.call_id)
    } catch (err) {
      console.error('Failed to create session:', err)
      setCreatingSession(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-12">
      {/* Hero */}
      <div className="text-center mb-8">
        <div className="flex items-center justify-center gap-3 mb-4">
          <Globe className="w-10 h-10 text-dispatch-accent" />
        </div>
        <h2 className="text-3xl font-bold mb-2">Emergency Dispatch System</h2>
        <p className="text-slate-400 max-w-2xl mx-auto text-sm">
          Multilingual real-time dispatch powered by Azure OpenAI GPT-4o &amp; gpt-4o-transcribe.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-6 bg-dispatch-panel border border-dispatch-border rounded-xl p-1">
        <button
          onClick={() => setActiveTab('simulation')}
          className={`flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-all duration-150 ${
            activeTab === 'simulation'
              ? 'bg-dispatch-accent text-black shadow'
              : 'text-slate-400 hover:text-white hover:bg-white/5'
          }`}
        >
          <FlaskConical className="w-4 h-4" />
          Simulation
          <span className={`hidden sm:inline-flex px-1.5 py-0.5 rounded text-xs ${
            activeTab === 'simulation' ? 'bg-black/20 text-black/70' : 'bg-slate-700 text-slate-300'
          }`}>Offline</span>
        </button>
        <button
          onClick={() => setActiveTab('live')}
          className={`flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-all duration-150 ${
            activeTab === 'live'
              ? 'bg-green-500 text-black shadow'
              : 'text-slate-400 hover:text-white hover:bg-white/5'
          }`}
        >
          <Zap className="w-4 h-4" />
          Live Demo
          <span className={`hidden sm:inline-flex px-1.5 py-0.5 rounded text-xs border ${
            activeTab === 'live'
              ? 'bg-black/20 text-black/70 border-black/10'
              : 'bg-green-500/20 text-green-400 border-green-500/40'
          }`}>Real AI</span>
        </button>
      </div>

      {/* === SIMULATION TAB === */}
      {activeTab === 'simulation' && (
        <>
          <div className="flex items-start gap-3 mb-6 p-3 rounded-lg border bg-slate-700/20 border-dispatch-border text-slate-400 text-sm">
            <FlaskConical className="w-4 h-4 mt-0.5 shrink-0" />
            <p>
              Pre-built scenarios with hardcoded data â€” no API keys needed. Instant, deterministic
              results. Great for demos without live credentials.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {SCENARIOS.map((scenario) => {
              const Icon = scenario.icon
              return (
                <button
                  key={scenario.id}
                  onClick={() => handleSelect(scenario.id)}
                  disabled={loading !== null}
                  className={`
                    relative text-left p-6 rounded-xl border
                    ${scenario.bgColor} ${scenario.borderColor}
                    hover:border-dispatch-accent transition-all duration-200
                    ${loading === scenario.id ? 'ring-2 ring-dispatch-accent' : ''}
                    disabled:opacity-50
                  `}
                >
                  <div className="flex items-start gap-4">
                    <div className={`p-2 rounded-lg ${scenario.bgColor}`}>
                      <Icon className={`w-6 h-6 ${scenario.color}`} />
                    </div>
                    <div className="flex-1">
                      <h3 className="font-semibold text-white mb-1">{scenario.name}</h3>
                      <p className="text-sm text-slate-400 mb-3">{scenario.description}</p>
                      <div className="flex items-center gap-2">
                        {scenario.languages.map((lang) => (
                          <span key={lang} className="px-2 py-0.5 bg-slate-700 text-slate-300 text-xs rounded">
                            {lang}
                          </span>
                        ))}
                      </div>
                    </div>
                    <Play className={`w-5 h-5 ${scenario.color} mt-1`} />
                  </div>
                  {loading === scenario.id && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/30 rounded-xl">
                      <div className="flex items-center gap-2">
                        <div className="w-4 h-4 border-2 border-dispatch-accent border-t-transparent rounded-full animate-spin" />
                        <span className="text-sm text-dispatch-accent font-medium">Connectingâ€¦</span>
                      </div>
                    </div>
                  )}
                </button>
              )
            })}
          </div>

          <div className="mt-6 p-3 bg-dispatch-panel rounded-lg border border-dispatch-border">
            <p className="text-xs text-slate-500 text-center">
              Simulation uses pre-built data â€” no API keys needed. Switch to Live Demo for a real
              two-way call with Azure OpenAI.
            </p>
          </div>
        </>
      )}

      {/* === LIVE DEMO TAB === */}
      {activeTab === 'live' && (
        <div className="flex flex-col items-center">
          <div className="flex items-start gap-3 mb-8 p-3 rounded-lg border bg-green-500/5 border-green-500/20 text-green-300 text-sm w-full">
            <Zap className="w-4 h-4 mt-0.5 shrink-0" />
            <p>
              A real two-way audio call between you (dispatcher, on this laptop) and a caller
              (on their phone). GPT-4o-transcribe provides live transcription, and GPT-4o
              performs incident extraction and generates callback phrases.
            </p>
          </div>

          {/* How it works */}
          <div className="w-full bg-dispatch-panel border border-dispatch-border rounded-xl p-6 mb-8">
            <h3 className="text-white font-semibold mb-4">How it works</h3>
            <ol className="space-y-3 text-sm text-slate-300">
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-dispatch-accent/20 text-dispatch-accent rounded-full flex items-center justify-center text-xs font-bold shrink-0">1</span>
                <span>Click <strong className="text-white">Start Live Call</strong> â€” a session ID and share link will be generated.</span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-dispatch-accent/20 text-dispatch-accent rounded-full flex items-center justify-center text-xs font-bold shrink-0">2</span>
                <span>Share the link with the caller. They open it on their phone and tap <strong className="text-white">Join Call</strong>.</span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-dispatch-accent/20 text-dispatch-accent rounded-full flex items-center justify-center text-xs font-bold shrink-0">3</span>
                <span>WebRTC connects you â€” you hear the caller in your speakers and they hear you through your mic.</span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-dispatch-accent/20 text-dispatch-accent rounded-full flex items-center justify-center text-xs font-bold shrink-0">4</span>
                <span>The caller's speech is transcribed in real-time using <strong className="text-white">gpt-4o-transcribe</strong>.</span>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 bg-dispatch-accent/20 text-dispatch-accent rounded-full flex items-center justify-center text-xs font-bold shrink-0">5</span>
                <span>Click <strong className="text-white">Run AI Analysis</strong> at any time to extract incident details, resolve location, and generate dispatcher callback phrases.</span>
              </li>
            </ol>
          </div>

          {/* Network requirement */}
          <div className="flex items-center gap-2 mb-8 text-sm text-yellow-400 bg-yellow-500/10 border border-yellow-500/20 rounded-lg px-4 py-3 w-full">
            <Wifi className="w-4 h-4 shrink-0" />
            <span>Caller's phone must be on the <strong>same Wi-Fi network</strong> as this laptop (or you can use a tunnel like Ngrok for remote access).</span>
          </div>

          {/* Start button */}
          <button
            onClick={handleStartLiveCall}
            disabled={creatingSession}
            className="inline-flex items-center gap-3 px-10 py-5 bg-green-500 hover:bg-green-400 disabled:opacity-60 text-black rounded-xl font-bold text-lg shadow-lg shadow-green-500/30 transition-all active:scale-95"
          >
            {creatingSession ? (
              <>
                <div className="w-5 h-5 border-2 border-black border-t-transparent rounded-full animate-spin" />
                Creating sessionâ€¦
              </>
            ) : (
              <>
                <Phone className="w-6 h-6" />
                Start Live Call
              </>
            )}
          </button>
          <p className="text-xs text-slate-500 mt-3">
            Requires valid Azure OpenAI credentials in your <code className="text-slate-400">.env</code> file.
          </p>
        </div>
      )}
    </div>
  )
}

