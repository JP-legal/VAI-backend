from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers
from decimal import Decimal

from .models import SupportAgentPlan, OutboundCallingPlan, Subscription, BundlePlan, PaymentMethod, BillingTransaction


class DecimalAsFloatField(serializers.FloatField):
    def to_representation(self, value):
        try:
            return float(Decimal(value))
        except Exception:
            return super().to_representation(value)


class SupportAgentTrialSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()
    extra_per_minute = DecimalAsFloatField()

    class Meta:
        model = SupportAgentPlan
        fields = (
            "id", "name", "price", "minutes", "unlimited_minutes", "extra_per_minute",
            "customizations_enabled", "is_active", "trial_period_days", "currency", "is_trial",
        )

    def validate(self, attrs):
        attrs["is_trial"] = True
        return attrs


class OutboundCallingTrialSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()
    extra_per_minute = DecimalAsFloatField()

    class Meta:
        model = OutboundCallingPlan
        fields = (
            "id", "name", "price", "minutes", "extra_per_minute",
            "can_use_vai_database", "is_active", "trial_period_days", "currency", "is_trial",
        )

    def validate(self, attrs):
        attrs["is_trial"] = True
        return attrs


class FreeTrialUserRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    userId = serializers.IntegerField()
    userName = serializers.CharField(allow_null=True)
    email = serializers.EmailField()
    planType = serializers.ChoiceField(choices=["support_agent", "outbound_calling"])
    planName = serializers.CharField()
    subDate = serializers.DateTimeField()
    currentPeriodEnd = serializers.DateTimeField()
    remainingSeconds = serializers.IntegerField()
    subscriptionId = serializers.IntegerField()

class BundlePlanAdminSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()
    sa_extra_per_minute = DecimalAsFloatField()
    oc_extra_per_minute = DecimalAsFloatField()

    class Meta:
        model = BundlePlan
        fields = (
            "id", "name", "price", "currency", "billing_interval",
            "is_active", "is_trial",
            # SA part
            "sa_minutes", "sa_unlimited_minutes", "sa_extra_per_minute", "sa_customizations_enabled",
            # OC part
            "oc_minutes", "oc_extra_per_minute", "oc_can_use_vai_database",
            # stripe links
            "stripe_product_id", "stripe_price_id",
        )
        read_only_fields = ("is_trial", "stripe_product_id", "stripe_price_id")


class SupportAgentPlanAdminSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()
    extra_per_minute = DecimalAsFloatField()

    class Meta:
        model = SupportAgentPlan
        fields = (
            "id", "name", "price", "currency", "billing_interval",
            "is_active", "is_trial", "trial_period_days",
            "minutes", "unlimited_minutes", "extra_per_minute", "customizations_enabled",
            "stripe_product_id", "stripe_price_id",
        )
        read_only_fields = ("is_trial", "stripe_product_id", "stripe_price_id")


class OutboundCallingPlanAdminSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()
    extra_per_minute = DecimalAsFloatField()

    class Meta:
        model = OutboundCallingPlan
        fields = (
            "id", "name", "price", "currency", "billing_interval",
            "is_active", "is_trial", "trial_period_days",
            "minutes", "extra_per_minute", "can_use_vai_database",
            "stripe_product_id", "stripe_price_id",
        )
        read_only_fields = ("is_trial", "stripe_product_id", "stripe_price_id")


# ---------- User rows for tables ----------

class UserRowSerializer(serializers.Serializer):
    """
    Common shape for SA/OC users lists
    """
    id = serializers.IntegerField()               # local row id == subscription pk
    userId = serializers.IntegerField()
    userName = serializers.CharField(allow_null=True)
    email = serializers.EmailField()
    planId = serializers.IntegerField()
    planName = serializers.CharField()
    renewalDate = serializers.DateTimeField()
    minutesRemaining = serializers.IntegerField()
    hoursRemaining = serializers.IntegerField()
    subscriptionId = serializers.IntegerField()


class BundleUserRowSerializer(serializers.Serializer):
    """
    Bundles: also include per-component remaining seconds (optional)
    """
    id = serializers.IntegerField()               # local row id == subscription pk
    userId = serializers.IntegerField()
    userName = serializers.CharField(allow_null=True)
    email = serializers.EmailField()
    planId = serializers.IntegerField()
    planName = serializers.CharField()
    renewalDate = serializers.DateTimeField()
    minutesRemaining = serializers.IntegerField()
    hoursRemaining = serializers.IntegerField()
    saRemainingSeconds = serializers.IntegerField()
    ocRemainingSeconds = serializers.IntegerField()
    subscriptionId = serializers.IntegerField()
class PaymentMethodSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="stripe_payment_method_id")

    class Meta:
        model = PaymentMethod
        fields = ("id", "brand", "last4", "exp_month", "exp_year", "is_default")
class SupportAgentPlanPublicSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()

    class Meta:
        model = SupportAgentPlan
        fields = ("id", "name", "price", "currency", "billing_interval",
                  "minutes", "unlimited_minutes", "extra_per_minute",
                  "customizations_enabled", "is_active")


class OutboundCallingPlanPublicSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()

    class Meta:
        model = OutboundCallingPlan
        fields = ("id", "name", "price", "currency", "billing_interval",
                  "minutes", "extra_per_minute", "can_use_vai_database", "is_active")


class MySubscriptionSerializer(serializers.Serializer):
    subscriptionId = serializers.IntegerField()
    component = serializers.ChoiceField(choices=["support_agent", "outbound_calling", "bundle"])
    planId = serializers.IntegerField()
    planName = serializers.CharField()
    status = serializers.CharField()
    billingInterval = serializers.CharField()
    currentPeriodEnd = serializers.DateTimeField(allow_null=True)
    cancelAtPeriodEnd = serializers.BooleanField()
    daysUntilRenewal = serializers.IntegerField()
    minutesRemaining = serializers.IntegerField()
    hoursRemaining = serializers.IntegerField()
    latestInvoiceUrl = serializers.CharField(allow_null=True)
    minutesIncluded = serializers.IntegerField()
    minutesUsed = serializers.IntegerField()
    unlimited = serializers.BooleanField()
    pricePerMinute = DecimalAsFloatField()
    isTopupAllowed = serializers.BooleanField()


# ---- Transactions ----
class BillingTransactionSerializer(serializers.ModelSerializer):
    amount = DecimalAsFloatField()

    class Meta:
        model = BillingTransaction
        fields = (
            "id", "created_at", "kind", "status", "amount", "currency",
            "stripe_invoice_id", "stripe_invoice_url", "stripe_invoice_pdf",
            "stripe_payment_intent_id", "stripe_charge_id",
            "failure_code", "failure_message", "description", "plan_name",
            "subscription_id",
        )
        read_only_fields = fields


# ---- Mutations / Validators ----
class StartSubscriptionSerializer(serializers.Serializer):
    component = serializers.ChoiceField(choices=["support_agent", "outbound_calling", "bundle"])
    plan_id = serializers.IntegerField()
    cancel_at_period_end = serializers.BooleanField(default=False)

    def validate(self, attrs):
        user = self.context["request"].user
        component = attrs["component"]
        plan_id = attrs["plan_id"]

        if component == "support_agent":
            plan = SupportAgentPlan.objects.filter(pk=plan_id, is_active=True, is_trial=False).first()
            if not plan:
                raise serializers.ValidationError("Invalid Support Agent plan.")
        elif component == "outbound_calling":
            plan = OutboundCallingPlan.objects.filter(pk=plan_id, is_active=True, is_trial=False).first()
            if not plan:
                raise serializers.ValidationError("Invalid Outbound Calling plan.")
        else:
            plan = BundlePlan.objects.filter(pk=plan_id, is_active=True, is_trial=False).first()
            if not plan:
                raise serializers.ValidationError("Invalid Combo plan.")
        attrs["plan"] = plan

        ct = ContentType.objects.get_for_model(attrs["plan"].__class__)
        existing = Subscription.objects.filter(
            user=user, plan_content_type=ct, status__in=["trialing", "active", "incomplete", "past_due"]
        ).first()
        attrs["existing_subscription"] = existing
        return attrs


class UpgradeSubscriptionSerializer(serializers.Serializer):
    new_plan_id = serializers.IntegerField()

    def validate(self, attrs):
        sub: Subscription = self.context["subscription"]
        if isinstance(sub.plan, SupportAgentPlan):
            plan = SupportAgentPlan.objects.filter(pk=attrs["new_plan_id"], is_active=True, is_trial=False).first()
            if not plan:
                raise serializers.ValidationError("Invalid Support Agent plan.")
        elif isinstance(sub.plan, OutboundCallingPlan):
            plan = OutboundCallingPlan.objects.filter(pk=attrs["new_plan_id"], is_active=True, is_trial=False).first()
            if not plan:
                raise serializers.ValidationError("Invalid Outbound Calling plan.")
        elif isinstance(sub.plan, BundlePlan):
            plan = BundlePlan.objects.filter(pk=attrs["new_plan_id"], is_active=True, is_trial=False).first()
            if not plan:
                raise serializers.ValidationError("Invalid Combo plan.")
        else:
            raise serializers.ValidationError("Unsupported subscription type.")
        attrs["new_plan"] = plan
        return attrs
class BundlePlanPublicSerializer(serializers.ModelSerializer):
    price = DecimalAsFloatField()
    sa_extra_per_minute = DecimalAsFloatField()
    oc_extra_per_minute = DecimalAsFloatField()

    class Meta:
        model = BundlePlan
        fields = (
            "id", "name", "price", "currency", "billing_interval",
            "sa_minutes", "sa_unlimited_minutes", "sa_extra_per_minute", "sa_customizations_enabled",
            "oc_minutes", "oc_extra_per_minute", "oc_can_use_vai_database",
            "is_active",
        )
class BillingTransactionAdminSerializer(serializers.ModelSerializer):
    userEmail = serializers.EmailField(source="user.email", read_only=True)
    userName = serializers.CharField(source="user.user_name", read_only=True, allow_null=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    method = serializers.SerializerMethodField()
    stripeInvoiceId = serializers.CharField(source="stripe_invoice_id", allow_null=True, read_only=True)
    stripeInvoiceUrl = serializers.URLField(source="stripe_invoice_url", allow_null=True, read_only=True)
    stripePaymentIntentId = serializers.CharField(source="stripe_payment_intent_id", allow_null=True, read_only=True)

    class Meta:
        model = BillingTransaction
        fields = [
            "id",
            "userEmail",
            "userName",
            "createdAt",
            "amount",
            "currency",
            "status",
            "kind",
            "method",
            "stripeInvoiceId",
            "stripeInvoiceUrl",
            "stripePaymentIntentId",
            "failure_code",
            "failure_message",
        ]

    def get_method(self, obj):
        pm = obj.user.payment_methods.filter(is_default=True).order_by("-updated_at").first()
        if pm:
            return f"{pm.brand} •••• {pm.last4}"
        return ""
class BillingTransactionSerializer(serializers.ModelSerializer):
    amount = serializers.FloatField()
    component = serializers.SerializerMethodField()

    class Meta:
        model = BillingTransaction
        fields = (
            "id",
            "created_at",
            "kind",
            "status",
            "amount",
            "currency",
            "stripe_invoice_id",
            "stripe_invoice_url",
            "stripe_invoice_pdf",
            "stripe_payment_intent_id",
            "stripe_charge_id",
            "failure_code",
            "failure_message",
            "description",
            "plan_name",
            "subscription_id",
            "component",
        )
        read_only_fields = fields

    def get_component(self, obj):
        ct = obj.plan_content_type or (obj.subscription.plan_content_type if obj.subscription_id and obj.subscription and obj.subscription.plan_content_type_id else None)
        if not ct:
            return None
        m = ct.model
        if m == "supportagentplan":
            return "support_agent"
        if m == "outboundcallingplan":
            return "outbound_calling"
        if m == "bundleplan":
            return "bundle"
        return None