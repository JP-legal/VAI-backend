import { NextRequest, NextResponse } from 'next/server'
import { createServiceClient } from '@/lib/supabase/server'
import { sendLeadNotification } from '@/lib/notifications/resend'
import { sendWhatsAppLeadNotification } from '@/lib/notifications/twilio'
import { updateVisitorIdentity } from '@/lib/visitors/identify'

export async function POST(req: NextRequest) {
  const {
    profileId,
    conversationId,
    visitorId,
    name,
    email,
    phone,
    summary,
  } = await req.json()

  if (!profileId) {
    return NextResponse.json({ error: 'profileId required' }, { status: 400 })
  }

  const supabase = await createServiceClient()

  // Create lead record
  const { data: lead } = await supabase
    .from('leads')
    .insert({
      profile_id: profileId,
      conversation_id: conversationId,
      visitor_id: visitorId,
      name,
      email,
      phone,
      summary,
      source: 'chat',
    })
    .select()
    .single()

  // Update conversation lead_captured flag
  if (conversationId) {
    await supabase
      .from('conversations')
      .update({
        lead_captured: true,
        visitor_name: name,
        visitor_email: email,
        visitor_phone: phone,
      })
      .eq('id', conversationId)
  }

  // Update visitor identity
  if (visitorId && (name || email || phone)) {
    await updateVisitorIdentity(visitorId, { name, email, phone })
  }

  // Send notifications
  const { data: profile } = await supabase
    .from('profiles')
    .select('notification_email, notify_on_lead, whatsapp_number, name')
    .eq('id', profileId)
    .single()

  if (profile?.notify_on_lead) {
    const notifEmail = profile.notification_email
    if (notifEmail && email) {
      await sendLeadNotification(
        notifEmail,
        profile.name,
        name || 'Anonymous',
        email,
        summary || '',
        conversationId || ''
      ).catch(() => {})
    }

    if (profile.whatsapp_number) {
      await sendWhatsAppLeadNotification(
        profile.whatsapp_number,
        profile.name,
        name || 'Anonymous',
        summary || ''
      ).catch(() => {})
    }
  }

  return NextResponse.json({ success: true, lead })
}
