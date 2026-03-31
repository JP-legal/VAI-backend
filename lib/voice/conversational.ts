'use client'

// ElevenLabs Conversational AI — client-side WebSocket session
// This module runs in the browser only

export interface ConversationSession {
  conversationId: string
  stop: () => void
}

export interface ConversationCallbacks {
  onUserTranscript: (text: string) => void
  onAIResponse: (text: string) => void
  onAudioStart: () => void
  onAudioEnd: () => void
  onError: (error: string) => void
  onStatusChange: (status: 'connecting' | 'connected' | 'disconnected') => void
}

export async function startVoiceSession(
  agentId: string,
  systemPrompt: string,
  voiceId: string,
  language: string,
  profileName: string,
  callbacks: ConversationCallbacks
): Promise<ConversationSession> {
  callbacks.onStatusChange('connecting')

  // Get a signed URL for the session
  const resp = await fetch('/api/voice/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agentId, systemPrompt, voiceId, language, profileName }),
  })

  if (!resp.ok) throw new Error('Failed to create voice session')
  const { signed_url, conversation_id } = await resp.json()

  const ws = new WebSocket(signed_url)
  let audioContext: AudioContext | null = null
  let audioQueue: AudioBuffer[] = []
  let isPlaying = false

  ws.onopen = () => {
    callbacks.onStatusChange('connected')
  }

  ws.onmessage = async (event) => {
    const data = JSON.parse(event.data)

    if (data.type === 'user_transcript') {
      callbacks.onUserTranscript(data.user_transcription_event?.user_transcript || '')
    }

    if (data.type === 'agent_response') {
      callbacks.onAIResponse(data.agent_response_event?.agent_response || '')
    }

    if (data.type === 'audio') {
      const audioBase64 = data.audio_event?.audio_base_64
      if (audioBase64) {
        callbacks.onAudioStart()
        if (!audioContext) audioContext = new AudioContext()
        const binary = atob(audioBase64)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
        const buffer = await audioContext.decodeAudioData(bytes.buffer)
        audioQueue.push(buffer)
        if (!isPlaying) playNextInQueue()
      }
    }
  }

  ws.onclose = () => {
    callbacks.onStatusChange('disconnected')
    audioContext?.close()
  }

  ws.onerror = () => {
    callbacks.onError('WebSocket error')
  }

  function playNextInQueue() {
    if (!audioContext || audioQueue.length === 0) {
      isPlaying = false
      callbacks.onAudioEnd()
      return
    }
    isPlaying = true
    const buffer = audioQueue.shift()!
    const source = audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(audioContext.destination)
    source.onended = playNextInQueue
    source.start()
  }

  // Capture microphone and stream to WebSocket
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const micContext = new AudioContext({ sampleRate: 16000 })
  const micSource = micContext.createMediaStreamSource(stream)
  const processor = micContext.createScriptProcessor(4096, 1, 1)

  micSource.connect(processor)
  processor.connect(micContext.destination)

  processor.onaudioprocess = (e) => {
    if (ws.readyState !== WebSocket.OPEN) return
    const inputData = e.inputBuffer.getChannelData(0)
    const pcm16 = new Int16Array(inputData.length)
    for (let i = 0; i < inputData.length; i++) {
      pcm16[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768))
    }
    const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm16.buffer)))
    ws.send(JSON.stringify({ user_audio_chunk: base64 }))
  }

  return {
    conversationId: conversation_id,
    stop: () => {
      processor.disconnect()
      micSource.disconnect()
      micContext.close()
      stream.getTracks().forEach((t) => t.stop())
      ws.close()
    },
  }
}
