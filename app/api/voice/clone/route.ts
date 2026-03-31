import { NextRequest, NextResponse } from 'next/server'
import { cloneVoice } from '@/lib/voice/elevenlabs'
import { createClient, createServiceClient } from '@/lib/supabase/server'
import { deductCredits } from '@/lib/stripe/credits'

export async function POST(req: NextRequest) {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const formData = await req.formData()
  const profileId = formData.get('profileId') as string
  const profileName = formData.get('profileName') as string
  const audioFiles = formData.getAll('audio') as File[]

  if (!profileId || !profileName || audioFiles.length === 0) {
    return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
  }

  // Verify profile ownership
  const { data: profile } = await supabase
    .from('profiles')
    .select('plan')
    .eq('id', profileId)
    .eq('user_id', user.id)
    .single()

  if (!profile) return NextResponse.json({ error: 'Profile not found' }, { status: 404 })

  // Check plan gating
  const voicePlans = ['pro', 'elite', 'startup', 'business', 'enterprise']
  if (!voicePlans.includes(profile.plan)) {
    return NextResponse.json({ error: 'Voice cloning requires Pro plan or above' }, { status: 403 })
  }

  // Deduct setup credits
  const hasCredits = await deductCredits(profileId, 'voice_clone_setup')
  if (!hasCredits) {
    return NextResponse.json({ error: 'Insufficient credits (50 credits required)' }, { status: 402 })
  }

  try {
    const blobs = audioFiles.map((f) => f as unknown as Blob)
    const voiceId = await cloneVoice(blobs, profileName, profileId)

    // Upload original samples to Supabase Storage
    const serviceSupabase = await createServiceClient()
    const sampleUrls: string[] = []

    for (let i = 0; i < audioFiles.length; i++) {
      const { data } = await serviceSupabase.storage
        .from('voice-samples')
        .upload(`${profileId}/sample_${i}.mp3`, audioFiles[i], { upsert: true })
      if (data?.path) {
        const { data: { publicUrl } } = serviceSupabase.storage
          .from('voice-samples')
          .getPublicUrl(data.path)
        sampleUrls.push(publicUrl)
      }
    }

    // Save voice profile
    await serviceSupabase.from('voice_profiles').upsert(
      {
        profile_id: profileId,
        elevenlabs_voice_id: voiceId,
        is_cloned: true,
        sample_urls: sampleUrls,
      },
      { onConflict: 'profile_id' }
    )

    // Update profile voice flags
    await serviceSupabase
      .from('profiles')
      .update({ voice_enabled: true, voice_mode_enabled: true })
      .eq('id', profileId)

    return NextResponse.json({ success: true, voice_id: voiceId })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Voice cloning failed' },
      { status: 500 }
    )
  }
}
