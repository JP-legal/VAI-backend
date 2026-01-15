import re
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

User = get_user_model()

E164_RE = re.compile(r'^\+[1-9]\d{1,14}$')


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=False)

    def validate(self, attrs):
        email = attrs.get("email")
        if email:
            attrs["email"] = email.lower()
        return attrs


class UserSerializer(serializers.ModelSerializer):
    access_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "user_name",
            "email_verified", "is_active", "is_staff",
            "phone_number", "country", "address",
            "outbound_calling", "cs_service_agent",
            "last_login", "date_joined", "access_type",
        ]
        read_only_fields = ["email_verified", "last_login", "date_joined", "access_type"]

    def get_access_type(self, obj):
        from vai.billing.models import Subscription
        now = timezone.now()
        active_models = set(
            Subscription.objects.filter(
                user=obj,
                status__in=["trialing", "active", "incomplete", "past_due"]
            )
            .filter(Q(current_period_end__isnull=True) | Q(current_period_end__gte=now))
            .values_list("plan_content_type__model", flat=True)
        )
        has_sa = "supportagentplan" in active_models or "bundleplan" in active_models
        has_oc = "outboundcallingplan" in active_models or "bundleplan" in active_models
        if has_sa and has_oc:
            return "Support Agent & Outbound Calls"
        if has_sa:
            return "Support Agent"
        if has_oc:
            return "Outbound Calls"
        return "None"


class AdminSetPasswordSerializer(serializers.Serializer):
    new_password1 = serializers.CharField(write_only=True, min_length=8)
    new_password2 = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs["new_password1"] != attrs["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Passwords do not match."})
        try:
            validate_password(attrs["new_password1"])
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password1": e.messages})
        return attrs


class RegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    user_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value: str):
        email = value.lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("Email already registered.")
        return email

    def validate_password(self, value: str):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def create(self, validated_data):
        # Keep account active (so JWTs work); mark as not verified yet
        return User.objects.create_user(
            email=validated_data["email"],
            user_name=validated_data["user_name"],
            password=validated_data["password"],
            is_active=True,
            email_verified=False,
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        request = self.context.get("request")
        user = authenticate(request, email=attrs.get("email"), password=attrs.get("password"))
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        if not user.is_active:
            raise serializers.ValidationError("Account is not active.")
        attrs["user"] = user
        return attrs


class SelfUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["email", "user_name", "phone_number", "country", "address"]

    def validate_email(self, value: str):
        email = (value or "").lower()
        qs = User.objects.filter(email__iexact=email)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Email already in use.")
        return email

    def validate_phone_number(self, value: str):
        v = value or ""
        if not v:
            return v
        if not E164_RE.match(v):
            raise serializers.ValidationError("Phone number must be in E.164 format, e.g. +14155550100.")
        return v

    def validate_country(self, value: str):
        if not value:
            return value
        return value

    def update(self, instance, validated_data):
        from .helpers import send_verification_email
        # handle email change + re-verification
        new_email = validated_data.get("email")
        if new_email and new_email.lower() != instance.email:
            instance.email = new_email.lower()
            instance.email_verified = False
            send_verification_email(instance)

        for field in ("user_name", "phone_number", "country", "address"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password1 = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["old_password"]):
            raise serializers.ValidationError({"old_password": "Wrong password."})
        if attrs["new_password1"] != attrs["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Passwords do not match."})
        try:
            validate_password(attrs["new_password1"], user=user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password1": e.messages})
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordConfirmSerializer(serializers.Serializer):
    uidb64 = serializers.CharField()
    token = serializers.CharField()
    new_password1 = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password1"] != attrs["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Passwords do not match."})
        try:
            validate_password(attrs["new_password1"])
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password1": e.messages})
        return attrs


class AdminUserListSerializer(serializers.ModelSerializer):
    access_type = serializers.SerializerMethodField()
    plan_customizations_enabled = serializers.SerializerMethodField()
    plan_can_use_vai_database = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "user_name", "email_verified", "is_active", "is_staff",
            "date_joined", "last_login", "outbound_calling", "outbound_customization",
            "cs_service_agent", "cs_use_vai_database", "access_type",
            "plan_customizations_enabled", "plan_can_use_vai_database",
        ]

    def _get_active_subscriptions(self, obj):
        """Get active subscriptions for a user, cached per serializer instance."""
        from vai.billing.models import Subscription, SupportAgentPlan, OutboundCallingPlan, BundlePlan
        from django.contrib.contenttypes.models import ContentType

        cache_key = f"_subs_{obj.pk}"
        if hasattr(self, cache_key):
            return getattr(self, cache_key)

        now = timezone.now()
        active_statuses = ["trialing", "active", "incomplete", "past_due"]

        sa_ct = ContentType.objects.get_for_model(SupportAgentPlan)
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)
        bu_ct = ContentType.objects.get_for_model(BundlePlan)

        def pick(qs):
            return qs.filter(status__in=active_statuses).filter(
                Q(current_period_end__isnull=True) | Q(current_period_end__gte=now)
            ).order_by("-started_at").first()

        sa_sub = pick(Subscription.objects.filter(user=obj, plan_content_type=sa_ct))
        oc_sub = pick(Subscription.objects.filter(user=obj, plan_content_type=oc_ct))
        bu_sub = pick(Subscription.objects.filter(user=obj, plan_content_type=bu_ct))

        result = {
            "support_sub": bu_sub or sa_sub,
            "outbound_sub": bu_sub or oc_sub,
        }
        setattr(self, cache_key, result)
        return result

    def get_plan_customizations_enabled(self, obj):
        """Check if user's Support Agent plan has customizations_enabled."""
        subs = self._get_active_subscriptions(obj)
        support_sub = subs.get("support_sub")
        if not support_sub:
            return False
        comps = getattr(support_sub.plan, "components", None)
        if callable(comps):
            toggles = comps().get("support_agent", {})
            return bool(toggles.get("customizations_enabled", False))
        return False

    def get_plan_can_use_vai_database(self, obj):
        """Check if user's Outbound plan has can_use_vai_database."""
        subs = self._get_active_subscriptions(obj)
        outbound_sub = subs.get("outbound_sub")
        if not outbound_sub:
            return False
        comps = getattr(outbound_sub.plan, "components", None)
        if callable(comps):
            toggles = comps().get("outbound_calling", {})
            return bool(toggles.get("can_use_vai_database", False))
        return False

    def get_access_type(self, obj):
        from vai.billing.models import Subscription
        now = timezone.now()
        active_models = set(
            Subscription.objects.filter(
                user=obj,
                status__in=["trialing", "active", "incomplete", "past_due"]
            )
            .filter(Q(current_period_end__isnull=True) | Q(current_period_end__gte=now))
            .values_list("plan_content_type__model", flat=True)
        )
        has_sa = "supportagentplan" in active_models or "bundleplan" in active_models
        has_oc = "outboundcallingplan" in active_models or "bundleplan" in active_models
        if has_sa and has_oc:
            return "Support Agent & Outbound Calls"
        if has_sa:
            return "Support Agent"
        if has_oc:
            return "Outbound Calls"
        return "None"


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    access_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "user_name", "is_active", "is_staff",
            "outbound_calling", "outbound_customization",
            "cs_service_agent", "cs_use_vai_database",
            "email_verified", "last_login", "date_joined", "access_type",
        ]
        read_only_fields = ["email_verified", "is_staff", "last_login", "date_joined", "access_type"]

    def get_access_type(self, obj):
        from vai.billing.models import Subscription
        now = timezone.now()
        active_models = set(
            Subscription.objects.filter(
                user=obj,
                status__in=["trialing", "active", "incomplete", "past_due"]
            )
            .filter(Q(current_period_end__isnull=True) | Q(current_period_end__gte=now))
            .values_list("plan_content_type__model", flat=True)
        )
        has_sa = "supportagentplan" in active_models or "bundleplan" in active_models
        has_oc = "outboundcallingplan" in active_models or "bundleplan" in active_models
        if has_sa and has_oc:
            return "Support Agent & Outbound Calls"
        if has_sa:
            return "Support Agent"
        if has_oc:
            return "Outbound Calls"
        return "None"

    def validate_email(self, value: str):
        email = value.lower()
        qs = User.objects.filter(email__iexact=email)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Email already in use.")
        return email

    def update(self, instance, validated_data):
        changed = set()
        if "email" in validated_data:
            new_email = validated_data["email"].lower()
            if new_email != instance.email:
                instance.email = new_email
                instance.email_verified = False
                changed.update({"email", "email_verified"})
        for field in ["user_name", "is_active", "outbound_calling", "outbound_customization", "cs_service_agent", "cs_use_vai_database"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
                changed.add(field)
        if changed:
            instance.save(update_fields=list(changed))
        return instance