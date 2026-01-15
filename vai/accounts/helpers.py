from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from urllib.parse import urljoin

from .tokens import email_verification_token
from vai.billing.services import stripe as stripe_svc
from vai.billing.services.stripe import _create_local_trial_subscription

User = get_user_model()


def assign_free_trial(user):
    """
    Assigns free trial subscriptions (Support Agent and Outbound Calling)
    to a newly registered user if they don't already have them.
    """
    from vai.billing.models import (
        Subscription as BillingSubscription,
        SupportAgentPlan,
        OutboundCallingPlan,
    )
    ct_sa = ContentType.objects.get_for_model(SupportAgentPlan)
    ct_oc = ContentType.objects.get_for_model(OutboundCallingPlan)
    has_sa = BillingSubscription.objects.filter(user=user, plan_content_type=ct_sa).exclude(status__in=["canceled", "ended"]).exists()
    has_oc = BillingSubscription.objects.filter(user=user, plan_content_type=ct_oc).exclude(status__in=["canceled", "ended"]).exists()
    sa = SupportAgentPlan.objects.filter(is_active=True, is_trial=True).order_by("-id").first()
    oc = OutboundCallingPlan.objects.filter(is_active=True, is_trial=True).order_by("-id").first()
    if sa and not has_sa:
        try:
            stripe_svc.create_subscription(user, sa, payment_method=None, trial_days=sa.trial_period_days or None, cancel_at_period_end=True)
        except Exception:
            _create_local_trial_subscription(user, sa)
    if oc and not has_oc:
        try:
            stripe_svc.create_subscription(user, oc, payment_method=None, trial_days=oc.trial_period_days or None, cancel_at_period_end=True)
        except Exception:
            _create_local_trial_subscription(user, oc)


def send_verification_email(user):
    """
    Sends an email verification link to the user's email address.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token_for_email = email_verification_token.make_token(user)
    path = f"/verify-email/{uid}/{token_for_email}"
    verification_link = urljoin(settings.FRONTEND_DOMAIN, path)

    context = {
        "user": user,
        "verification_link": verification_link,
        "current_year": timezone.now().year,
    }

    subject = "Verify your email"
    body = render_to_string("accounts/email/verification_email.html", context)

    send_mail(
        subject,
        None,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
        html_message=body,
    )