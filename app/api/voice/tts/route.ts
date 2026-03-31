import { NextRequest, NextResponse } from 'next/server'
import { textToSpeech } from '@/lib/voice/elevenlabs'
import { createServiceClient } from '@/lib/supabase/server'
import { deductCredits } from '@/lib/stripe/credits'

export async function POST(req: NextRequest) {
  const { text, profileId, conversationId, language = 'en-US' } = await req.json()

  if (!text || !profileId) {
    return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
  }

  const supabase = await createServiceClient()

  // Load voice profile
  const { data: voiceProfile } = await supabase
    .from('voice_profiles')
    .select('*')
    .eq('profile_id', profileId)
    .single()

  const { data: profile } = await supabase
    .from('profiles')
    .select('plan, voice_enabled')
    .eq('id', profileId)
    .single()

  if (!profile?.voice_enabled) {
    return NextResponse.json({ error: 'Voice not enabled for this profile' }, { status: 403 })
  }

  // Deduct credits
  if (profile.plan !== 'elite' && profile.plan !== 'enterprise') {
    const hasCredits = await deductCredits(profileId, 'tts_playback', conversationId)
    if (!hasCredits) {
      return NextResponse.json({ error: 'Insufficient credits' }, { status: 402 })
    }
  }

  const voiceId =
    voiceProfile?.elevenlabs_voice_id || voiceProfile?.fallback_voice_id || 'pNInz6obpgDQGcFmaJgB'

  try {
    const stream = await textToSpeech(text, voiceId, language, true)

    return new Response(stream as ReadableStream, {
      headers: {
        'Content-Type': 'audio/mpeg',
        'Transfer-Encoding': 'chunked',
      },
    })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'TTS failed' },
      { status: 500 }
    )
  }
}
