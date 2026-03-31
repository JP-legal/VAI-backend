import { NextRequest, NextResponse } from 'next/server'
import { claude, CLAUDE_MODEL } from '@/lib/ai/claude'
import { buildSystemPrompt } from '@/lib/ai/persona-builder'
import { detectLanguage } from '@/lib/language/detector'
import { getVisitorMemory } from '@/lib/memory/manager'
import { createServiceClient } from '@/lib/supabase/server'
import { deductCredits } from '@/lib/stripe/credits'
import { Message } from '@/lib/supabase/types'

export async function POST(req: NextRequest) {
  const {
    profileId,
    conversationId,
    messages,
    currentLanguage = 'en-US',
    visitorId,
    visitorName,
  } = await req.json()

  if (!profileId || !messages?.length) {
    return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
  }

  const supabase = await createServiceClient()

  // Load profile
  const { data: profile } = await supabase
    .from('profiles')
    .select('*')
    .eq('id', profileId)
    .single()

  if (!profile) {
    return NextResponse.json({ error: 'Profile not found' }, { status: 404 })
  }

  // Check credits (skip for unlimited plans)
  if (profile.plan !== 'elite' && profile.plan !== 'enterprise') {
    const hasCredits = await deductCredits(profileId, 'text_message', conversationId)
    if (!hasCredits) {
      return NextResponse.json({ error: 'Insufficient credits' }, { status: 402 })
    }
  }

  // Detect language from last user message
  const lastUserMessage = [...messages].reverse().find((m: Message) => m.role === 'user')
  const langContext = await detectLanguage(
    lastUserMessage?.content || '',
    currentLanguage,
    profile.language_mode,
    profile.default_language
  )

  // Get visitor memory if returning visitor
  let visitorMemory: string | undefined
  if (visitorId) {
    const memory = await getVisitorMemory(visitorId, profileId)
    if (memory?.memory_summary) visitorMemory = memory.memory_summary
  }

  const systemPrompt = buildSystemPrompt(
    profile,
    langContext.detectedLanguage,
    langContext.detectedDialect,
    visitorMemory,
    visitorName
  )

  // Stream response
  const stream = await claude.messages.stream({
    model: CLAUDE_MODEL,
    max_tokens: 600,
    system: systemPrompt,
    messages: messages.map((m: Message) => ({
      role: m.role,
      content: m.content,
    })),
  })

  const encoder = new TextEncoder()

  const readable = new ReadableStream({
    async start(controller) {
      // Send language context first
      controller.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({ type: 'language', language: langContext.detectedLanguage, dialect: langContext.detectedDialect, switched: langContext.shouldSwitch })}\n\n`
        )
      )

      for await (const chunk of stream) {
        if (
          chunk.type === 'content_block_delta' &&
          chunk.delta.type === 'text_delta'
        ) {
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify({ type: 'text', text: chunk.delta.text })}\n\n`)
          )
        }
      }

      controller.enqueue(encoder.encode('data: [DONE]\n\n'))
      controller.close()
    },
  })

  return new Response(readable, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    },
  })
}
