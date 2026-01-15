from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from vai.billing.services import stripe as stripe_svc

User = get_user_model()

@receiver(post_save, sender=User)
def ensure_stripe_customer(sender, instance: User, created, **kwargs):
    if created and not instance.stripe_customer_id:
        try:
            stripe_svc.get_or_create_customer(instance)
        except Exception:
            pass