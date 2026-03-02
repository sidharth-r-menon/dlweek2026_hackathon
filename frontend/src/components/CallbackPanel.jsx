import React, { useState } from 'react'
import { MessageSquare, Volume2, Copy, Check } from 'lucide-react'

const PURPOSE_ICONS = {
  Reassurance: '🟢',
  'Stay with victim': '🔵',
  'Location confirm': '🟡',
  'Keep line open': '🟠',
}

export default function CallbackPanel({ callbackScript, language }) {
  const [highlightedId, setHighlightedId] = useState(null)
  const [copiedId, setCopiedId] = useState(null)

  const phrases = callbackScript?.phrases || []

  const handleCopy = (text, id) => {
    navigator.clipboard.writeText(text).catch(() => {})
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  const handleHighlight = (id) => {
    setHighlightedId(id === highlightedId ? null : id)
  }

  return (
    <div className="h-full flex flex-col">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-dispatch-border bg-dispatch-panel/30">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-dispatch-accent" />
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
            Callback Script
          </h2>
        </div>
        {language && (
          <span className="text-xs text-slate-400">
            {language.toUpperCase()}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {phrases.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-slate-500 text-sm italic">
              Generating phonetic phrases...
            </p>
          </div>
        ) : (
          <>
            <p className="text-xs text-slate-500 mb-4">
              Click a phrase to highlight it for reading. These phonetic guides let
              you speak to the caller in their language without any training.
            </p>

            {phrases.map((phrase) => (
              <PhraseCard
                key={phrase.phrase_id}
                phrase={phrase}
                isHighlighted={highlightedId === phrase.phrase_id}
                isCopied={copiedId === phrase.phrase_id}
                onHighlight={() => handleHighlight(phrase.phrase_id)}
                onCopy={() => handleCopy(phrase.phonetic || phrase.native_script, phrase.phrase_id)}
              />
            ))}
          </>
        )}
      </div>

      {/* Footer */}
      {phrases.length > 0 && (
        <div className="px-4 py-2 border-t border-dispatch-border bg-dispatch-panel/20">
          <p className="text-[10px] text-slate-600 text-center">
            Phonetic approximations — read slowly and clearly. Tap phrase to enlarge.
          </p>
        </div>
      )}
    </div>
  )
}

function PhraseCard({ phrase, isHighlighted, isCopied, onHighlight, onCopy }) {
  const icon = PURPOSE_ICONS[phrase.purpose] || '⚪'

  return (
    <div
      className={`
        p-4 rounded-xl border cursor-pointer transition-all duration-200
        ${isHighlighted
          ? 'bg-dispatch-accent/10 border-dispatch-accent shadow-lg shadow-dispatch-accent/10 scale-[1.02]'
          : 'bg-dispatch-panel/40 border-dispatch-border hover:border-slate-500'
        }
      `}
      onClick={onHighlight}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm">{icon}</span>
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">
            {phrase.phrase_id}: {phrase.purpose}
          </span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onCopy()
          }}
          className="p-1 text-slate-500 hover:text-white transition-colors"
          title="Copy phonetic text"
        >
          {isCopied ? (
            <Check className="w-3.5 h-3.5 text-green-400" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      {/* Native script */}
      {phrase.native_script && (
        <p className={`text-base mb-2 ${isHighlighted ? 'text-white' : 'text-slate-200'}`}>
          {phrase.native_script}
        </p>
      )}

      {/* Phonetic guide — the main attraction */}
      {phrase.phonetic && (
        <div className={`
          p-2.5 rounded-lg mb-2
          ${isHighlighted ? 'bg-dispatch-accent/20' : 'bg-slate-800/50'}
        `}>
          <div className="flex items-center gap-1 mb-1">
            <Volume2 className={`w-3 h-3 ${isHighlighted ? 'text-dispatch-accent' : 'text-slate-500'}`} />
            <span className="text-[10px] text-slate-500 uppercase">Read aloud</span>
          </div>
          <p className={`
            text-sm font-mono tracking-wide
            ${isHighlighted ? 'text-dispatch-accent font-semibold text-base' : 'text-amber-300'}
          `}>
            {phrase.phonetic}
          </p>
        </div>
      )}

      {/* English translation */}
      <p className="text-xs text-slate-500 italic">
        {phrase.english}
      </p>
    </div>
  )
}
