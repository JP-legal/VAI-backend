const FIRECRAWL_BASE = 'https://api.firecrawl.dev/v1'

interface ScrapeResult {
  success: boolean
  data?: {
    markdown?: string
    metadata?: {
      title?: string
      description?: string
      ogImage?: string
    }
  }
  error?: string
}

export async function scrapeUrl(url: string): Promise<ScrapeResult> {
  const response = await fetch(`${FIRECRAWL_BASE}/scrape`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${process.env.FIRECRAWL_API_KEY}`,
    },
    body: JSON.stringify({
      url,
      formats: ['markdown'],
      onlyMainContent: true,
    }),
  })

  if (!response.ok) {
    return { success: false, error: `Firecrawl error: ${response.status}` }
  }

  return response.json()
}

export async function scrapeLinkedIn(linkedinUrl: string): Promise<string> {
  const result = await scrapeUrl(linkedinUrl)
  if (!result.success || !result.data?.markdown) return ''
  return result.data.markdown.slice(0, 3000)
}

export async function scrapeWebsite(websiteUrl: string): Promise<string> {
  const result = await scrapeUrl(websiteUrl)
  if (!result.success || !result.data?.markdown) return ''
  return result.data.markdown.slice(0, 5000)
}
