import { NextRequest, NextResponse } from 'next/server'

const ELEVENLABS_BASE = 'https://api.elevenlabs.io/v1'

export async function POST(req: NextRequest) {
  const { agentId, systemPrompt, voiceId, language, profileName } = await req.json()

  const agentIdToUse = agentId || process.env.ELEVENLABS_AGENT_ID
  if (!agentIdToUse) {
    return NextResponse.json({ error: 'ElevenLabs agent not configured' }, { status: 500 })
  }

  try {
    // Get signed URL from ElevenLabs for this session
    const response = await fetch(
      `${ELEVENLABS_BASE}/convai/conversation/get_signed_url?agent_id=${agentIdToUse}`,
      {
        method: 'GET',
        headers: { 'xi-api-key': process.env.ELEVENLABS_API_KEY! },
      }
    )

    if (!response.ok) {
      const err = await response.text()
      throw new Error(`ElevenLabs session error: ${err}`)
    }

    const { signed_url } = await response.json()
    const conversationId = crypto.randomUUID()

    return NextResponse.json({
      signed_url,
      conversation_id: conversationId,
      // Overrides are passed via the WebSocket connection initiation
      overrides: {
        agent: {
          prompt: { prompt: systemPrompt },
          first_message: `Hello! I'm the AI assistant for ${profileName}. How can I help you today?`,
          language,
        },
        tts: { voice_id: voiceId },
      },
    })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Session creation failed' },
      { status: 500 }
    )
  }
}
