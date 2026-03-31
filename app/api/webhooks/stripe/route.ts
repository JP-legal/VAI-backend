import { NextRequest, NextResponse } from 'next/server'
import { stripe } from '@/lib/stripe/checkout'
import { createServiceClient } from '@/lib/supabase/server'
import { PLAN_CREDITS } from '@/lib/stripe/credits'
import Stripe from 'stripe'

export async function POST(req: NextRequest) {
  const body = await req.text()
  const signature = req.headers.get('stripe-signature')!

  let event: Stripe.Event

  try {
    event = stripe.webhooks.constructEvent(body, signature, process.env.STRIPE_WEBHOOK_SECRET!)
  } catch (err) {
    return NextResponse.json({ error: 'Invalid signature' }, { status: 400 })
  }

  const supabase = await createServiceClient()

  if (event.type === 'checkout.session.completed') {
    const session = event.data.object as Stripe.Checkout.Session
    const { profileId, plan, type, credits } = session.metadata || {}

    if (type === 'topup' && profileId && credits) {
      // Credit top-up
      const creditAmount = parseInt(credits)
      await supabase.rpc('add_credits', { p_profile_id: profileId, p_amount: creditAmount })
      await supabase.from('credit_transactions').insert({
        profile_id: profileId,
        amount: creditAmount,
        type: 'credit',
        reason: 'stripe_topup',
      })
    } else if (plan && profileId) {
      // Plan subscription
      const planCredits = PLAN_CREDITS[plan] || 0
      await supabase
        .from('profiles')
        .update({
          plan,
          credits: planCredits === -1 ? 999999 : planCredits,
        })
        .eq('id', profileId)
    }
  }

  if (event.type === 'customer.subscription.deleted') {
    const subscription = event.data.object as Stripe.Subscription
    const profileId = subscription.metadata?.profileId
    if (profileId) {
      await supabase
        .from('profiles')
        .update({ plan: 'free', credits: PLAN_CREDITS.free })
        .eq('id', profileId)
    }
  }

  return NextResponse.json({ received: true })
}
