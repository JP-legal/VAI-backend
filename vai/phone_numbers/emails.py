from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives, send_mail
from django.utils import timezone
from django.utils.timezone import localtime
from django.conf import settings

DEFAULT_FROM = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@vai.ai")


def _frontend_url(path: str):
    base = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    path = path or ""
    if path.startswith("/"):
        return f"{base}{path}"
    return f"{base}/{path}" if base else path


def send_request_received_email(user, instance):
    """
    Sent right after user creates a request (pending).
    """
    ctx = {
        "user_name": getattr(user, "username", "") or getattr(user, "first_name", "") or user.email,
        "country": instance.country,
        "request_id": str(instance.public_id),
        "created_at": localtime(instance.created_at).strftime("%Y-%m-%d %H:%M"),
        "current_year": timezone.now().year,
    }
    subject = "We received your phone number request"
    html_body = render_to_string("emails/phone_number_request_received.html", ctx)
    text_body = (
        f"Hi {ctx['user_name']},\n\n"
        f"Thanks for your request for a phone number in {ctx['country']}. Our team is reviewing it now.\n\n"
        f"Request ID: {ctx['request_id']}\n"
        f"Requested on: {ctx['created_at']}\n\n"
        "What’s next?\n"
        f"• We’ll verify availability for {ctx['country']}\n"
        "• You’ll get an email once it’s approved or if we need more info\n\n"
        "– The V-AI Team"
    )
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=DEFAULT_FROM,
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=True)


def send_request_rejected_email(user, request_obj):
    subject = "Your phone number request was not approved"
    support_url = _frontend_url("/numbers/requests")
    body = (
        f"Hi {getattr(user, 'username', '') or getattr(user, 'first_name', '') or 'there'},\n\n"
        f"We’re sorry—your request (#{request_obj.public_id}) for a number in {request_obj.country} wasn’t approved.\n\n"
        f"Reason:\n{request_obj.rejection_reason or '—'}\n\n"
        f"You can submit a new request or choose a different country here:\n{support_url}\n\n"
        f"If you believe this is an error, reply to this email and we’ll help.\n\n– The V-AI Team"
    )
    send_mail(subject, body, DEFAULT_FROM, [user.email], fail_silently=True)


def send_request_approved_email(user, request_obj):
    """
    APPROVED (no number yet). Tells the user the request is approved and they'll be assigned
    a number soon (they'll get another email when the number is actually assigned).
    """
    manage_numbers_url = _frontend_url("/numbers")
    body = (
        f"Hi {getattr(user, 'username', '') or getattr(user, 'first_name', '') or 'there'},\n\n"
        f"Good news—your request (#{request_obj.public_id}) for a number in {request_obj.country} is approved.\n\n"
        f"We’ll assign an available number to your account shortly and notify you by email.\n"
        f"You’ll be able to manage your numbers here:\n{manage_numbers_url}\n\n"
        f"If you didn’t make this request, reply to this email immediately.\n\n– The V-AI Team"
    )
    send_mail("Your phone number request is approved", body, DEFAULT_FROM, [user.email], fail_silently=True)


def send_number_assigned_email(user, phone_obj):
    subject = "A phone number was assigned to you"
    manage_numbers_url = _frontend_url("/numbers")
    body = (
        f"Hi {getattr(user, 'username', '') or getattr(user, 'first_name', '') or 'there'},\n\n"
        f"You have been assigned the phone number {phone_obj.number} ({phone_obj.country}).\n\n"
        f"Manage it here:\n{manage_numbers_url}\n\n– The V-AI Team"
    )
    send_mail(subject, body, DEFAULT_FROM, [user.email], fail_silently=True)
