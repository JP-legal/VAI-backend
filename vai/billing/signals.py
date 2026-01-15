from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from vai.billing.models import BundlePlan, OutboundCallingPlan, SupportAgentPlan
from vai.billing.services.stripe import ensure_product_and_price, STRIPE_API_KEY


@receiver(post_save, sender=SupportAgentPlan)
@receiver(post_save, sender=OutboundCallingPlan)
@receiver(post_save, sender=BundlePlan)
def auto_sync_stripe_artifacts(sender, instance, created, **kwargs):
    if not instance.auto_sync_to_stripe or not STRIPE_API_KEY:
        return

    update_fields = kwargs.get("update_fields")
    if update_fields and set(update_fields) <= {"stripe_product_id", "stripe_price_id"}:
        return

    def _do_sync():
        try:
            ensure_product_and_price(instance)
        except Exception  as e:
            print(e)

    transaction.on_commit(_do_sync)