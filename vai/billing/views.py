from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.utils.dateparse import parse_datetime
from rest_framework.pagination import PageNumberPagination

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, generics
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from .models import (
    SupportAgentPlan, OutboundCallingPlan, Subscription, UsageBucket, BundlePlan, PaymentMethod, BillingTransaction
)
from .services import stripe as stripe_svc
from .serializers import (
    SupportAgentTrialSerializer, OutboundCallingTrialSerializer, FreeTrialUserRowSerializer, UserRowSerializer,
    BundlePlanAdminSerializer, SupportAgentPlanAdminSerializer, OutboundCallingPlanAdminSerializer,
    BundleUserRowSerializer, PaymentMethodSerializer, BillingTransactionAdminSerializer
)


# ---------- internal helpers ----------
class IsAdminAuthed(permissions.IsAuthenticated, permissions.IsAdminUser):
    pass


# ---------- pagination ----------
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 500
def _get_or_create_sa_trial():
    obj, _ = SupportAgentPlan.objects.get_or_create(
        is_trial=True,
        defaults=dict(
            name="Support Agent - Free Trial",
            price=Decimal("0.00"),
            minutes=60,
            unlimited_minutes=False,
            extra_per_minute=Decimal("0.00"),
            customizations_enabled=False,
            is_active=True,
            trial_period_days=14,
            currency="usd",
        ),
    )
    return obj


def _get_or_create_oc_trial():
    obj, _ = OutboundCallingPlan.objects.get_or_create(
        is_trial=True,
        defaults=dict(
            name="Outbound Calling - Free Trial",
            price=Decimal("0.00"),
            minutes=20,
            extra_per_minute=Decimal("0.00"),
            can_use_vai_database=False,
            is_active=True,
            trial_period_days=30,
            currency="usd",
        ),
    )
    return obj


def _plan_type_for_subscription(sub: Subscription) -> str:
    if isinstance(sub.plan, SupportAgentPlan):
        return "support_agent"
    if isinstance(sub.plan, OutboundCallingPlan):
        return "outbound_calling"
    raise ValueError("Unsupported plan type for free trial listing")


def _active_bucket(sub: Subscription, component: str) -> UsageBucket | None:
    sub.initialize_or_rollover_usage_buckets()
    return sub.get_active_bucket(component)


# Keep Stripe Product/Price up-to-date (create if missing; new Price if amount/currency changed)
def _sync_stripe_product_and_price(plan):
    try:
        stripe_svc.ensure_product_and_price(plan)  # creates when missing
        if plan.stripe_price_id:
            price = stripe_svc.stripe.Price.retrieve(plan.stripe_price_id)
            unit_amount = int(Decimal(plan.price) * 100)
            if price["unit_amount"] != unit_amount or price["currency"] != plan.currency:
                new_price = stripe_svc.stripe.Price.create(
                    product=plan.stripe_product_id,
                    currency=plan.currency,
                    unit_amount=unit_amount,
                    recurring={"interval": plan.billing_interval},
                    metadata={
                        "django_model": plan.__class__.__name__,
                        "django_plan_id": str(plan.pk),
                        "is_trial": str(plan.is_trial),
                    },
                )
                try:
                    stripe_svc.stripe.Price.modify(plan.stripe_price_id, active=False)
                except Exception:
                    pass
                plan.stripe_price_id = new_price["id"]
                plan.save(update_fields=["stripe_price_id"])
    except Exception:
        # non-fatal; don't block admin API
        pass


# ---------- API endpoints ----------

class FreeTrialPlansView(APIView):
    """
    GET /billing/free-trials
    Returns both free-trial plans (creating sane defaults if missing).
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        sa = _get_or_create_sa_trial()
        oc = _get_or_create_oc_trial()
        sa_data = SupportAgentTrialSerializer(sa).data
        oc_data = OutboundCallingTrialSerializer(oc).data
        return Response({"support_agent": sa_data, "outbound_calling": oc_data})


class SupportAgentTrialView(APIView):
    """
    GET/PUT /billing/free-trials/support-agent
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        plan = _get_or_create_sa_trial()
        return Response(SupportAgentTrialSerializer(plan).data)

    @transaction.atomic
    def put(self, request):
        plan = _get_or_create_sa_trial()
        ser = SupportAgentTrialSerializer(instance=plan, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        plan = ser.save(is_trial=True)
        _sync_stripe_product_and_price(plan)
        return Response(SupportAgentTrialSerializer(plan).data)


class OutboundCallingTrialView(APIView):
    """
    GET/PUT /billing/free-trials/outbound-calling
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        plan = _get_or_create_oc_trial()
        return Response(OutboundCallingTrialSerializer(plan).data)

    @transaction.atomic
    def put(self, request):
        plan = _get_or_create_oc_trial()
        ser = OutboundCallingTrialSerializer(instance=plan, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        plan = ser.save(is_trial=True)
        _sync_stripe_product_and_price(plan)
        return Response(OutboundCallingTrialSerializer(plan).data)


class FreeTrialUsersView(APIView):
    """
    GET /billing/free-trials/users
    Lists users with active/trialing subscriptions to *trial* plans (SA or OC).
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        sa_ids = list(SupportAgentPlan.objects.filter(is_trial=True).values_list("id", flat=True))
        oc_ids = list(OutboundCallingPlan.objects.filter(is_trial=True).values_list("id", flat=True))

        sa_ct = ContentType.objects.get_for_model(SupportAgentPlan)
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)

        subs = Subscription.objects.filter(
            models.Q(plan_content_type=sa_ct, plan_object_id__in=sa_ids)
            | models.Q(plan_content_type=oc_ct, plan_object_id__in=oc_ids),
            status__in=["trialing", "active"],  # trial plans only
        ).select_related("user")

        rows = []
        for sub in subs:
            try:
                plan_type = _plan_type_for_subscription(sub)
            except ValueError:
                continue

            component = "support_agent" if plan_type == "support_agent" else "outbound_calling"
            bucket = _active_bucket(sub, component)
            remaining = 0
            if bucket:
                if bucket.unlimited:
                    remaining = 10 ** 12  # mirrors component_remaining_seconds()
                else:
                    remaining = max(0, bucket.seconds_included - bucket.seconds_used)

            rows.append(dict(
                id=sub.pk,
                userId=sub.user_id,
                userName=sub.user.user_name or None,
                email=sub.user.email,
                planType=plan_type,
                planName=sub.plan.name,
                subDate=sub.started_at,
                currentPeriodEnd=sub.current_period_end or sub.started_at,
                remainingSeconds=int(remaining),
                subscriptionId=sub.pk,  # local id used by adjust/cancel endpoints
            ))

        data = {"results": FreeTrialUserRowSerializer(rows, many=True).data}
        return Response(data)


class FreeTrialUserAdjustView(APIView):
    """
    POST /billing/free-trials/users/<int:subscription_id>/adjust
    Body: { add_minutes?: number, extend_days?: number }
    - add_minutes => increases current bucket's seconds_included
    - extend_days => extends current period end (and Stripe trial_end if still trialing)
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @transaction.atomic
    def post(self, request, subscription_id: int):
        add_minutes = int(request.data.get("add_minutes") or 0)
        extend_days = int(request.data.get("extend_days") or 0)

        try:
            sub = Subscription.objects.select_related("user").get(pk=subscription_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)

        try:
            plan_type = _plan_type_for_subscription(sub)
        except ValueError:
            return Response({"detail": "Unsupported plan for this action"}, status=400)

        component = "support_agent" if plan_type == "support_agent" else "outbound_calling"
        sub.initialize_or_rollover_usage_buckets()
        bucket = sub.get_active_bucket(component)
        if not bucket:
            return Response({"detail": "No active usage bucket"}, status=409)

        if add_minutes > 0:
            bucket.seconds_included = models.F("seconds_included") + (add_minutes * 60)
            bucket.save(update_fields=["seconds_included"])
            bucket.refresh_from_db()

        if extend_days > 0:
            new_end = (sub.current_period_end or timezone.now()) + timedelta(days=extend_days)
            sub.current_period_end = new_end
            sub.save(update_fields=["current_period_end"])

            # keep bucket end aligned
            bucket.period_end = new_end
            bucket.save(update_fields=["period_end"])

            # If still in Stripe trial, extend trial_end at Stripe too
            if sub.status == "trialing" and sub.stripe_subscription_id:
                try:
                    # Stripe expects a UNIX timestamp for trial_end; add buffer seconds
                    trial_end_ts = int(new_end.timestamp())
                    stripe_svc.stripe.Subscription.modify(sub.stripe_subscription_id, trial_end=trial_end_ts)
                except Exception:
                    # Non-fatal if Stripe disallows (e.g., not in trial)
                    pass

        remaining = (10 ** 12 if bucket.unlimited else max(0, bucket.seconds_included - bucket.seconds_used))
        return Response({
            "ok": True,
            "remainingSeconds": int(remaining),
            "currentPeriodEnd": sub.current_period_end,
        })


class FreeTrialUserDeleteView(APIView):
    """
    DELETE /billing/free-trials/users/<int:subscription_id>
    Cancels the subscription immediately.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @transaction.atomic
    def delete(self, request, subscription_id: int):
        try:
            sub = Subscription.objects.get(pk=subscription_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)

        try:
            stripe_svc.cancel_subscription(sub, at_period_end=False)
        except Exception:
            sub.status = "canceled"
            sub.canceled_at = timezone.now()
            sub.ended_at = sub.ended_at or timezone.now()
            sub.save(update_fields=["status", "canceled_at", "ended_at"])

        return Response({"ok": True}, status=200)
class BundlePlansView(generics.ListCreateAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = BundlePlanAdminSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = BundlePlan.objects.all().order_by("-created_at")
        # optional filters
        is_active = self.request.query_params.get("is_active")
        if is_active in ("true", "false"):
            qs = qs.filter(is_active=(is_active == "true"))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        plan = serializer.save(is_trial=False)  # bundles page is for real plans
        if getattr(plan, "auto_sync_to_stripe", True):
            _sync_stripe_product_and_price(plan)


class BundlePlanDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = BundlePlanAdminSerializer
    queryset = BundlePlan.objects.all()

    @transaction.atomic
    def perform_update(self, serializer):
        plan = serializer.save(is_trial=False)
        if getattr(plan, "auto_sync_to_stripe", True):
            _sync_stripe_product_and_price(plan)


# ----- Support Agent (non-trial by default) -----
class SupportAgentPlansView(generics.ListCreateAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = SupportAgentPlanAdminSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        include_trials = self.request.query_params.get("include_trials") == "true"
        qs = SupportAgentPlan.objects.all().order_by("-created_at")
        if not include_trials:
            qs = qs.filter(is_trial=False)
        is_active = self.request.query_params.get("is_active")
        if is_active in ("true", "false"):
            qs = qs.filter(is_active=(is_active == "true"))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        plan = serializer.save(is_trial=False)
        if getattr(plan, "auto_sync_to_stripe", True):
            _sync_stripe_product_and_price(plan)


class SupportAgentPlanDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = SupportAgentPlanAdminSerializer

    def get_queryset(self):
        return SupportAgentPlan.objects.all()

    @transaction.atomic
    def perform_update(self, serializer):
        plan = serializer.save(is_trial=False)
        if getattr(plan, "auto_sync_to_stripe", True):
            _sync_stripe_product_and_price(plan)


# ----- Outbound Calling (non-trial by default) -----
class OutboundCallingPlansView(generics.ListCreateAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = OutboundCallingPlanAdminSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        include_trials = self.request.query_params.get("include_trials") == "true"
        qs = OutboundCallingPlan.objects.all().order_by("-created_at")
        if not include_trials:
            qs = qs.filter(is_trial=False)
        is_active = self.request.query_params.get("is_active")
        if is_active in ("true", "false"):
            qs = qs.filter(is_active=(is_active == "true"))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        plan = serializer.save(is_trial=False)
        if getattr(plan, "auto_sync_to_stripe", True):
            _sync_stripe_product_and_price(plan)


class OutboundCallingPlanDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = OutboundCallingPlanAdminSerializer

    def get_queryset(self):
        return OutboundCallingPlan.objects.all()

    @transaction.atomic
    def perform_update(self, serializer):
        plan = serializer.save(is_trial=False)
        if getattr(plan, "auto_sync_to_stripe", True):
            _sync_stripe_product_and_price(plan)


# ==========================================================
#                       USERS TABLES
# ==========================================================

def _remaining_for(sub: Subscription, component: str) -> int:
    sub.initialize_or_rollover_usage_buckets()
    bucket = sub.get_active_bucket(component)
    if not bucket:
        return 0
    if bucket.unlimited:
        return 10**12
    return max(0, bucket.seconds_included - bucket.seconds_used)


class BundleUsersView(generics.ListAPIView):
    """
    GET /billing/bundles/users?plan_id=<id>&q=<search>
    List users with active/trialing subscriptions to Bundle plans (no trials concept for bundles).
    """
    permission_classes = [IsAdminAuthed]
    serializer_class = BundleUserRowSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        # We build rows manually; return Subscriptions queryset then map in list()
        ctype = ContentType.objects.get_for_model(BundlePlan)
        qs = Subscription.objects.filter(
            plan_content_type=ctype,
            status__in=["trialing", "active"],
        ).select_related("user").order_by("-started_at")

        plan_id = self.request.query_params.get("plan_id")
        if plan_id:
            qs = qs.filter(plan_object_id=int(plan_id))
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(user__email__icontains=q) | models.Q(user__user_name__icontains=q))
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        subs = page if page is not None else qs

        rows = []
        for sub in subs:
            sa_seconds = _remaining_for(sub, "support_agent")
            oc_seconds = _remaining_for(sub, "outbound_calling")
            total_seconds = sa_seconds + oc_seconds
            rows.append(dict(
                id=sub.pk,
                userId=sub.user_id,
                userName=sub.user.user_name or None,
                email=sub.user.email,
                planId=sub.plan_object_id,
                planName=sub.plan.name,
                renewalDate=sub.current_period_end or sub.started_at,
                minutesRemaining=int(total_seconds // 60),
                hoursRemaining=int(total_seconds // 3600),
                saRemainingSeconds=int(sa_seconds),
                ocRemainingSeconds=int(oc_seconds),
                subscriptionId=sub.pk,
            ))
        serializer = self.get_serializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class SupportAgentUsersView(generics.ListAPIView):
    """
    GET /billing/support-agent/users?plan_id=<id>&q=<search>&include_trials=false
    Non-trial plans by default.
    """
    permission_classes = [IsAdminAuthed]
    serializer_class = UserRowSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        sa_ct = ContentType.objects.get_for_model(SupportAgentPlan)
        include_trials = self.request.query_params.get("include_trials") == "true"
        plan_ids = list(SupportAgentPlan.objects.filter(
            is_trial=True if include_trials else False
        ).values_list("id", flat=True))
        # Exclude trials by default
        plan_qs = SupportAgentPlan.objects.all()
        if not include_trials:
            plan_qs = plan_qs.filter(is_trial=False)
        allowed_ids = list(plan_qs.values_list("id", flat=True))

        qs = Subscription.objects.filter(
            plan_content_type=sa_ct,
            plan_object_id__in=allowed_ids,
            status__in=["trialing", "active"],
        ).select_related("user").order_by("-started_at")

        plan_id = self.request.query_params.get("plan_id")
        if plan_id:
            qs = qs.filter(plan_object_id=int(plan_id))
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(user__email__icontains=q) | models.Q(user__user_name__icontains=q))
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        subs = page if page is not None else qs

        rows = []
        for sub in subs:
            rem = _remaining_for(sub, "support_agent")
            rows.append(dict(
                id=sub.pk,
                userId=sub.user_id,
                userName=sub.user.user_name or None,
                email=sub.user.email,
                planId=sub.plan_object_id,
                planName=sub.plan.name,
                renewalDate=sub.current_period_end or sub.started_at,
                minutesRemaining=int(rem // 60),
                hoursRemaining=int(rem // 3600),
                subscriptionId=sub.pk,
            ))
        serializer = self.get_serializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class OutboundCallingUsersView(generics.ListAPIView):
    """
    GET /billing/outbound-calling/users?plan_id=<id>&q=<search>&include_trials=false
    Non-trial plans by default.
    """
    permission_classes = [IsAdminUser]
    serializer_class = UserRowSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)
        include_trials = self.request.query_params.get("include_trials") == "true"
        plan_qs = OutboundCallingPlan.objects.all()
        if not include_trials:
            plan_qs = plan_qs.filter(is_trial=False)
        allowed_ids = list(plan_qs.values_list("id", flat=True))

        qs = Subscription.objects.filter(
            plan_content_type=oc_ct,
            plan_object_id__in=allowed_ids,
            status__in=["trialing", "active"],
        ).select_related("user").order_by("-started_at")

        plan_id = self.request.query_params.get("plan_id")
        if plan_id:
            qs = qs.filter(plan_object_id=int(plan_id))
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(user__email__icontains=q) | models.Q(user__user_name__icontains=q))
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        subs = page if page is not None else qs

        rows = []
        for sub in subs:
            rem = _remaining_for(sub, "outbound_calling")
            rows.append(dict(
                id=sub.pk,
                userId=sub.user_id,
                userName=sub.user.user_name or None,
                email=sub.user.email,
                planId=sub.plan_object_id,
                planName=sub.plan.name,
                renewalDate=sub.current_period_end or sub.started_at,
                minutesRemaining=int(rem // 60),
                hoursRemaining=int(rem // 3600),
                subscriptionId=sub.pk,
            ))
        serializer = self.get_serializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
class PaymentMethodsView(APIView):
    """
    GET /billing/payment-methods — list cards (syncs from Stripe)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pms, default_pm_id = stripe_svc.list_customer_payment_methods(request.user)
        data = PaymentMethodSerializer(
            PaymentMethod.objects.filter(user=request.user).order_by("-is_default", "-updated_at"),
            many=True,
        ).data
        return Response({"default": default_pm_id, "methods": data})


class CreateSetupIntentView(APIView):
    """
    POST /billing/payment-methods/setup-intent — returns {client_secret}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        payload = stripe_svc.create_setup_intent(request.user)
        return Response({"client_secret": payload["client_secret"]}, status=status.HTTP_201_CREATED)


class SetDefaultPaymentMethodView(APIView):
    """
    POST /billing/payment-methods/set-default {payment_method_id}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        pm_id = request.data.get("payment_method_id")
        if not pm_id:
            return Response({"detail": "payment_method_id is required"}, status=400)
        try:
            stripe_svc.set_default_payment_method(request.user, pm_id)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        # return the refreshed list
        _, _ = stripe_svc.list_customer_payment_methods(request.user)
        data = PaymentMethodSerializer(PaymentMethod.objects.filter(user=request.user), many=True).data
        return Response({"ok": True, "methods": data}, status=200)


class DeletePaymentMethodView(APIView):
    """
    DELETE /billing/payment-methods/<pm_id>
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pm_id: str):
        try:
            stripe_svc.detach_payment_method(request.user, pm_id)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        return Response(status=204)
class PaymentsAdminView(generics.ListAPIView):
    permission_classes = [IsAdminAuthed]
    serializer_class = BillingTransactionAdminSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = BillingTransaction.objects.select_related("user").all()
        status_param = self.request.query_params.get("status")
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            qs = qs.filter(status__in=statuses)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                models.Q(user__email__icontains=q)
                | models.Q(user__user_name__icontains=q)
                | models.Q(stripe_invoice_id__icontains=q)
                | models.Q(stripe_payment_intent_id__icontains=q)
                | models.Q(stripe_charge_id__icontains=q)
                | models.Q(plan_name__icontains=q)
            )
        date_from = self.request.query_params.get("date_from")
        if date_from:
            dt = parse_datetime(date_from)
            if dt:
                qs = qs.filter(created_at__gte=dt)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            dt = parse_datetime(date_to)
            if dt:
                qs = qs.filter(created_at__lte=dt)
        amt_min = self.request.query_params.get("amount_min")
        if amt_min:
            try:
                qs = qs.filter(amount__gte=amt_min)
            except Exception:
                pass
        amt_max = self.request.query_params.get("amount_max")
        if amt_max:
            try:
                qs = qs.filter(amount__lte=amt_max)
            except Exception:
                pass
        ordering = self.request.query_params.get("ordering")
        allowed = {"created_at", "-created_at", "amount", "-amount", "status", "-status", "user__email", "-user__email"}
        if ordering in allowed:
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by("-created_at")
        return qs

class RetryPaymentView(APIView):
    permission_classes = [IsAdminAuthed]

    def post(self, request, pk: int):
        try:
            tx = BillingTransaction.objects.select_related("user").get(pk=pk)
        except BillingTransaction.DoesNotExist:
            return Response({"detail": "Transaction not found"}, status=404)
        if tx.status in ["succeeded", "paid"]:
            ser = BillingTransactionAdminSerializer(tx).data
            return Response({"transaction": ser, "ok": True}, status=200)
        try:
            result = None
            if tx.stripe_invoice_id:
                result = stripe_svc.stripe.Invoice.pay(tx.stripe_invoice_id)
                inv_status = result.get("status")
                if inv_status in ["paid", "succeeded"]:
                    tx.status = "succeeded"
                    tx.stripe_charge_id = (result.get("charge") or "") if isinstance(result, dict) else ""
                    tx.failure_code = ""
                    tx.failure_message = ""
                else:
                    tx.status = "failed"
                    tx.failure_message = f"Invoice status: {inv_status}"
            elif tx.stripe_payment_intent_id:
                pi = stripe_svc.stripe.PaymentIntent.confirm(tx.stripe_payment_intent_id)
                pi_status = pi.get("status")
                if pi_status in ["succeeded"]:
                    tx.status = "succeeded"
                    charges = pi.get("charges", {}).get("data", [])
                    if charges:
                        tx.stripe_charge_id = charges[0].get("id")
                    tx.failure_code = ""
                    tx.failure_message = ""
                else:
                    tx.status = "failed"
                    tx.failure_message = f"PaymentIntent status: {pi_status}"
            else:
                return Response({"detail": "No retryable Stripe artifact on transaction"}, status=400)
            tx.save()
            ser = BillingTransactionAdminSerializer(tx).data
            return Response({"transaction": ser, "ok": tx.status == "succeeded"}, status=200)
        except Exception as e:
            tx.status = "failed"
            tx.failure_message = str(e)
            tx.save(update_fields=["status", "failure_message"])
            return Response({"detail": str(e)}, status=400)

class RetryFailedChargesView(APIView):
    permission_classes = [IsAdminAuthed]

    def post(self, request):
        failed_qs = BillingTransaction.objects.filter(status__in=["failed", "canceled"]).order_by("-created_at")
        succeeded = 0
        failed = 0
        for tx in failed_qs:
            try:
                if tx.stripe_invoice_id:
                    res = stripe_svc.stripe.Invoice.pay(tx.stripe_invoice_id)
                    inv_status = res.get("status")
                    if inv_status in ["paid", "succeeded"]:
                        tx.status = "succeeded"
                        tx.failure_code = ""
                        tx.failure_message = ""
                        succeeded += 1
                    else:
                        tx.status = "failed"
                        tx.failure_message = f"Invoice status: {inv_status}"
                        failed += 1
                elif tx.stripe_payment_intent_id:
                    pi = stripe_svc.stripe.PaymentIntent.confirm(tx.stripe_payment_intent_id)
                    if pi.get("status") == "succeeded":
                        tx.status = "succeeded"
                        tx.failure_code = ""
                        tx.failure_message = ""
                        succeeded += 1
                    else:
                        tx.status = "failed"
                        tx.failure_message = f"PaymentIntent status: {pi.get('status')}"
                        failed += 1
                else:
                    failed += 1
                tx.save()
            except Exception as e:
                tx.status = "failed"
                tx.failure_message = str(e)
                tx.save(update_fields=["status", "failure_message"])
                failed += 1
        return Response({"ok": True, "succeeded": succeeded, "failed": failed}, status=200)