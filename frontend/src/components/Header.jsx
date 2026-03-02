import React from 'react'
import { Radio, Circle, Zap } from 'lucide-react'

const LANGUAGE_LABELS = {
  zh: 'ZH (Mandarin)',
  ms: 'MS (Malay)',
  ta: 'TA (Tamil)',
  en: 'EN (English)',
}

export default function Header({ isLive, isLiveMode, elapsedTime, language, isDispatched }) {
  return (
    <header className="bg-dispatch-panel border-b border-dispatch-border px-6 py-3">
      <div className="flex items-center justify-between">
        {/* Left: Title */}
        <div className="flex items-center gap-3">
          <Radio className="w-6 h-6 text-dispatch-accent" />
          <div>
            <h1 className="text-lg font-bold tracking-tight">
              Cross-Lingual Safety Radio
            </h1>
            <p className="text-xs text-slate-400">
              LLM-Powered Multilingual Emergency Dispatch
            </p>
          </div>
        </div>

        {/* Center: Call status */}
        <div className="flex items-center gap-6">
          {isLive && !isDispatched && (
            <div className="flex items-center gap-2">
              {isLiveMode ? (
                <>
                  <Zap className="w-3 h-3 text-green-400 blink-recording" />
                  <span className="text-sm font-medium text-green-400">
                    LIVE AI — REAL-TIME
                  </span>
                </>
              ) : (
                <>
                  <Circle className="w-3 h-3 fill-red-500 text-red-500 blink-recording" />
                  <span className="text-sm font-medium text-red-400">
                    SIMULATION — IN PROGRESS
                  </span>
                </>
              )}
            </div>
          )}
          {isDispatched && (
            <div className="flex items-center gap-2">
              <Circle className="w-3 h-3 fill-green-500 text-green-500" />
              <span className="text-sm font-medium text-green-400">
                DISPATCHED
              </span>
            </div>
          )}
          {isLive && (
            <div className="text-sm font-mono text-slate-300">
              T+{elapsedTime}s
            </div>
          )}
        </div>

        {/* Right: Language tags */}
        <div className="flex items-center gap-2">
          {language && (
            <span className="px-2 py-1 bg-dispatch-accent/20 text-dispatch-accent text-xs font-medium rounded">
              {LANGUAGE_LABELS[language] || language.toUpperCase()}
            </span>
          )}
          {isLive && (
            <span className={`px-2 py-1 text-xs font-medium rounded flex items-center gap-1 ${
              isLiveMode
                ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                : 'bg-red-500/20 text-red-400'
            }`}>
              {isLiveMode
                ? <><Zap className="w-2 h-2" /> GPT-4o</>
                : <><Circle className="w-2 h-2 fill-current blink-recording" /> SIM</>
              }
            </span>
          )}
        </div>
      </div>
    </header>
  )
}
