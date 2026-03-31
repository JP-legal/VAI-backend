const SERPER_BASE = 'https://google.serper.dev'

interface SerperResult {
  organic?: Array<{
    title: string
    link: string
    snippet: string
  }>
  knowledgeGraph?: {
    title?: string
    description?: string
    attributes?: Record<string, string>
  }
}

export async function searchPerson(name: string, company?: string): Promise<SerperResult> {
  const query = company ? `"${name}" "${company}"` : `"${name}" professional`

  const response = await fetch(`${SERPER_BASE}/search`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-KEY': process.env.SERPER_API_KEY!,
    },
    body: JSON.stringify({ q: query, num: 5 }),
  })

  if (!response.ok) return {}
  return response.json()
}

export async function extractSearchContext(results: SerperResult): Promise<string> {
  const snippets = results.organic?.map((r) => `${r.title}: ${r.snippet}`).join('\n') || ''
  const kg = results.knowledgeGraph?.description || ''
  return [kg, snippets].filter(Boolean).join('\n').slice(0, 2000)
}
