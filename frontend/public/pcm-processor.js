/**
 * pcm-processor.js — AudioWorklet processor
 *
 * Place this file in your /public directory so it is served as a static asset.
 * Loaded via: await ctx.audioWorklet.addModule('/pcm-processor.js')
 *
 * Converts Float32 WebRTC audio samples → Int16 PCM16 and posts each
 * buffer back to the main thread for forwarding to the Azure Realtime WS.
 *
 * Why AudioWorklet instead of ScriptProcessor:
 *  - ScriptProcessor is deprecated and has known timing/dropout issues
 *  - AudioWorklet runs in a dedicated audio thread — no dropouts
 *  - Transferable ArrayBuffer (zero-copy) keeps the main thread unblocked
 */

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    // Accumulate samples until we have ~170ms worth at 24kHz (4096 samples)
    // before posting — reduces WS message overhead without adding latency
    this._buffer = []
    this._bufferSize = 4096
  }

  process(inputs) {
    const input = inputs[0]

    // No audio input — keep processor alive
    if (!input || !input[0] || input[0].length === 0) return true

    const float32 = input[0]

    for (let i = 0; i < float32.length; i++) {
      this._buffer.push(float32[i])
    }

    // Once we have enough samples, convert and post
    if (this._buffer.length >= this._bufferSize) {
      const chunk = this._buffer.splice(0, this._bufferSize)
      const pcm16 = new Int16Array(chunk.length)

      for (let i = 0; i < chunk.length; i++) {
        // Clamp to [-1, 1] then scale to Int16 range
        const s = Math.max(-1, Math.min(1, chunk[i]))
        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
      }

      // Transfer the buffer (zero-copy) to main thread
      this.port.postMessage(pcm16.buffer, [pcm16.buffer])
    }

    return true // Keep processor alive
  }
}

registerProcessor('pcm-processor', PCMProcessor)
