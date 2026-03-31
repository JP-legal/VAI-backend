import { SUPPORTED_LANGUAGES, DEFAULT_VOICES } from '@/lib/supabase/types'

const ELEVENLABS_BASE = 'https://api.elevenlabs.io/v1'

export async function cloneVoice(
  audioFiles: Blob[],
  profileName: string,
  profileId: string
): Promise<string> {
  const formData = new FormData()
  formData.append('name', `AURA-${profileId}`)
  formData.append('description', `Cloned voice for ${profileName}`)
  audioFiles.forEach((file, i) => {
    formData.append('files', file, `sample_${i}.mp3`)
  })

  const response = await fetch(`${ELEVENLABS_BASE}/voices/add`, {
    method: 'POST',
    headers: { 'xi-api-key': process.env.ELEVENLABS_API_KEY! },
    body: formData,
  })

  if (!response.ok) {
    const err = await response.text()
    throw new Error(`ElevenLabs voice clone failed: ${err}`)
  }

  const { voice_id } = await response.json()
  return voice_id
}

export async function textToSpeech(
  text: string,
  voiceId: string,
  language: string,
  stream = true
): Promise<ReadableStream<Uint8Array> | ArrayBuffer> {
  const elevenlabsLang = SUPPORTED_LANGUAGES[language]?.elevenlabs_code || 'en'

  const response = await fetch(
    `${ELEVENLABS_BASE}/text-to-speech/${voiceId}${stream ? '/stream' : ''}`,
    {
      method: 'POST',
      headers: {
        'xi-api-key': process.env.ELEVENLABS_API_KEY!,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        text,
        model_id: 'eleven_turbo_v2_5',
        voice_settings: {
          stability: 0.5,
          similarity_boost: 0.8,
          style: 0.0,
          use_speaker_boost: true,
        },
        language_code: elevenlabsLang,
      }),
    }
  )

  if (!response.ok) {
    const err = await response.text()
    throw new Error(`ElevenLabs TTS failed: ${err}`)
  }

  return stream ? (response.body as ReadableStream<Uint8Array>) : response.arrayBuffer()
}

export async function deleteVoice(voiceId: string): Promise<void> {
  await fetch(`${ELEVENLABS_BASE}/voices/${voiceId}`, {
    method: 'DELETE',
    headers: { 'xi-api-key': process.env.ELEVENLABS_API_KEY! },
  })
}

export function getFallbackVoiceId(gender: 'male' | 'female' = 'male'): string {
  return gender === 'female' ? DEFAULT_VOICES.female_professional : DEFAULT_VOICES.male_professional
}

export async function getVoicePreview(voiceId: string, language: string): Promise<ArrayBuffer> {
  const previewText =
    language.startsWith('ar')
      ? 'مرحباً! أنا مساعدك الذكي. كيف يمكنني مساعدتك اليوم؟'
      : 'Hello! I am your AI assistant. How can I help you today?'
  return textToSpeech(previewText, voiceId, language, false) as Promise<ArrayBuffer>
}
