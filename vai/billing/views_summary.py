from datetime import datetime
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from .models import Subscription, SupportAgentPlan, OutboundCallingPlan, BundlePlan


class SubscriptionSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        active_statuses = ["trialing", "active", "incomplete", "past_due"]

        def pick(qs):
            return qs.filter(status__in=active_statuses).order_by("-started_at").first()

        sa_ct = ContentType.objects.get_for_model(SupportAgentPlan)
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)
        bu_ct = ContentType.objects.get_for_model(BundlePlan)

        sa_sub = pick(Subscription.objects.filter(user=request.user, plan_content_type=sa_ct))
        oc_sub = pick(Subscription.objects.filter(user=request.user, plan_content_type=oc_ct))
        bu_sub = pick(Subscription.objects.filter(user=request.user, plan_content_type=bu_ct))

        def component_row(sub: Subscription | None, component: str):
            """
            Returns plan-derived state for a given component.
            DOES NOT include admin overrides; we add those below so the response
            reflects final, effective capabilities.
            """
            if not sub:
                return dict(hasActive=False, minutesRemaining=0, unlimited=False, toggles={})
            if sub.current_period_end and sub.current_period_end < now:
                return dict(hasActive=False, minutesRemaining=0, unlimited=False, toggles={})

            # Ensure buckets exist for the current period to compute minutes left
            sub.initialize_or_rollover_usage_buckets()
            bucket = sub.get_active_bucket(component)

            seconds_remaining = 0
            unlimited = False
            if bucket:
                unlimited = bool(bucket.unlimited)
                if unlimited:
                    seconds_remaining = 10**9  # a very large number for "infinite"
                else:
                    seconds_remaining = max(0, bucket.seconds_included - bucket.seconds_used)

            comps = getattr(sub.plan, "components", None)
            toggles = {}
            if callable(comps):
                toggles = comps().get(component, {})

            return dict(
                hasActive=True,
                minutesRemaining=int(seconds_remaining // 60),
                unlimited=unlimited,
                toggles=toggles,
            )

        # Prefer bundle for both (if present), otherwise the single-component sub
        support_sub = bu_sub or sa_sub
        outbound_sub = bu_sub or oc_sub

        support = component_row(support_sub, "support_agent")
        outbound = component_row(outbound_sub, "outbound_calling")

        # --- Admin overrides stored on the user record ---
        # NOTE: Field names are historical but map as follows:
        #   - Support Agent customization  => user.cs_use_vai_database (admin toggle)
        #   - Outbound "Use V‑AI Database" => user.outbound_customization (admin toggle)
        support_admin_toggle = bool(getattr(request.user, "cs_use_vai_database", False))
        outbound_admin_toggle = bool(getattr(request.user, "outbound_customization", False))

        # --- Plan flags from the plan's declared components ---
        plan_support_customization = bool(support["toggles"].get("customizations_enabled", False))
        plan_outbound_vai_db = bool(outbound["toggles"].get("can_use_vai_database", False))

        # --- Effective capabilities (require active component AND (plan OR admin)) ---
        customizations_enabled_effective = bool(
            support["hasActive"] and (plan_support_customization or support_admin_toggle)
        )
        can_use_vai_db_effective = bool(
            outbound["hasActive"] and (plan_outbound_vai_db or outbound_admin_toggle)
        )

        return Response({
            "support": {
                "hasActive": support["hasActive"],
                "minutesRemaining": support["minutesRemaining"],
                "unlimited": support["unlimited"],
                # Support Agent → customization
                "customizationsEnabled": customizations_enabled_effective,
            },
            "outbound": {
                "hasActive": outbound["hasActive"],
                "minutesRemaining": outbound["minutesRemaining"],
                "unlimited": outbound["unlimited"],
                # Outbound → use_vai_database
                "canUseVaiDatabase": can_use_vai_db_effective,
            }
        }, status=200)
