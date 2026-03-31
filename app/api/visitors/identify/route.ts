import { NextRequest, NextResponse } from 'next/server'
import { identifyVisitor } from '@/lib/visitors/identify'

export async function POST(req: NextRequest) {
  const { anonymousId, browserLanguage } = await req.json()

  if (!anonymousId) {
    return NextResponse.json({ error: 'anonymousId required' }, { status: 400 })
  }

  const ip =
    req.headers.get('x-forwarded-for')?.split(',')[0] ||
    req.headers.get('x-real-ip') ||
    undefined

  try {
    const visitor = await identifyVisitor(anonymousId, ip)

    // Update browser language if provided
    if (browserLanguage && !visitor.browser_language) {
      const { createServiceClient } = await import('@/lib/supabase/server')
      const supabase = await createServiceClient()
      await supabase.from('visitors').update({ browser_language: browserLanguage }).eq('id', visitor.id)
    }

    return NextResponse.json({ visitor })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Identification failed' },
      { status: 500 }
    )
  }
}
