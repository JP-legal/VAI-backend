import { NextRequest, NextResponse } from 'next/server'
import { createServiceClient } from '@/lib/supabase/server'

export async function POST(req: NextRequest) {
  const body = await req.json()

  if (body.event !== 'invitee.created') {
    return NextResponse.json({ received: true })
  }

  const { payload } = body
  const email = payload?.email
  const name = payload?.name
  const profileSlug = payload?.tracking?.utm_source

  if (!email || !profileSlug) {
    return NextResponse.json({ received: true })
  }

  const supabase = await createServiceClient()

  const { data: profile } = await supabase
    .from('profiles')
    .select('id')
    .eq('slug', profileSlug)
    .single()

  if (profile) {
    await supabase.from('leads').upsert(
      {
        profile_id: profile.id,
        name,
        email,
        source: 'calendly',
        status: 'new',
      },
      { onConflict: 'profile_id,email' }
    )

    // Update any conversation with this email
    await supabase
      .from('conversations')
      .update({ meeting_booked: true })
      .eq('profile_id', profile.id)
      .eq('visitor_email', email)
  }

  return NextResponse.json({ received: true })
}
