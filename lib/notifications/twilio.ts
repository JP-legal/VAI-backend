import twilio from 'twilio'

export async function sendWhatsAppLeadNotification(
  toNumber: string,
  profileName: string,
  visitorName: string,
  summary: string
): Promise<void> {
  const client = twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN)
  const from = `whatsapp:${process.env.TWILIO_WHATSAPP_NUMBER}`
  const to = `whatsapp:${toNumber.startsWith('+') ? toNumber : '+' + toNumber}`

  await client.messages.create({
    from,
    to,
    body: `*New Lead — AURA*\n\n👤 *${visitorName}* just spoke with your AI assistant.\n\n📝 Summary: ${summary}\n\nView at: ${process.env.NEXT_PUBLIC_APP_URL}/dashboard`,
  })
}
