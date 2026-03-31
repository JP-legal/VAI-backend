import { NextRequest, NextResponse } from 'next/server'
import { createClient, createServiceClient } from '@/lib/supabase/server'

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const serviceSupabase = await createServiceClient()

  // Verify ownership
  const { data: conv } = await serviceSupabase
    .from('conversations')
    .select('audio_url, profile_id')
    .eq('id', id)
    .single()

  if (!conv) return NextResponse.json({ error: 'Not found' }, { status: 404 })

  const { data: profile } = await serviceSupabase
    .from('profiles')
    .select('user_id')
    .eq('id', conv.profile_id)
    .single()

  if (profile?.user_id !== user.id) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }

  if (!conv.audio_url) {
    return NextResponse.json({ error: 'No recording available' }, { status: 404 })
  }

  return NextResponse.redirect(conv.audio_url)
}
