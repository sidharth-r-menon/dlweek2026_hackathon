import React, { useRef, useEffect } from 'react'
import { FileText, Languages } from 'lucide-react'

export default function TranscriptPanel({ transcript, language, isStreaming, processingStatus }) {
  const scrollRef = useRef(null)

  // Auto-scroll to bottom as new words appear
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [transcript])

  // Highlight Chinese/Malay/Tamil text differently from English
  const renderTranscript = (text) => {
    if (!text) return null

    // Simple regex to detect CJK characters, Tamil, etc.
    const parts = text.split(/(\s+)/)
    return parts.map((part, i) => {
      const isCJK = /[\u4e00-\u9fff\u3400-\u4dbf]/.test(part)
      const isTamil = /[\u0B80-\u0BFF]/.test(part)
      const isMalay = /^(tolong|api|dekat|ada|orang|cepat|lah|ah)\b/i.test(part)

      let className = 'text-slate-200'
      if (isCJK) className = 'text-yellow-300 font-medium'
      else if (isTamil) className = 'text-emerald-300 font-medium'
      else if (isMalay) className = 'text-sky-300 font-medium'

      return (
        <span key={i} className={className}>
          {part}
        </span>
      )
    })
  }

  return (
    <div className="h-full flex flex-col">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-dispatch-border bg-dispatch-panel/30">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-dispatch-accent" />
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
            Live Transcript
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {language && (
            <span className="flex items-center gap-1 text-xs text-slate-400">
              <Languages className="w-3 h-3" />
              {language.toUpperCase()}
            </span>
          )}
          {isStreaming && (
            <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
          )}
        </div>
      </div>

      {/* Transcript content */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-1"
      >
        {!transcript ? (
          <div className="flex items-center justify-center h-full">
            {processingStatus ? (
              <div className="flex flex-col items-center gap-2">
                <div className="w-5 h-5 border-2 border-dispatch-accent border-t-transparent rounded-full animate-spin" />
                <p className="text-dispatch-accent text-sm">{processingStatus}</p>
              </div>
            ) : (
              <p className="text-slate-500 text-sm italic">Waiting for audio stream...</p>
            )}
          </div>
        ) : (
          <div className="text-sm leading-relaxed">
            {renderTranscript(transcript)}
            {isStreaming && (
              <span className="inline-block w-2 h-4 bg-dispatch-accent ml-1 animate-pulse" />
            )}
          </div>
        )}
      </div>

      {/* Footer stats */}
      {transcript && (
        <div className="px-4 py-2 border-t border-dispatch-border bg-dispatch-panel/20">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>{transcript.split(/\s+/).length} words</span>
            <span>Confidence: 92%</span>
          </div>
        </div>
      )}
    </div>
  )
}
