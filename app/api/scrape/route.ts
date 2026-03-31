import { NextRequest, NextResponse } from 'next/server'
import { enrichProfile } from '@/lib/scraping/pipeline'
import { createClient } from '@/lib/supabase/server'

export async function POST(req: NextRequest) {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { name, linkedinUrl, websiteUrl, company, profileId } = await req.json()
  if (!name) return NextResponse.json({ error: 'Name required' }, { status: 400 })

  try {
    const enriched = await enrichProfile(name, linkedinUrl, websiteUrl, company)

    if (profileId) {
      await supabase
        .from('profiles')
        .update({
          title: enriched.title,
          company: enriched.company,
          bio: enriched.bio,
          ai_persona: enriched.ai_persona,
          ai_context: enriched.ai_context,
          scraped_data: enriched.scraped_data,
        })
        .eq('id', profileId)
        .eq('user_id', user.id)
    }

    return NextResponse.json({ success: true, data: enriched })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Scraping failed' },
      { status: 500 }
    )
  }
}
