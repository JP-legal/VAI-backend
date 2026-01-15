from django.contrib import admin
from .models import SupportAgentPlan, OutboundCallingPlan, BundlePlan, Subscription, UsageBucket, UsageEvent
from .services.stripe import ensure_product_and_price, STRIPE_API_KEY


@admin.register(SupportAgentPlan, OutboundCallingPlan, BundlePlan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "currency", "billing_interval", "is_trial", "is_active", "stripe_product_id", "stripe_price_id")
    list_filter = ("is_active", "is_trial", "billing_interval", "currency")
    search_fields = ("name", "stripe_product_id", "stripe_price_id")
    readonly_fields = ("stripe_product_id", "stripe_price_id")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Auto-create Product/Price in Stripe if configured
        if obj.auto_sync_to_stripe and STRIPE_API_KEY:
            try:
                ensure_product_and_price(obj)
            except Exception as e:
                # Surface a readable admin warning — but don't block saving
                self.message_user(request, f"Stripe sync skipped/failed: {e}", level="WARNING")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "current_period_start", "current_period_end", "stripe_subscription_id")
    list_filter = ("status",)
    search_fields = ("user__email", "stripe_subscription_id")


@admin.register(UsageBucket)
class UsageBucketAdmin(admin.ModelAdmin):
    list_display = ("subscription", "component", "period_start", "period_end", "seconds_included", "seconds_used", "unlimited")


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = ("subscription", "component", "seconds", "at_time")
    list_filter = ("component",)
    search_fields = ("subscription__user__email",)
