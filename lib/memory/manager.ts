import { claude, CLAUDE_MODEL } from '@/lib/ai/claude'
import { createServiceClient } from '@/lib/supabase/server'
import { Conversation, VisitorMemory } from '@/lib/supabase/types'

export async function getVisitorMemory(
  visitorId: string,
  profileId: string
): Promise<VisitorMemory | null> {
  const supabase = await createServiceClient()
  const { data } = await supabase
    .from('visitor_memory')
    .select('*')
    .eq('visitor_id', visitorId)
    .eq('profile_id', profileId)
    .single()
  return data
}

export async function consolidateMemory(
  visitorId: string,
  profileId: string,
  conversation: Conversation
): Promise<void> {
  const existing = await getVisitorMemory(visitorId, profileId)

  const summaryResponse = await claude.messages.create({
    model: CLAUDE_MODEL,
    max_tokens: 500,
    messages: [
      {
        role: 'user',
        content: `Summarize this conversation in 2-3 sentences, extracting:
- Who the visitor is (name, company, role if mentioned)
- What they were interested in
- Any commitments made or follow-ups needed
- Overall sentiment

Conversation: ${JSON.stringify(conversation.messages.slice(-20))}

Return JSON only: { "summary": "...", "visitor_facts": {}, "follow_up_needed": true/false, "sentiment": "positive/neutral/negative" }`,
      },
    ],
  })

  const summaryText =
    summaryResponse.content[0].type === 'text' ? summaryResponse.content[0].text : ''
  let newSummaryData: { summary: string; visitor_facts: Record<string, unknown> } = {
    summary: '',
    visitor_facts: {},
  }

  try {
    const match = summaryText.match(/\{[\s\S]*\}/)
    if (match) newSummaryData = JSON.parse(match[0])
  } catch {
    newSummaryData.summary = summaryText
  }

  let finalMemory = newSummaryData.summary
  let mergedFacts = newSummaryData.visitor_facts

  if (existing?.memory_summary) {
    const mergeResponse = await claude.messages.create({
      model: CLAUDE_MODEL,
      max_tokens: 400,
      messages: [
        {
          role: 'user',
          content: `Merge these two memory summaries into one concise updated memory. Keep the most important facts. Max 200 words.

Previous: ${existing.memory_summary}
New: ${newSummaryData.summary}

Return JSON only: { "merged_summary": "...", "merged_facts": {} }`,
        },
      ],
    })

    const mergeText =
      mergeResponse.content[0].type === 'text' ? mergeResponse.content[0].text : ''
    try {
      const match = mergeText.match(/\{[\s\S]*\}/)
      if (match) {
        const merged = JSON.parse(match[0])
        finalMemory = merged.merged_summary || finalMemory
        mergedFacts = { ...(existing.key_facts || {}), ...(merged.merged_facts || mergedFacts) }
      }
    } catch {
      // keep newSummaryData values
    }
  }

  const supabase = await createServiceClient()
  await supabase.from('visitor_memory').upsert(
    {
      visitor_id: visitorId,
      profile_id: profileId,
      memory_summary: finalMemory,
      key_facts: mergedFacts,
      conversation_count: (existing?.conversation_count || 0) + 1,
      last_updated: new Date().toISOString(),
    },
    { onConflict: 'visitor_id,profile_id' }
  )
}

export async function generateConversationSummary(
  messages: Conversation['messages']
): Promise<{ summary: string; topics: string[] }> {
  const response = await claude.messages.create({
    model: CLAUDE_MODEL,
    max_tokens: 300,
    messages: [
      {
        role: 'user',
        content: `Summarize this conversation and extract main topics. Return JSON only:
{ "summary": "one sentence summary", "topics": ["topic1", "topic2"] }

Conversation: ${JSON.stringify(messages.slice(-20))}`,
      },
    ],
  })

  const text = response.content[0].type === 'text' ? response.content[0].text : ''
  try {
    const match = text.match(/\{[\s\S]*\}/)
    if (match) return JSON.parse(match[0])
  } catch {}
  return { summary: text, topics: [] }
}
