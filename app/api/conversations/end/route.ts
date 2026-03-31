import { NextRequest, NextResponse } from 'next/server'
import { createServiceClient } from '@/lib/supabase/server'
import { consolidateMemory, generateConversationSummary } from '@/lib/memory/manager'
import { Conversation } from '@/lib/supabase/types'

export async function POST(req: NextRequest) {
  const { conversationId, visitorId } = await req.json()

  if (!conversationId) {
    return NextResponse.json({ error: 'conversationId required' }, { status: 400 })
  }

  const supabase = await createServiceClient()

  const { data: conv } = await supabase
    .from('conversations')
    .select('*')
    .eq('id', conversationId)
    .single()

  if (!conv) return NextResponse.json({ error: 'Conversation not found' }, { status: 404 })

  const endedAt = new Date().toISOString()
  const startedAt = new Date(conv.created_at)
  const durationSeconds = Math.round((Date.now() - startedAt.getTime()) / 1000)

  // Generate summary and topics
  const { summary, topics } = await generateConversationSummary(conv.messages || [])

  await supabase
    .from('conversations')
    .update({
      ended_at: endedAt,
      duration_seconds: durationSeconds,
      message_count: (conv.messages || []).length,
      ai_summary: summary,
      topics,
    })
    .eq('id', conversationId)

  // Consolidate visitor memory
  if (visitorId || conv.visitor_id) {
    const vid = visitorId || conv.visitor_id
    await consolidateMemory(vid, conv.profile_id, conv as Conversation).catch(() => {})

    // Increment visitor conversation count
    await supabase
      .from('visitors')
      .update({
        last_seen_at: endedAt,
        total_conversations: supabase.rpc('increment_conversations', { visitor_id: vid }),
      })
      .eq('id', vid)
  }

  return NextResponse.json({ success: true })
}
