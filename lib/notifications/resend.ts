import { Resend } from 'resend'

function getResend() {
  return new Resend(process.env.RESEND_API_KEY)
}

export async function sendLeadNotification(
  toEmail: string,
  profileName: string,
  visitorName: string,
  visitorEmail: string,
  summary: string,
  conversationId: string
): Promise<void> {
  await getResend().emails.send({
    from: 'AURA <notifications@yourdomain.com>',
    to: toEmail,
    subject: `New lead: ${visitorName} wants to connect with you`,
    html: `
      <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>New lead from your AURA profile</h2>
        <p><strong>${visitorName}</strong> (${visitorEmail}) just had a conversation with your AI assistant.</p>
        <blockquote style="background: #f5f5f5; padding: 12px; border-left: 4px solid #6366f1;">
          ${summary}
        </blockquote>
        <a href="${process.env.NEXT_PUBLIC_APP_URL}/dashboard/conversations/${conversationId}"
           style="background: #6366f1; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; margin-top: 16px;">
          View Full Conversation
        </a>
      </div>
    `,
  })
}

export async function sendNewMessageNotification(
  toEmail: string,
  profileName: string,
  visitorName: string,
  messagePreview: string
): Promise<void> {
  await getResend().emails.send({
    from: 'AURA <notifications@yourdomain.com>',
    to: toEmail,
    subject: `${visitorName} is chatting with your AI`,
    html: `
      <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Someone is talking to your AI</h2>
        <p><strong>${visitorName}</strong> said:</p>
        <blockquote style="background: #f5f5f5; padding: 12px; border-left: 4px solid #6366f1;">
          ${messagePreview}
        </blockquote>
        <a href="${process.env.NEXT_PUBLIC_APP_URL}/dashboard"
           style="background: #6366f1; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; margin-top: 16px;">
          Go to Dashboard
        </a>
      </div>
    `,
  })
}
