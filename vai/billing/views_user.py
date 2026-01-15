from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timezone as dt_tz, timedelta, date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework import serializers as drf_serializers

from .models import Subscription, SupportAgentPlan, OutboundCallingPlan, BillingTransaction, PaymentMethod, BundlePlan, UsageEvent
from .serializers import (
    SupportAgentPlanPublicSerializer, OutboundCallingPlanPublicSerializer,
    MySubscriptionSerializer, BillingTransactionSerializer,
    StartSubscriptionSerializer, UpgradeSubscriptionSerializer, BundlePlanPublicSerializer
)
from .services import stripe as stripe_svc


class IsAuthed(permissions.IsAuthenticated):
    pass


def _days_until(dt: datetime | None) -> int:
    if not dt:
        return 0
    now = timezone.now()
    delta = dt - now
    days = int(delta.total_seconds() // 86400)
    return max(0, days)


def _my_component_row(sub: Subscription, component: str) -> dict:
    ps, pe, item_id, _ = stripe_svc.normalize_subscription_with_periods(sub.stripe_subscription_id, sub.stripe_subscription_item_id)
    changed_fields = []
    if ps and sub.current_period_start != ps:
        sub.current_period_start = ps
        changed_fields.append("current_period_start")
    if pe and sub.current_period_end != pe:
        sub.current_period_end = pe
        changed_fields.append("current_period_end")
    if item_id and sub.stripe_subscription_item_id != item_id:
        sub.stripe_subscription_item_id = item_id
        changed_fields.append("stripe_subscription_item_id")
    if changed_fields:
        sub.save(update_fields=changed_fields)
    sub.initialize_or_rollover_usage_buckets()
    plan = sub.plan
    bucket = sub.get_active_bucket(component)
    rem_seconds = sub.component_remaining_seconds(component)
    latest_inv_url = None
    if sub.latest_invoice_id:
        try:
            inv = stripe_svc.stripe.Invoice.retrieve(sub.latest_invoice_id)
            is_paid = bool(inv.get("paid"))
            status = str(inv.get("status") or "")
            if (not is_paid) and status in {"open", "draft"}:
                latest_inv_url = inv.get("hosted_invoice_url")
        except Exception:
            pass
    seconds_included = 0
    seconds_used = 0
    unlimited = False
    price_per_minute = 0.0
    if bucket:
        seconds_included = bucket.seconds_included
        seconds_used = bucket.seconds_used
        unlimited = bool(bucket.unlimited)
        try:
            price_per_minute = float(bucket.extra_per_minute)
        except Exception:
            price_per_minute = 0.0
    now = timezone.now()
    is_trial_plan = bool(getattr(plan, "is_trial", False))
    period_active = bool(sub.current_period_end and sub.current_period_end >= now)
    cancel_at_end_flag = bool(sub.cancel_at_period_end and period_active and sub.status != "canceled")
    status_allows = sub.status in {"active", "trialing"} or cancel_at_end_flag
    is_topup_allowed = bool(status_allows and period_active and not is_trial_plan)
    return dict(
        subscriptionId=sub.pk,
        component=component,
        planId=sub.plan_object_id,
        planName=getattr(plan, "name", str(plan)),
        status=sub.status,
        billingInterval=getattr(plan, "billing_interval", "month"),
        currentPeriodEnd=sub.current_period_end,
        cancelAtPeriodEnd=sub.cancel_at_period_end,
        daysUntilRenewal=_days_until(sub.current_period_end),
        minutesRemaining=int(rem_seconds // 60),
        hoursRemaining=int(rem_seconds // 3600),
        latestInvoiceUrl=latest_inv_url,
        minutesIncluded=int(seconds_included // 60),
        minutesUsed=int(seconds_used // 60),
        unlimited=unlimited,
        pricePerMinute=price_per_minute,
        isTopupAllowed=is_topup_allowed,
    )




class MySubscriptionsView(APIView):
    permission_classes = [IsAuthed]

    def get(self, request):
        sa_ct = ContentType.objects.get_for_model(SupportAgentPlan)
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)
        bu_ct = ContentType.objects.get_for_model(BundlePlan)

        def pick_any(qs):
            return qs.filter(status__in=["trialing", "active", "incomplete", "past_due"]).order_by(
                "-started_at").first()

        def pick_non_trial(qs):
            subs = qs.filter(status__in=["trialing", "active", "incomplete", "past_due"]).order_by("-started_at")
            for s in subs:
                try:
                    if not getattr(s.plan, "is_trial", False):
                        return s
                except Exception:
                    continue
            return None

        sa_sub = pick_non_trial(Subscription.objects.filter(user=request.user, plan_content_type=sa_ct))
        oc_sub = pick_non_trial(Subscription.objects.filter(user=request.user, plan_content_type=oc_ct))
        bu_sub = pick_any(Subscription.objects.filter(user=request.user, plan_content_type=bu_ct))

        data = {}
        if bu_sub:
            bundle_row = _my_component_row(bu_sub, "support_agent")
            bundle_row["component"] = "bundle"
            data["bundle"] = MySubscriptionSerializer(bundle_row).data
            data["support_agent"] = MySubscriptionSerializer(_my_component_row(bu_sub, "support_agent")).data
            data["outbound_calling"] = MySubscriptionSerializer(_my_component_row(bu_sub, "outbound_calling")).data
        else:
            if sa_sub:
                data["support_agent"] = MySubscriptionSerializer(_my_component_row(sa_sub, "support_agent")).data
            if oc_sub:
                data["outbound_calling"] = MySubscriptionSerializer(_my_component_row(oc_sub, "outbound_calling")).data
        return Response(data, status=200)


class AvailablePlansView(APIView):
    permission_classes = [IsAuthed]

    def get(self, request):
        component = request.query_params.get("component")
        if component not in ("support_agent", "outbound_calling", "bundle"):
            return Response({"detail": "component is required"}, status=400)

        if component == "support_agent":
            plans = SupportAgentPlan.objects.filter(is_active=True, is_trial=False).order_by("price", "minutes")
            return Response(SupportAgentPlanPublicSerializer(plans, many=True).data)
        elif component == "outbound_calling":
            plans = OutboundCallingPlan.objects.filter(is_active=True, is_trial=False).order_by("price", "minutes")
            return Response(OutboundCallingPlanPublicSerializer(plans, many=True).data)
        else:
            plans = BundlePlan.objects.filter(is_active=True, is_trial=False).order_by("price", "sa_minutes", "oc_minutes")
            return Response(BundlePlanPublicSerializer(plans, many=True).data)


class StartSubscriptionView(APIView):
    permission_classes = [IsAuthed]

    def post(self, request):
        ser = StartSubscriptionSerializer(data=request.data, context={"request": request})
        if not ser.is_valid():
            return Response(ser.errors, status=400)
        plan = ser.validated_data["plan"]
        existing = ser.validated_data["existing_subscription"]
        if existing:
            return Response({"detail": "You already have a subscription for this component. Use upgrade instead."},
                            status=409)

        ct_sa = ContentType.objects.get_for_model(SupportAgentPlan)
        ct_oc = ContentType.objects.get_for_model(OutboundCallingPlan)
        ct_bu = ContentType.objects.get_for_model(BundlePlan)
        active_statuses = ["trialing", "active", "incomplete", "past_due"]

        if isinstance(plan, BundlePlan):
            subs = Subscription.objects.filter(user=request.user, plan_content_type__in=[ct_sa, ct_oc],
                                               status__in=active_statuses).order_by("-started_at")
            for s in subs:
                try:
                    if not getattr(s.plan, "is_trial", False):
                        return Response({"detail": "You cannot purchase a Combo plan while a single plan is active."},
                                        status=409)
                except Exception:
                    continue
        else:
            subs = Subscription.objects.filter(user=request.user, plan_content_type=ct_bu,
                                               status__in=active_statuses).order_by("-started_at")
            for s in subs:
                try:
                    if not getattr(s.plan, "is_trial", False):
                        return Response({"detail": "You cannot purchase a single plan while a Combo plan is active."},
                                        status=409)
                except Exception:
                    continue

        pm = PaymentMethod.objects.filter(user=request.user).order_by("-is_default", "-updated_at").first()
        pm_id = pm.stripe_payment_method_id if pm else None
        sub = stripe_svc.create_subscription(
            request.user,
            plan,
            pm_id,
            cancel_at_period_end=ser.validated_data["cancel_at_period_end"],
        )
        row = _my_component_row(sub, "support_agent" if isinstance(plan, SupportAgentPlan) else (
            "outbound_calling" if isinstance(plan, OutboundCallingPlan) else "support_agent"))
        if isinstance(plan, BundlePlan):
            row["component"] = "bundle"

        try:
            trials = Subscription.objects.filter(user=request.user, status__in=active_statuses).exclude(pk=sub.pk)
            for t in trials:
                try:
                    if getattr(t.plan, "is_trial", False):
                        stripe_svc.cancel_subscription(t, at_period_end=False)
                except Exception:
                    continue
        except Exception:
            pass

        return Response({"subscription": MySubscriptionSerializer(row).data}, status=201)


class UpgradeMySubscriptionView(APIView):
    permission_classes = [IsAuthed]

    def post(self, request, subscription_id: int):
        try:
            sub = Subscription.objects.select_related("user").get(pk=subscription_id, user=request.user)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
        ser = UpgradeSubscriptionSerializer(data=request.data, context={"subscription": sub})
        if not ser.is_valid():
            return Response(ser.errors, status=400)
        new_plan = ser.validated_data["new_plan"]
        success_url = getattr(settings, "CHECKOUT_SUCCESS_URL", "https://example.com/billing/success")
        cancel_url = getattr(settings, "CHECKOUT_CANCEL_URL", "https://example.com/billing/cancel")
        session = stripe_svc.cancel_and_checkout_full_price_upgrade(request.user, sub, new_plan, success_url, cancel_url)
        return Response({"url": session["url"], "id": session["id"]}, status=201)



class CancelMySubscriptionView(APIView):
    """
    POST /billing/me/subscriptions/<int:subscription_id>/cancel
    Body: { immediate?: boolean }  # default False => cancel at period end
    """
    permission_classes = [IsAuthed]

    def post(self, request, subscription_id: int):
        immediate = bool(request.data.get("immediate", False))
        try:
            sub = Subscription.objects.select_related("user").get(pk=subscription_id, user=request.user)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)

        try:
            stripe_svc.cancel_subscription(sub, at_period_end=(not immediate))
            # Audit transaction regardless of flow
            BillingTransaction.objects.create(
                user=request.user,
                subscription=sub,
                plan_content_type=sub.plan_content_type,
                plan_object_id=sub.plan_object_id,
                plan_name=str(sub.plan),
                kind="cancel",
                status="succeeded",
                amount=Decimal("0.00"),
                currency=getattr(sub.plan, "currency", "usd"),
                description="User-initiated cancellation",
                meta={"immediate": immediate},
            )
        except Exception as e:
            BillingTransaction.objects.create(
                user=request.user,
                subscription=sub,
                plan_content_type=sub.plan_content_type,
                plan_object_id=sub.plan_object_id,
                plan_name=str(sub.plan),
                kind="cancel",
                status="failed",
                amount=Decimal("0.00"),
                currency=getattr(sub.plan, "currency", "usd"),
                failure_message=str(e),
            )
            return Response({"detail": str(e)}, status=400)

        row = _my_component_row(sub, "support_agent" if isinstance(sub.plan, SupportAgentPlan) else "outbound_calling")
        return Response({"subscription": MySubscriptionSerializer(row).data}, status=200)


class BeginSubscriptionCheckoutView(APIView):
    permission_classes = [IsAuthed]

    class Input(drf_serializers.Serializer):
        component = drf_serializers.ChoiceField(choices=["support_agent", "outbound_calling", "bundle"])
        plan_id = drf_serializers.IntegerField()
        success_url = drf_serializers.URLField(required=False)
        cancel_url = drf_serializers.URLField(required=False)

    def post(self, request):
        ser = self.Input(data=request.data)
        ser.is_valid(raise_exception=True)
        component = ser.validated_data["component"]
        plan_id = ser.validated_data["plan_id"]

        if component == "support_agent":
            plan = SupportAgentPlan.objects.filter(pk=plan_id, is_active=True, is_trial=False).first()
        elif component == "outbound_calling":
            plan = OutboundCallingPlan.objects.filter(pk=plan_id, is_active=True, is_trial=False).first()
        else:
            plan = BundlePlan.objects.filter(pk=plan_id, is_active=True, is_trial=False).first()
        if not plan:
            return Response({"detail": "Invalid plan."}, status=400)

        ct_sa = ContentType.objects.get_for_model(SupportAgentPlan)
        ct_oc = ContentType.objects.get_for_model(OutboundCallingPlan)
        ct_bu = ContentType.objects.get_for_model(BundlePlan)
        active_statuses = ["trialing", "active", "incomplete", "past_due"]

        if isinstance(plan, BundlePlan):
            subs = Subscription.objects.filter(user=request.user, plan_content_type__in=[ct_sa, ct_oc],
                                               status__in=active_statuses).order_by("-started_at")
            for s in subs:
                try:
                    if not getattr(s.plan, "is_trial", False):
                        return Response({"detail": "You cannot purchase a Combo plan while a single plan is active."},
                                        status=409)
                except Exception:
                    continue
        else:
            subs = Subscription.objects.filter(user=request.user, plan_content_type=ct_bu,
                                               status__in=active_statuses).order_by("-started_at")
            for s in subs:
                try:
                    if not getattr(s.plan, "is_trial", False):
                        return Response({"detail": "You cannot purchase a single plan while a Combo plan is active."},
                                        status=409)
                except Exception:
                    continue

        success_url = ser.validated_data.get("success_url") or getattr(settings, "CHECKOUT_SUCCESS_URL",
                                                                       "https://example.com/billing/success")
        cancel_url = ser.validated_data.get("cancel_url") or getattr(settings, "CHECKOUT_CANCEL_URL",
                                                                     "https://example.com/billing/cancel")

        try:
            session = stripe_svc.create_checkout_session_for_subscription(request.user, plan, success_url, cancel_url)
            return Response({"url": session["url"], "id": session["id"]}, status=201)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)


class BeginTopUpCheckoutView(APIView):
    permission_classes = [IsAuthed]

    class Input(drf_serializers.Serializer):
        subscription_id = drf_serializers.IntegerField()
        component = drf_serializers.ChoiceField(choices=["support_agent", "outbound_calling"])
        minutes = drf_serializers.IntegerField(min_value=1)
        success_url = drf_serializers.URLField(required=False)
        cancel_url = drf_serializers.URLField(required=False)

    def post(self, request):
        ser = self.Input(data=request.data)
        ser.is_valid(raise_exception=True)
        sub_id = ser.validated_data["subscription_id"]
        component = ser.validated_data["component"]
        minutes = ser.validated_data["minutes"]
        try:
            sub = Subscription.objects.get(pk=sub_id, user=request.user)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found."}, status=404)
        now = timezone.now()
        plan = sub.plan
        is_trial_plan = bool(getattr(plan, "is_trial", False))
        period_active = bool(sub.current_period_end and sub.current_period_end >= now)
        cancel_at_end_flag = bool(sub.cancel_at_period_end and period_active and sub.status != "canceled")
        status_allows = sub.status in {"active", "trialing"} or cancel_at_end_flag
        is_topup_allowed = bool(status_allows and period_active and not is_trial_plan)
        if not is_topup_allowed:
            return Response({"detail": "Top-ups are not allowed for this subscription."}, status=409)
        success_url = ser.validated_data.get("success_url") or getattr(settings, "CHECKOUT_SUCCESS_URL", "https://example.com/billing/success")
        cancel_url = ser.validated_data.get("cancel_url") or getattr(settings, "CHECKOUT_CANCEL_URL", "https://example.com/billing/cancel")
        try:
            session = stripe_svc.create_checkout_session_for_topup(
                request.user, sub, component, minutes, success_url, cancel_url
            )
            return Response({"url": session["url"], "id": session["id"]}, status=201)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)



class BeginPortalUpdateConfirmView(APIView):
    permission_classes = [IsAuthed]

    class Input(drf_serializers.Serializer):
        subscription_id = drf_serializers.IntegerField()
        new_plan_id = drf_serializers.IntegerField()
        return_url = drf_serializers.URLField(required=False)

    def post(self, request):
        ser = self.Input(data=request.data)
        ser.is_valid(raise_exception=True)
        sub_id = ser.validated_data["subscription_id"]
        new_plan_id = ser.validated_data["new_plan_id"]
        try:
            sub = Subscription.objects.get(pk=sub_id, user=request.user)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
        if isinstance(sub.plan, SupportAgentPlan):
            new_plan = SupportAgentPlan.objects.filter(pk=new_plan_id, is_active=True, is_trial=False).first()
        elif isinstance(sub.plan, OutboundCallingPlan):
            new_plan = OutboundCallingPlan.objects.filter(pk=new_plan_id, is_active=True, is_trial=False).first()
        elif isinstance(sub.plan, BundlePlan):
            new_plan = BundlePlan.objects.filter(pk=new_plan_id, is_active=True, is_trial=False).first()
        else:
            new_plan = None
        if not new_plan:
            return Response({"detail": "Invalid plan for this subscription."}, status=400)
        success_url = getattr(settings, "CHECKOUT_SUCCESS_URL", "https://example.com/billing/success")
        cancel_url = getattr(settings, "CHECKOUT_CANCEL_URL", "https://example.com/billing/cancel")
        session = stripe_svc.cancel_and_checkout_full_price_upgrade(request.user, sub, new_plan, success_url, cancel_url)
        return Response({"url": session["url"], "id": session["id"]}, status=201)
class CreateTestClockView(APIView):
    permission_classes = [IsAuthed]
    def post(self, request):
        frozen_time = int(request.data.get("frozen_time") or 0) or None
        clock = stripe_svc.create_test_clock(frozen_time=frozen_time)
        return Response({"id": clock["id"], "status": clock.get("status")}, status=201)
class AssignCustomerToClockView(APIView):
    permission_classes = [IsAuthed]
    def post(self, request):
        clock_id = request.data.get("clock_id")
        if not clock_id:
            return Response({"detail": "clock_id is required"}, status=400)
        cust_id = stripe_svc.reassign_customer_to_test_clock(request.user, clock_id)
        return Response({"customer_id": cust_id, "clock_id": clock_id}, status=200)
class AdvanceTestClockView(APIView):
    permission_classes = [IsAuthed]
    def post(self, request):
        clock_id = request.data.get("clock_id")
        to = request.data.get("to")
        if not clock_id or not to:
            return Response({"detail": "clock_id and to are required"}, status=400)
        out = stripe_svc.advance_test_clock(clock_id, int(to))
        return Response({"id": out["id"], "frozen_time": out.get("frozen_time")}, status=200)
class SetDefaultTestPaymentMethodView(APIView):
    permission_classes = [IsAuthed]
    def post(self, request):
        mode = str(request.data.get("mode") or "good")
        failing = mode.lower() == "fail"
        pm = stripe_svc.set_default_test_payment_method(request.user, failing=failing)
        return Response({"payment_method_id": pm["id"], "brand": pm["card"]["brand"], "last4": pm["card"]["last4"], "failing": failing}, status=200)

class BannerStateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        upgrade_url = "https://vai.xob-webservices.com/Account?tab=1"

        active_q = (
            Subscription.objects.filter(
                user=request.user,
                status__in=["trialing", "active", "incomplete", "past_due"],
            )
            .filter(Q(current_period_end__isnull=True) | Q(current_period_end__gte=now))
            .order_by("-started_at")
        )

        # -----------------------------
        # Use Case 1: Trial Started
        # -----------------------------
        for sub in active_q:
            plan = sub.plan
            if getattr(plan, "is_trial", False):
                days_remaining = _days_until(sub.current_period_end)
                msg = f"You are using a free trial ({days_remaining} days remaining)"
                return Response(
                    {"type": "trial_active", "message": msg, "action_url": upgrade_url, "closable": False},
                    status=200,
                )

        # ----------------------------------------------------
        # Use Case 4: No Minutes Left (active sub, zero left)
        # ----------------------------------------------------
        if active_q.exists():
            no_minutes_left_globally = True
            for sub in active_q:
                try:
                    sub.initialize_or_rollover_usage_buckets()
                except Exception:
                    pass

                # Prefer declared components; fallback by plan type.
                try:
                    components = list(sub.plan.components().keys())
                except Exception:
                    components = []
                if not components:
                    if isinstance(sub.plan, SupportAgentPlan):
                        components = ["support_agent"]
                    elif isinstance(sub.plan, OutboundCallingPlan):
                        components = ["outbound_calling"]
                    else:
                        components = ["support_agent", "outbound_calling"]

                # If ANY active component has minutes or is unlimited, we do NOT show the no-minutes banner.
                for comp in components:
                    bucket = sub.get_active_bucket(comp)
                    if bucket and bucket.unlimited:
                        no_minutes_left_globally = False
                        break
                    if sub.component_remaining_seconds(comp) > 0:
                        no_minutes_left_globally = False
                        break

                if not no_minutes_left_globally:
                    break

            if no_minutes_left_globally:
                return Response(
                    {
                        "type": "no_minutes",
                        "message": "You have no remaining minutes in your account.",
                        "action_url": upgrade_url,
                        "closable": True,  # can be X-closed
                    },
                    status=200,
                )

        # ---------------------------------------------------------
        # No active subscriptions → Trial Ended OR Expired Sub
        # ---------------------------------------------------------
        if not active_q.exists():
            ended_q = (
                Subscription.objects.filter(user=request.user)
                .order_by("-ended_at", "-current_period_end", "-updated_at")
            )
            for sub in ended_q:
                ended_dt = sub.ended_at or sub.current_period_end
                if ended_dt and ended_dt <= now:
                    if getattr(sub.plan, "is_trial", False):
                        # Use Case 2: Trial Ended
                        return Response(
                            {
                                "type": "trial_ended_no_plan",
                                "message": "Your free trial has ended. Please upgrade to continue using our services.",
                                "action_url": upgrade_url,
                                "closable": False,  # sticky
                            },
                            status=200,
                        )
                    else:
                        # Use Case 3: Expired Subscription (recent)
                        if now - ended_dt <= timedelta(days=7):
                            dt = timezone.localtime(ended_dt)
                            date_str = dt.strftime("%b %d, %Y").replace(" 0", " ")
                            msg = f"Your subscription expired on {date_str}. Renew to regain access."
                            return Response(
                                {
                                    "type": "expired_recent",
                                    "message": msg,
                                    "action_url": upgrade_url,
                                    "closable": True,  # can be X-closed
                                },
                                status=200,
                            )
                    break

        # Nothing to show
        return Response({"type": "none", "message": "", "action_url": upgrade_url, "closable": False}, status=200)
class MyTransactionsView(APIView):
    permission_classes = [IsAuthed]

    def get(self, request):
        qs = BillingTransaction.objects.filter(user=request.user).order_by("-created_at")
        component = request.query_params.get("component")
        if component in {"support_agent", "outbound_calling", "bundle"}:
            ct_map = {
                "support_agent": ContentType.objects.get_for_model(SupportAgentPlan),
                "outbound_calling": ContentType.objects.get_for_model(OutboundCallingPlan),
                "bundle": ContentType.objects.get_for_model(BundlePlan),
            }
            qs = qs.filter(plan_content_type=ct_map[component])
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            try:
                d = datetime.strptime(date_from, "%Y-%m-%d").date()
                start_dt = timezone.make_aware(datetime.combine(d, datetime.min.time()))
                qs = qs.filter(created_at__gte=start_dt)
            except Exception:
                pass
        if date_to:
            try:
                d = datetime.strptime(date_to, "%Y-%m-%d").date()
                end_dt = timezone.make_aware(datetime.combine(d, datetime.max.time()))
                qs = qs.filter(created_at__lte=end_dt)
            except Exception:
                pass
        page = int(request.query_params.get("page") or 1)
        page = 1 if page < 1 else page
        page_size = int(request.query_params.get("page_size") or 10)
        page_size = 1 if page_size < 1 else (100 if page_size > 100 else page_size)
        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        items = qs[start:end]
        data = BillingTransactionSerializer(items, many=True).data
        return Response({"count": total, "page": page, "page_size": page_size, "results": data}, status=200)
class DailyUsageView(APIView):
    """
    GET /billing/me/usage/daily

    Returns 'This Week' and 'Last Week' as 7-day rolling windows anchored to *today*:
      - This Week  : [today-6 ... today]
      - Last Week  : [today-13 ... today-7]
    Days/labels are dynamic; the last label in "This Week" is always today's weekday.

    Response:
    {
      "this_week": {
        "start_date": "YYYY-MM-DD",
        "end_date": "YYYY-MM-DD",
        "labels": ["WED","THU","FRI","SAT","SUN","MON","TUE"],  # example if today is Tuesday
        "support_agent": [m0,...,m6],
        "outbound_calling": [m0,...,m6]
      },
      "last_week": { ...same shape... }
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Work in local time for day boundaries
        now_local = timezone.localtime(timezone.now())
        today = now_local.date()

        # Rolling 7-day windows
        start_this = today - timedelta(days=6)   # inclusive
        start_last = today - timedelta(days=13)  # inclusive
        end_exclusive = today + timedelta(days=1)  # exclusive upper bound for all events

        # Build aware datetimes at local midnight
        start_dt = timezone.make_aware(datetime.combine(start_last, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_exclusive, datetime.min.time()))

        # Fetch both components for 14-day span
        qs = (
            UsageEvent.objects
            .filter(
                subscription__user=request.user,
                component__in=["support_agent", "outbound_calling"],
                at_time__gte=start_dt,
                at_time__lt=end_dt,
            )
            .only("component", "seconds", "at_time")
        )

        # Accumulate seconds per local date per component
        by_date = defaultdict(lambda: {"support_agent": 0, "outbound_calling": 0})
        for ev in qs:
            d = timezone.localtime(ev.at_time).date()
            by_date[d][ev.component] += int(ev.seconds or 0)

        def labels_for(start_date):
            # Build 7 labels from start_date..start_date+6, using local weekday short names (uppercased)
            return [
                (start_date + timedelta(days=i)).strftime("%a").upper()
                for i in range(7)
            ]

        def build_week_payload(start_date):
            sa, oc = [], []
            for i in range(7):
                day = start_date + timedelta(days=i)
                bucket = by_date.get(day, {"support_agent": 0, "outbound_calling": 0})
                # minutes spent (truncate fractional minutes)
                sa.append(bucket["support_agent"] // 60)
                oc.append(bucket["outbound_calling"] // 60)
            return {
                "start_date": str(start_date),
                "end_date": str(start_date + timedelta(days=6)),
                "labels": labels_for(start_date),
                "support_agent": sa,
                "outbound_calling": oc,
            }

        data = {
            "this_week": build_week_payload(start_this),
            "last_week": build_week_payload(start_last),
        }
        return Response(data, status=200)


def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, n: int) -> date:
    # add n months to date d (first-of-month safe)
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _month_list(start_month: date, end_month_inclusive: date) -> list[date]:
    # list of first-of-month dates inclusive
    out = []
    d = start_month
    while d <= end_month_inclusive:
        out.append(d)
        d = _add_months(d, 1)
    return out


def _month_bounds_aware(local_month: date):
    # returns (start_dt_aware, end_dt_aware) for the given month in local tz
    tz = timezone.get_current_timezone()
    start_naive = datetime.combine(local_month, datetime.min.time())
    last_day = monthrange(local_month.year, local_month.month)[1]
    after_naive = datetime.combine(date(local_month.year, local_month.month, last_day) + timedelta(days=1), datetime.min.time())
    return timezone.make_aware(start_naive, tz), timezone.make_aware(after_naive, tz)


class MetricsOverviewView(APIView):
    """
    GET /billing/metrics/overview
    {
      "active_users": 123,
      "new_users_this_month": 10,
      "revenue_total": 12345.67,
      "revenue_monthly_avg": 999.99,
      "currency": "usd"
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        User = get_user_model()
        tz = timezone.get_current_timezone()
        now_local = timezone.localtime(timezone.now(), tz)
        first_this_month = _first_of_month(now_local.date())
        first_this_month_dt = timezone.make_aware(datetime.combine(first_this_month, datetime.min.time()), tz)

        active_users = User.objects.filter(is_active=True).count()
        new_users_this_month = User.objects.filter(is_active=True, date_joined__gte=first_this_month_dt).count()

        # Revenue total (all-time, USD, succeeded, amount > 0)
        revenue_qs = BillingTransaction.objects.filter(
            status="succeeded", amount__gt=0, currency="usd"
        )
        revenue_total = revenue_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")

        # Average monthly revenue since the first successful invoice
        first_invoice = revenue_qs.order_by("created_at").first()
        revenue_monthly_avg = Decimal("0.00")

        if first_invoice and revenue_total > 0:
            # Calculate months from first invoice to now
            first_invoice_date = timezone.localtime(first_invoice.created_at, tz).date()
            first_invoice_month = _first_of_month(first_invoice_date)
            current_month = _first_of_month(now_local.date())

            # Calculate number of months (inclusive)
            months_elapsed = 0
            temp_month = first_invoice_month
            while temp_month <= current_month:
                months_elapsed += 1
                temp_month = _add_months(temp_month, 1)

            if months_elapsed > 0:
                revenue_monthly_avg = revenue_total / Decimal(months_elapsed)

        return Response({
            "active_users": int(active_users),
            "new_users_this_month": int(new_users_this_month),
            "revenue_total": float(revenue_total),
            "revenue_monthly_avg": float(revenue_monthly_avg),
            "currency": "usd",
        }, status=200)


class MetricsUsageMonthlyView(APIView):
    """
    GET /billing/metrics/usage?window=last12|ytd

    Returns minutes per month for both components:
    {
      "labels": ["JAN", "FEB", ...],
      "support_agent": [..],
      "outbound_calling": [..]
    }
    """
    permission_classes = [IsAuthed]

    def get(self, request):
        window = str(request.query_params.get("window") or "last12").lower()
        tz = timezone.get_current_timezone()
        now_local = timezone.localtime(timezone.now(), tz)
        curr_first = datetime(now_local.year, now_local.month, 1)

        if window == "ytd":
            start = datetime(now_local.year, 1, 1)
        else:
            # last 12 months inclusive
            y, m = curr_first.year, curr_first.month - 11
            while m <= 0:
                m += 12
                y -= 1
            start = datetime(y, m, 1)

        # Build month sequence up to current month (inclusive)
        months = []
        y, m = start.year, start.month
        while (y < curr_first.year) or (y == curr_first.year and m <= curr_first.month):
            months.append(datetime(y, m, 1).date())
            m += 1
            if m > 12:
                m = 1
                y += 1

        start_dt = timezone.make_aware(datetime.combine(months[0], datetime.min.time()))
        next_month = (curr_first + timedelta(days=32)).replace(day=1)
        end_dt = timezone.make_aware(datetime.combine(next_month, datetime.min.time()))

        qs = (
            UsageEvent.objects
            .filter(
                at_time__gte=start_dt,
                at_time__lt=end_dt,
                component__in=["support_agent", "outbound_calling"],
            )
            .annotate(m=TruncMonth("at_time", tzinfo=tz))
            .values("m", "component")
            .annotate(total_seconds=Sum("seconds"))
        )

        by_month = {mo: {"support_agent": 0, "outbound_calling": 0} for mo in months}
        for row in qs:
            mo = timezone.localtime(row["m"], tz).date().replace(day=1)
            comp = row["component"]
            minutes = int((row["total_seconds"] or 0) // 60)
            if mo in by_month:
                by_month[mo][comp] = minutes

        labels = [m.strftime("%b").upper() for m in months]
        sa = [by_month[m]["support_agent"] for m in months]
        oc = [by_month[m]["outbound_calling"] for m in months]

        return Response({"labels": labels, "support_agent": sa, "outbound_calling": oc}, status=200)


class MetricsSubscriptionBreakdownView(APIView):
    """
    GET /billing/metrics/subscriptions
    -> { "bundles": 10, "support_agent": 20, "outbound_calling": 5 }
    Counts only active (non-trial) subscriptions with not-ended period.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        now = timezone.now()

        # Only count "active" status subscriptions (exclude trialing, incomplete, past_due)
        base = (
            Subscription.objects
            .filter(status="active")
            .filter(Q(current_period_end__isnull=False) | Q(current_period_end__gte=now))
        )

        ct_bu = ContentType.objects.get_for_model(BundlePlan)
        ct_sa = ContentType.objects.get_for_model(SupportAgentPlan)
        ct_oc = ContentType.objects.get_for_model(OutboundCallingPlan)

        # Filter out free trials by checking the actual plan objects
        def count_non_trial(content_type):
            subs = base.filter(plan_content_type=content_type)
            count = 0
            for sub in subs:
                try:
                    plan = sub.plan
                    if not getattr(plan, "is_trial", False):
                        count += 1
                except Exception:
                    continue
            return count

        bundles = count_non_trial(ct_bu)
        sa = count_non_trial(ct_sa)
        oc = count_non_trial(ct_oc)

        return Response({"bundles": bundles, "support_agent": sa, "outbound_calling": oc}, status=200)


class MetricsRevenueMonthlyView(APIView):
    """
    GET /billing/metrics/revenue?window=last12|ytd
    -> { "labels": ["JAN 25", ...], "values": [123.45,...], "currency":"usd" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        window = str(request.query_params.get("window") or "last12").lower()
        tz = timezone.get_current_timezone()
        now_local = timezone.localtime(timezone.now(), tz)
        this_month = _first_of_month(now_local.date())

        if window == "ytd":
            start_month = date(this_month.year, 1, 1)
        else:
            start_month = _add_months(this_month, -11)

        months = _month_list(start_month, this_month)
        start_dt, _ = _month_bounds_aware(start_month)
        _, end_dt = _month_bounds_aware(this_month)

        qs = (
            BillingTransaction.objects
            .filter(status="succeeded", amount__gt=0, currency="usd", created_at__gte=start_dt, created_at__lt=end_dt)
            .annotate(m=TruncMonth("created_at", tzinfo=tz))
            .values("m")
            .annotate(total=Sum("amount"))
        )
        by_month = {
            timezone.localtime(row["m"], tz).date().replace(day=1): (row["total"] or Decimal("0.00"))
            for row in qs
        }
        labels, values = [], []
        for m in months:
            labels.append(m.strftime("%b %y").upper())
            values.append(float(by_month.get(m, Decimal("0.00"))))

        return Response({"labels": labels, "values": values, "currency": "usd"}, status=200)