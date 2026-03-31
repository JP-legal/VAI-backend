import { createServiceClient } from '@/lib/supabase/server'
import { Visitor } from '@/lib/supabase/types'

export async function identifyVisitor(
  anonymousId: string,
  ipAddress?: string
): Promise<Visitor> {
  const supabase = await createServiceClient()

  // Try to find existing visitor
  const { data: existing } = await supabase
    .from('visitors')
    .select('*')
    .eq('anonymous_id', anonymousId)
    .single()

  if (existing) {
    await supabase
      .from('visitors')
      .update({ last_seen_at: new Date().toISOString() })
      .eq('id', existing.id)
    return existing as Visitor
  }

  // Get geolocation from IP
  let country_code = ''
  let city = ''
  if (ipAddress && ipAddress !== '127.0.0.1' && ipAddress !== '::1') {
    try {
      const geo = await fetch(
        `https://api.ipapi.com/api/${ipAddress}?access_key=${process.env.IPAPI_KEY}&fields=country_code,city`
      )
      if (geo.ok) {
        const data = await geo.json()
        country_code = data.country_code || ''
        city = data.city || ''
      }
    } catch {}
  }

  const { data: newVisitor } = await supabase
    .from('visitors')
    .insert({
      anonymous_id: anonymousId,
      country_code,
      city,
      first_seen_at: new Date().toISOString(),
      last_seen_at: new Date().toISOString(),
    })
    .select()
    .single()

  return newVisitor as Visitor
}

export async function updateVisitorIdentity(
  visitorId: string,
  updates: Partial<Pick<Visitor, 'name' | 'email' | 'phone'>>
): Promise<void> {
  const supabase = await createServiceClient()
  await supabase.from('visitors').update(updates).eq('id', visitorId)
}

export async function incrementConversationCount(visitorId: string): Promise<void> {
  const supabase = await createServiceClient()
  await supabase.rpc('increment', { table: 'visitors', column: 'total_conversations', id: visitorId })
}
