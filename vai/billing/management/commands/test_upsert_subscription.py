from django.core.management.base import BaseCommand
from django.utils import timezone
import json
import logging
import stripe

from vai.billing.services.stripe import normalize_subscription_with_periods

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--sub_id", required=True)

    def handle(self, *args, **options):
        sub_id = options["sub_id"]
        # data = stripe.Subscription.retrieve(sub_id, expand=["items.data.price.product", "customer", "items.data", "latest_invoice", "latest_invoice.lines.data"])
        try:
            # print('hi')
            normalize_subscription_with_periods(sub_id)
        except Exception:
            # print(str(normalize_subscription_with_periods(sub_id)))
            pass
