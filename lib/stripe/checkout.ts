import Stripe from 'stripe'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!, {
    apiVersion: '2026-02-25.clover',
  })
}

export const stripe = new Proxy({} as Stripe, {
  get(_target, prop) {
    return getStripe()[prop as keyof Stripe]
  },
})

export const STRIPE_PRICES: Record<string, string> = {
  personal: process.env.STRIPE_PRICE_PERSONAL || '',
  pro: process.env.STRIPE_PRICE_PRO || '',
  elite: process.env.STRIPE_PRICE_ELITE || '',
  startup: process.env.STRIPE_PRICE_STARTUP || '',
  business: process.env.STRIPE_PRICE_BUSINESS || '',
  enterprise: process.env.STRIPE_PRICE_ENTERPRISE || '',
}

export async function createCheckoutSession(
  profileId: string,
  userId: string,
  plan: string,
  successUrl: string,
  cancelUrl: string
): Promise<string> {
  const priceId = STRIPE_PRICES[plan]
  if (!priceId) throw new Error(`Unknown plan: ${plan}`)

  const session = await stripe.checkout.sessions.create({
    mode: 'subscription',
    payment_method_types: ['card'],
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: successUrl,
    cancel_url: cancelUrl,
    metadata: { profileId, userId, plan },
  })

  return session.url!
}

export async function createCreditTopupSession(
  profileId: string,
  userId: string,
  credits: number,
  priceId: string,
  successUrl: string,
  cancelUrl: string
): Promise<string> {
  const session = await stripe.checkout.sessions.create({
    mode: 'payment',
    payment_method_types: ['card'],
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: successUrl,
    cancel_url: cancelUrl,
    metadata: { profileId, userId, credits: credits.toString(), type: 'topup' },
  })

  return session.url!
}
