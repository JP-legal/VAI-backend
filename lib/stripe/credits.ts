import { createServiceClient } from '@/lib/supabase/server'
import { CREDIT_RATES } from '@/lib/supabase/types'

export async function deductCredits(
  profileId: string,
  type: keyof typeof CREDIT_RATES,
  conversationId?: string
): Promise<boolean> {
  const amount = CREDIT_RATES[type]
  const supabase = await createServiceClient()

  const { data } = await supabase.rpc('deduct_credits', {
    p_profile_id: profileId,
    p_amount: amount,
    p_reason: type,
    p_conversation_id: conversationId || null,
  })

  return data === true
}

export async function addCredits(
  profileId: string,
  amount: number,
  reason: string
): Promise<void> {
  const supabase = await createServiceClient()
  await supabase.from('profiles').update({ credits: supabase.rpc('credits + ' + amount) }).eq('id', profileId)
  await supabase.from('credit_transactions').insert({
    profile_id: profileId,
    amount,
    type: 'credit',
    reason,
  })
}

export async function getCredits(profileId: string): Promise<number> {
  const supabase = await createServiceClient()
  const { data } = await supabase.from('profiles').select('credits').eq('id', profileId).single()
  return data?.credits || 0
}

export const PLAN_CREDITS: Record<string, number> = {
  free: 500,
  personal: 5000,
  pro: 15000,
  elite: -1, // unlimited
  startup: 20000,
  business: 80000,
  enterprise: -1, // unlimited
}
