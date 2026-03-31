import { claude, CLAUDE_MODEL } from '@/lib/ai/claude'
import { scrapeLinkedIn, scrapeWebsite } from './firecrawl'
import { searchPerson, extractSearchContext } from './serper'

export interface EnrichmentResult {
  name: string
  title?: string
  company?: string
  bio?: string
  ai_persona?: string
  ai_context?: string
  scraped_data: Record<string, unknown>
  avatar_url?: string
}

export async function enrichProfile(
  name: string,
  linkedinUrl?: string,
  websiteUrl?: string,
  company?: string
): Promise<EnrichmentResult> {
  const [linkedinContent, websiteContent, searchResults] = await Promise.all([
    linkedinUrl ? scrapeLinkedIn(linkedinUrl) : Promise.resolve(''),
    websiteUrl ? scrapeWebsite(websiteUrl) : Promise.resolve(''),
    searchPerson(name, company),
  ])

  const searchContext = await extractSearchContext(searchResults)

  const combined = [
    linkedinContent && `LinkedIn:\n${linkedinContent}`,
    websiteContent && `Website:\n${websiteContent}`,
    searchContext && `Search results:\n${searchContext}`,
  ]
    .filter(Boolean)
    .join('\n\n---\n\n')

  if (!combined) {
    return {
      name,
      company,
      scraped_data: {},
    }
  }

  const response = await claude.messages.create({
    model: CLAUDE_MODEL,
    max_tokens: 1000,
    messages: [
      {
        role: 'user',
        content: `Extract structured profile information from these sources. Return JSON only:
{
  "title": "Job title",
  "company": "Company name",
  "bio": "2-3 sentence professional bio",
  "expertise": ["skill1", "skill2"],
  "services": ["service1", "service2"],
  "achievements": ["achievement1"],
  "industries": ["industry1"],
  "ai_persona": "Instructions for the AI: how it should represent this person, their communication style, key talking points",
  "ai_context": "Additional context the AI should know: specific offerings, pricing hints, target clients"
}

Sources:
${combined}`,
      },
    ],
  })

  const text = response.content[0].type === 'text' ? response.content[0].text : ''
  let parsed: Record<string, unknown> = {}

  try {
    const match = text.match(/\{[\s\S]*\}/)
    if (match) parsed = JSON.parse(match[0])
  } catch {
    parsed = {}
  }

  return {
    name,
    title: (parsed.title as string) || undefined,
    company: (parsed.company as string) || company,
    bio: (parsed.bio as string) || undefined,
    ai_persona: (parsed.ai_persona as string) || undefined,
    ai_context: (parsed.ai_context as string) || undefined,
    scraped_data: {
      expertise: parsed.expertise || [],
      services: parsed.services || [],
      achievements: parsed.achievements || [],
      industries: parsed.industries || [],
      raw_linkedin: linkedinContent.slice(0, 1000),
    },
  }
}
