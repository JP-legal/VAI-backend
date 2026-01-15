from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import F
from django.utils import timezone
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

from .choices import INTERVAL_CHOICES, SUBSCRIPTION_STATUS, COMPONENT_CHOICES, TRANSACTION_KIND, TRANSACTION_STATUS
from .utils import minutes_to_seconds, seconds_to_billable_minutes, utcnow


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True


class AbstractPlan(TimeStampedModel):
    """
    Base for all plan-like objects (SupportAgentPlan, OutboundCallingPlan, BundlePlan).
    Free trials are first-class plans too (set is_trial=True) and are fully editable.
    """
    name = models.CharField(max_length=150)
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Recurring price in currency units (e.g. USD). Use 0.00 for free trials.")
    currency = models.CharField(max_length=10, default="usd")
    billing_interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, default="month")

    is_active = models.BooleanField(default=True)
    is_trial = models.BooleanField(default=False, help_text="Mark as a trial plan (price often 0).")
    trial_period_days = models.PositiveIntegerField(default=0, help_text="Optional: use to set trial period on subscription creation.")

    # Stripe linkage
    stripe_product_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    stripe_price_id = models.CharField(max_length=64, blank=True, null=True, unique=True)

    # If True, Plan admin.save() will try to create Product/Price in Stripe when missing
    auto_sync_to_stripe = models.BooleanField(default=True)

    class Meta:
        abstract = True

    def __str__(self):
        label = f"{self.name} ({'trial' if self.is_trial else self.billing_interval})"
        return label

    # --- Stripe-friendly helpers ---
    @property
    def unit_amount_cents(self) -> int:
        # Stripe expects integer amount in the smallest currency unit
        return int(Decimal(self.price) * 100)

    # --- Contract: concrete plans must implement component declarations ---
    def components(self):
        """
        Return a dict keyed by 'support_agent' and/or 'outbound_calling' describing
        allowance, overage, and feature toggles. Example shape:

        {
          "support_agent": {
              "seconds_included": 1800,
              "unlimited": False,
              "extra_per_minute": Decimal("0.15"),
              "customizations_enabled": True,
          },
          "outbound_calling": {
              "seconds_included": 3600,
              "unlimited": False,
              "extra_per_minute": Decimal("0.10"),
              "can_use_vai_database": True,
          }
        }
        """
        raise NotImplementedError("Concrete plan must override components()")


# ---------------------------
# Concrete Plans (editable)
# ---------------------------

class SupportAgentPlan(AbstractPlan):
    # Support Agent specific
    minutes = models.PositiveIntegerField(default=0)
    unlimited_minutes = models.BooleanField(default=False)
    extra_per_minute = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))
    customizations_enabled = models.BooleanField(default=False)

    def components(self):
        return {
            "support_agent": {
                "seconds_included": 0 if self.unlimited_minutes else minutes_to_seconds(self.minutes),
                "unlimited": self.unlimited_minutes,
                "extra_per_minute": self.extra_per_minute,
                "customizations_enabled": self.customizations_enabled,
            }
        }


class OutboundCallingPlan(AbstractPlan):
    # Outbound Calling specific
    minutes = models.PositiveIntegerField(default=0)
    extra_per_minute = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))
    can_use_vai_database = models.BooleanField(default=False)

    def components(self):
        return {
            "outbound_calling": {
                "seconds_included": minutes_to_seconds(self.minutes),
                "unlimited": False,
                "extra_per_minute": self.extra_per_minute,
                "can_use_vai_database": self.can_use_vai_database,
            }
        }


class BundlePlan(AbstractPlan):
    # Support Agent portion
    sa_minutes = models.PositiveIntegerField(default=0)
    sa_unlimited_minutes = models.BooleanField(default=False)
    sa_extra_per_minute = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))
    sa_customizations_enabled = models.BooleanField(default=False)

    # Outbound Calling portion
    oc_minutes = models.PositiveIntegerField(default=0)
    oc_extra_per_minute = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))
    oc_can_use_vai_database = models.BooleanField(default=False)

    def components(self):
        return {
            "support_agent": {
                "seconds_included": 0 if self.sa_unlimited_minutes else minutes_to_seconds(self.sa_minutes),
                "unlimited": self.sa_unlimited_minutes,
                "extra_per_minute": self.sa_extra_per_minute,
                "customizations_enabled": self.sa_customizations_enabled,
            },
            "outbound_calling": {
                "seconds_included": minutes_to_seconds(self.oc_minutes),
                "unlimited": False,
                "extra_per_minute": self.oc_extra_per_minute,
                "can_use_vai_database": self.oc_can_use_vai_database,
            },
        }


# ---------------------------------
# Subscription + Usage (seconds)
# ---------------------------------

class Subscription(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")

    plan_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    plan_object_id = models.PositiveIntegerField()
    plan = GenericForeignKey("plan_content_type", "plan_object_id")

    stripe_subscription_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    stripe_subscription_item_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    latest_invoice_id = models.CharField(max_length=64, blank=True, null=True)

    status = models.CharField(max_length=32, choices=SUBSCRIPTION_STATUS, default="incomplete")
    started_at = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateTimeField(blank=True, null=True)
    current_period_end = models.DateTimeField(blank=True, null=True)
    # ↓↓↓ Auto‑renew by default
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(blank=True, null=True)
    ended_at = models.DateTimeField(blank=True, null=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["plan_content_type", "plan_object_id"]),
        ]

    def __str__(self):
        return f"{self.user.email} -> {self.plan} [{self.status}]"

    # ---- Usage lifecycle ----
    def initialize_or_rollover_usage_buckets(self):
        """
        Ensure usage buckets exist for the current period; if the subscription
        has entered a new period, create new buckets with fresh allocations.
        """
        if not self.current_period_start or not self.current_period_end:
            # If Stripe hasn't populated these yet, initialize a "provisional" bucket window
            now = utcnow()
            self.current_period_start = self.current_period_start or now
            self.current_period_end = self.current_period_end or now

        existing = {ub.component for ub in self.usage_buckets.active()}
        for component, cfg in self.plan.components().items():
            if component not in existing:
                UsageBucket.objects.create(
                    subscription=self,
                    component=component,
                    period_start=self.current_period_start,
                    period_end=self.current_period_end,
                    seconds_included=cfg["seconds_included"],
                    unlimited=cfg.get("unlimited", False),
                    extra_per_minute=cfg.get("extra_per_minute", Decimal("0.00")),
                )

    def get_active_bucket(self, component: str):
        return self.usage_buckets.active().filter(component=component).first()

    def record_usage_seconds(self, component: str, seconds: int, at_time=None):
        """
        Increments usage in seconds and adds a UsageEvent for audit.
        """
        self.initialize_or_rollover_usage_buckets()
        bucket = self.get_active_bucket(component)
        if bucket is None:
            raise ValueError(f"No active usage bucket for component '{component}'")

        # Increment usage
        bucket.seconds_used = F("seconds_used") + int(seconds)
        bucket.save(update_fields=["seconds_used"])
        bucket.refresh_from_db()

        UsageEvent.objects.create(
            subscription=self,
            component=component,
            seconds=int(seconds),
            at_time=at_time or timezone.now(),
        )

    def component_remaining_seconds(self, component: str) -> int:
        bucket = self.get_active_bucket(component)
        if not bucket:
            return 0
        if bucket.unlimited:
            return 10**12  # practically infinite
        return max(0, bucket.seconds_included - bucket.seconds_used)

    def component_overage_seconds(self, component: str) -> int:
        bucket = self.get_active_bucket(component)
        if not bucket or bucket.unlimited:
            return 0
        return max(0, bucket.seconds_used - bucket.seconds_included)

    def total_overage_cost(self) -> Decimal:
        """
        Sum over both components: (ceil(overage_seconds/60) * extra_per_minute)
        """
        total = Decimal("0.00")
        for bucket in self.usage_buckets.active().all():
            if bucket.unlimited:
                continue
            over_s = max(0, bucket.seconds_used - bucket.seconds_included)
            if over_s > 0:
                minutes_to_bill = seconds_to_billable_minutes(over_s)
                total += Decimal(minutes_to_bill) * bucket.extra_per_minute
        return total


class UsageBucketQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(period_start__lte=now, period_end__gte=now)



class UsageBucket(TimeStampedModel):
    """
    One per (component, billing_period) per subscription.
    """
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="usage_buckets")
    component = models.CharField(max_length=32, choices=COMPONENT_CHOICES)

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    seconds_included = models.PositiveIntegerField(default=0)
    seconds_used = models.PositiveIntegerField(default=0)
    unlimited = models.BooleanField(default=False)
    extra_per_minute = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))

    objects = UsageBucketQuerySet.as_manager()

    class Meta:
        unique_together = [("subscription", "component", "period_start", "period_end")]
        indexes = [
            models.Index(fields=["subscription", "component"]),
        ]

    def __str__(self):
        return f"{self.subscription} :: {self.component} [{self.seconds_used}/{self.seconds_included}s]"


class UsageEvent(TimeStampedModel):
    """
    Audit log for raw usage increments (seconds).
    """
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="usage_events")
    component = models.CharField(max_length=32, choices=COMPONENT_CHOICES)
    seconds = models.PositiveIntegerField()
    at_time = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["subscription", "component", "at_time"]),
        ]
class PaymentMethod(TimeStampedModel):
    """
    Cached descriptor of a Stripe PaymentMethod (card). Source of truth is Stripe.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_methods")
    stripe_payment_method_id = models.CharField(max_length=64, unique=True)
    brand = models.CharField(max_length=32)
    last4 = models.CharField(max_length=4)
    exp_month = models.PositiveSmallIntegerField()
    exp_year = models.PositiveSmallIntegerField()
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "stripe_payment_method_id")]
        indexes = [
            models.Index(fields=["user", "is_default"]),
        ]

    def __str__(self):
        label = f"{self.brand} •••• {self.last4}"
        return f"{self.user.email} - {label}"

class BillingTransaction(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing_transactions")
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")

    # plan snapshot (so reports remain readable even if plan changes later)
    plan_content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    plan_object_id = models.PositiveIntegerField(null=True, blank=True)
    plan = GenericForeignKey("plan_content_type", "plan_object_id")
    plan_name = models.CharField(max_length=150, blank=True)  # denormalized label

    kind = models.CharField(max_length=32, choices=TRANSACTION_KIND)
    status = models.CharField(max_length=32, choices=TRANSACTION_STATUS, default="initiated")

    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=10, default="usd")

    # Stripe artifacts
    stripe_invoice_id = models.CharField(max_length=64, blank=True, null=True)
    stripe_invoice_url = models.URLField(blank=True, null=True)
    stripe_invoice_pdf = models.URLField(blank=True, null=True)
    stripe_payment_intent_id = models.CharField(max_length=64, blank=True, null=True)
    stripe_charge_id = models.CharField(max_length=64, blank=True, null=True)
    stripe_checkout_session_id = models.CharField(max_length=256, blank=True, null=True, unique=True)

    # failure/cancel info
    failure_code = models.CharField(max_length=128, blank=True)
    failure_message = models.TextField(blank=True)

    # misc
    description = models.CharField(max_length=255, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "kind", "status"]),
            models.Index(fields=["subscription", "kind"]),
        ]

    def __str__(self):
        return f"{self.user.email} {self.kind} {self.status} {self.amount}{self.currency}"