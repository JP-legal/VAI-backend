import { NextRequest, NextResponse } from 'next/server'
import { createServiceClient } from '@/lib/supabase/server'

export async function POST(req: NextRequest) {
  const formData = await req.formData()
  const conversationId = formData.get('conversationId') as string
  const profileId = formData.get('profileId') as string
  const audio = formData.get('audio') as File

  if (!conversationId || !profileId || !audio) {
    return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
  }

  const supabase = await createServiceClient()

  const { data, error } = await supabase.storage
    .from('recordings')
    .upload(`${profileId}/${conversationId}.webm`, audio, {
      contentType: 'audio/webm',
      upsert: true,
    })

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  const { data: { publicUrl } } = supabase.storage
    .from('recordings')
    .getPublicUrl(data.path)

  await supabase
    .from('conversations')
    .update({ has_audio: true, audio_url: publicUrl })
    .eq('id', conversationId)

  return NextResponse.json({ success: true, audio_url: publicUrl })
}
