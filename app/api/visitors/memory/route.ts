import { NextRequest, NextResponse } from 'next/server'
import { getVisitorMemory } from '@/lib/memory/manager'

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const visitorId = searchParams.get('visitorId')
  const profileId = searchParams.get('profileId')

  if (!visitorId || !profileId) {
    return NextResponse.json({ error: 'Missing visitorId or profileId' }, { status: 400 })
  }

  const memory = await getVisitorMemory(visitorId, profileId)
  return NextResponse.json({ memory })
}
