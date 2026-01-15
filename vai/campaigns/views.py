import os
from datetime import timedelta, datetime, time

from django.contrib.contenttypes.models import ContentType
from django.db.models.functions import Coalesce, Greatest
from django.http import FileResponse, HttpResponseRedirect
from django.utils import timezone
from django.db.models import Count, Q, OuterRef, F, IntegerField, Subquery, Sum
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import viewsets, mixins, permissions, filters, serializers
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from vai.billing.models import OutboundCallingPlan, Subscription, BundlePlan
from vai.campaigns.models import Campaign, CallLog, CampaignLead
from vai.campaigns.serializers import CampaignCreateSerializer, CampaignListSerializer, CampaignRenameSerializer, \
    CallLogDetailSerializer, CallLogListSerializer, CampaignDetailSerializer, CampaignLeadRowSerializer, \
    SupportCallDetailAdminSerializer, SupportCallListAdminSerializer, AdminCallLogRowSerializer
from vai.leads.views import StandardResultsSetPagination
from voices.models import Agent, VoiceProfile, CallSession
from vai.lists.models import LeadList
from vai.phone_numbers.models import PhoneNumberRequest  # adjust app label if needed
from vai.leads.models import Lead  # NEW: for V-AI DB filters

def _sentiment_from_score(score):
    if score is None:
        return None
    try:
        s = int(score)
    except Exception:
        return None
    if s > 7:
        return "positive"
    if s < 4:
        return "negative"
    return "neutral"

class IsOwner(permissions.BasePermission):
    permission_classes = [permissions.AllowAny]
    def has_object_permission(self, request, view, obj):
        return getattr(obj, "owner_id", None) == request.user.id

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class CampaignViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at", "status", "total_leads", "contacted_leads", "positive_leads"]
    ordering = ["-created_at"]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        if self.action == "create":
            from .serializers import CampaignCreateSerializer
            return CampaignCreateSerializer
        if self.action == "retrieve":
            from .serializers import CampaignDetailSerializer
            return CampaignDetailSerializer
        if self.action == "list":
            from .serializers import CampaignListSerializer
            return CampaignListSerializer
        if self.action in ["update", "partial_update"]:
            from .serializers import CampaignRenameSerializer
            return CampaignRenameSerializer
        from .serializers import CampaignCreateSerializer
        return CampaignCreateSerializer

    def update(self, request, *args, **kwargs):
        extra = set(request.data.keys()) - {"name"}
        if extra:
            raise serializers.ValidationError({"detail": "Only the name can be edited."})
        self._require_outbound(request.user)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        extra = set(request.data.keys()) - {"name"}
        if extra:
            raise serializers.ValidationError({"detail": "Only the name can be edited."})
        self._require_outbound(request.user)
        return super().partial_update(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        self._require_outbound(user)

        last_completed_score = (
            CallLog.objects
            .filter(
                campaign_id=OuterRef("campaign_id"),
                lead_id=OuterRef("lead_id"),
                status=CallLog.Status.COMPLETED,
            )
            .order_by("-created_at")
            .values("score")[:1]
        )

        positive_leads_subq = (
            CampaignLead.objects
            .filter(campaign_id=OuterRef("pk"))
            .annotate(last_score=Subquery(last_completed_score, output_field=IntegerField()))
            .filter(last_score__gt=7)
            .values("campaign_id")
            .annotate(cnt=Count("id"))
            .values("cnt")[:1]
        )

        total_duration_subq = (
            CallLog.objects
            .filter(campaign_id=OuterRef("pk"), status=CallLog.Status.COMPLETED)
            .values("campaign_id")
            .annotate(total=Coalesce(Sum("duration_seconds"), 0))
            .values("total")[:1]
        )

        return (
            Campaign.objects
            .filter(owner=user)
            .select_related("agent__profile", "phone_number", "lead_list")
            .annotate(
                total_leads=Count("campaign_leads", distinct=True),
                contacted_leads=Count(
                    "call_logs__lead_id",
                    filter=Q(call_logs__status__in=[CallLog.Status.ANSWERED, CallLog.Status.COMPLETED]),
                    distinct=True,
                ),
                positive_leads=Coalesce(Subquery(positive_leads_subq, output_field=IntegerField()), 0),
                total_duration_seconds=Coalesce(Subquery(total_duration_subq, output_field=IntegerField()), 0),
            )
        )

    def create(self, request, *args, **kwargs):
        self._require_outbound(request.user)
        use_vai = bool(request.data.get("use_vai_database", False))
        if use_vai and not self._can_use_vai_database(request.user):
            return Response({"detail": "Your plan does not include V-AI database"}, status=403)
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["get"], url_path="leads")
    def leads(self, request, pk=None):
        self._require_outbound(request.user)
        campaign = self.get_object()

        last_call = (CallLog.objects
                     .filter(campaign=campaign,
                             lead_id=OuterRef("lead_id"),
                             status=CallLog.Status.COMPLETED)
                     .order_by("-created_at"))

        from django.db.models.functions import Greatest
        from django.db.models import F
        from django.db.models.functions import Coalesce

        qs = (CampaignLead.objects
        .filter(campaign=campaign)
        .select_related("lead")
        .annotate(
            last_score=Subquery(last_call.values("score")[:1], output_field=IntegerField()),
            last_ended=Subquery(last_call.values("ended_at")[:1]),
            latest_call_id=Subquery(last_call.values("id")[:1], output_field=IntegerField()),
            last_interaction_at=Greatest(
                Coalesce(F("last_ended"), F("created_at")),
                Coalesce(F("last_contacted_at"), F("created_at")),
                Coalesce(F("last_attempted_at"), F("created_at")),
            ),
        ))

        q = request.query_params.get("search") or ""
        if q:
            qs = qs.filter(Q(lead__name__icontains=q) | Q(lead__phone_number__icontains=q))

        ordering = (request.query_params.get("ordering") or "").strip()
        if ordering:
            desc = ordering.startswith("-")
            key = ordering[1:] if desc else ordering
            mapping = {
                "lead_name": "lead__name",
                "phone_number": "lead__phone_number",
                "sentiment": "last_score",
                "engagement_score": "last_score",
                "last_interaction_at": "last_interaction_at",
            }
            field = mapping.get(key)
            if field:
                qs = qs.order_by(("-" if desc else "") + field)
        else:
            qs = qs.order_by("-last_interaction_at")

        page = self.paginate_queryset(qs)
        from .serializers import CampaignLeadRowSerializer
        serializer = CampaignLeadRowSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        self._require_outbound(request.user)
        campaign = self.get_object()
        campaign.start()
        return Response({"status": campaign.status, "started_at": campaign.started_at})

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        self._require_outbound(request.user)
        campaign = self.get_object()
        campaign.stop()
        return Response({"status": campaign.status, "ended_at": campaign.ended_at})

    def _require_outbound(self, user):
        if not self._has_active_outbound(user):
            raise serializers.ValidationError({"detail": "Outbound subscription required"}, code="permission_denied")

    def _has_active_outbound(self, user):
        now = timezone.now()
        active = ["trialing", "active", "incomplete", "past_due"]
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)
        bu_ct = ContentType.objects.get_for_model(BundlePlan)
        sub = Subscription.objects.filter(user=user, plan_content_type__in=[oc_ct, bu_ct], status__in=active).order_by("-started_at").first()
        if not sub:
            return False
        if sub.current_period_end and sub.current_period_end < now:
            return False
        return True

    def _can_use_vai_database(self, user):
        """
        Returns True if the user can use the V‑AI Database for outbound campaigns.
        Requires an active Outbound (or Bundle) subscription period.
        Allowed when EITHER the plan enables it OR the per-user admin toggle is True.
        """
        now = timezone.now()
        active = ["trialing", "active", "incomplete", "past_due"]
        oc_ct = ContentType.objects.get_for_model(OutboundCallingPlan)
        bu_ct = ContentType.objects.get_for_model(BundlePlan)

        sub = (
            Subscription.objects
            .filter(user=user, plan_content_type__in=[oc_ct, bu_ct], status__in=active)
            .order_by("-started_at")
            .first()
        )
        if not sub:
            return False
        if sub.current_period_end and sub.current_period_end < now:
            return False

        plan_flag = False
        comps = getattr(sub.plan, "components", None)
        if callable(comps):
            cfg = comps().get("outbound_calling", {}) or {}
            plan_flag = bool(cfg.get("can_use_vai_database", False))

        # Admin override toggle at the user level (UI label: "Use V-AI Database")
        user_flag = bool(getattr(user, "outbound_customization", False))

        return plan_flag or user_flag


class CampaignOptionsViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=["get"], url_path="agents")
    def agents(self, request):
        qs = Agent.objects.select_related("profile").filter(profile__owner=request.user, profile__status="ready")
        data = [{"id": a.id, "label": a.profile.display_name, "eleven_agent_id": a.eleven_agent_id} for a in qs]
        return Response({"results": data})

    @action(detail=False, methods=["get"], url_path="lists")
    def lists(self, request):
        q = request.query_params.get("search") or ""
        qs = LeadList.objects.filter(owner=request.user)
        if q:
            qs = qs.filter(name__icontains=q)
        data = [{"id": ll.id, "name": ll.name, "country": ll.country, "lead_count": ll.leads.count()} for ll in qs[:200]]
        return Response({"results": data})

    @action(detail=False, methods=["get"], url_path="phone-numbers")
    def phone_numbers(self, request):
        qs = PhoneNumberRequest.objects.filter(owner=request.user, status=PhoneNumberRequest.Status.ENABLED).exclude(number__isnull=True)
        data = [{"id": pn.id, "number": pn.number, "country": pn.country} for pn in qs]
        return Response({"results": data})

    @action(detail=False, methods=["get"], url_path="voices")
    def voices(self, request):
        profiles = (
            VoiceProfile.objects
            .filter(owner=request.user, eleven_voice_id__isnull=False)
            .order_by("eleven_voice_id", "-created_at")
            .only("id", "display_name", "eleven_voice_id", "status", "created_at")
        )

        seen = set()
        items = []
        for p in profiles:
            if p.eleven_voice_id in seen:
                continue
            seen.add(p.eleven_voice_id)
            items.append({
                "profile_id": p.id,
                "voice_id": p.eleven_voice_id,
                "label": p.display_name or f"Voice #{p.id}",
                "status": p.status,
                "is_default": False,
                "created_at": p.created_at.isoformat(),
            })

        items.sort(key=lambda x: x["label"].lower())

        default_id_en = os.getenv("ELEVEN_DEFAULT_VOICE_ID")
        default_id_ar = os.getenv("ELEVEN_DEFAULT_VOICE_ID_AR")
        default_voices = []
        if default_id_en:
            default_voices.append({
                "profile_id": None,
                "voice_id": default_id_en,
                "label": "Default Voice (English)",
                "status": "ready",
                "is_default": True,
                "created_at": None,
            })
        if default_id_ar:
            default_voices.append({
                "profile_id": None,
                "voice_id": default_id_ar,
                "label": "Default Voice (Arabic)",
                "status": "ready",
                "is_default": True,
                "created_at": None,
            })
        if default_voices:
            items = default_voices + items

        return Response({"results": items})

    @action(detail=False, methods=["get"], url_path="vai-industries")
    def vai_industries(self, request):
        values = (
            Lead.objects
            .filter(owner__isnull=True)
            .exclude(industry__isnull=True).exclude(industry="")
            .values_list("industry", flat=True)
            .distinct().order_by("industry")
        )
        return Response({"results": [{"name": v} for v in values]})

    @action(detail=False, methods=["get"], url_path="vai-countries")
    def vai_countries(self, request):
        values = (
            Lead.objects
            .filter(owner__isnull=True)
            .exclude(country__isnull=True).exclude(country="")
            .values_list("country", flat=True)
            .distinct().order_by("country")
        )
        return Response({"results": [{"name": v} for v in values]})

    @action(detail=False, methods=["get"], url_path="vai-positions")
    def vai_positions(self, request):
        values = (
            Lead.objects
            .filter(owner__isnull=True)
            .exclude(position__isnull=True).exclude(position="")
            .values_list("position", flat=True)
            .distinct().order_by("position")
        )
        return Response({"results": [{"name": v} for v in values]})

    @action(detail=False, methods=["get"], url_path="vai-count")
    def vai_count(self, request):
        industry = (request.query_params.get("industry") or "").strip()
        country = (request.query_params.get("country") or "").strip()
        position = (request.query_params.get("position") or "").strip()
        qs = Lead.objects.filter(owner__isnull=True)
        if industry:
            qs = qs.filter(industry__iexact=industry)
        if country:
            qs = qs.filter(country__iexact=country)
        if position:
            qs = qs.filter(position__iexact=position)
        return Response({"count": qs.count()})



class CallLogViewSet(viewsets.ReadOnlyModelViewSet):

    # permission_classes = [IsOwner]
    permission_classes = [permissions.AllowAny]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "campaign__name",
        "agent__profile__display_name",
        "phone_number__number",
        "status",
    ]
    ordering_fields = [
        "created_at",
        "started_at",
        "ended_at",
        "duration_seconds",
        "score",
        "campaign__name",
        "agent__profile__display_name",
        "phone_number__number",
        "status",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        return (
            CallLog.objects
            .filter(owner=user, status=CallLog.Status.COMPLETED)
            .select_related("campaign", "agent__profile", "phone_number")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        return CallLogDetailSerializer if self.action == "retrieve" else CallLogListSerializer

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        call = self.get_object()

        if call.recording_url:
            return HttpResponseRedirect(call.recording_url)

        if call.audio_file:
            filename = f"call-{call.id}.mp3"
            response = FileResponse(call.audio_file.open("rb"), as_attachment=True, filename=filename ,   content_type="audio/mpeg")
            return response

        return Response({"detail": "No recording available."}, status=404)


def _parse_date_bounds(request):
    """
    Accepts query params ?date_from=YYYY-MM-DD[THH:MM[:SS]]&date_to=...
    Returns aware datetimes (start of day / end of day for plain dates).
    """
    df = request.query_params.get("date_from")
    dt = request.query_params.get("date_to")

    def to_aware_start(s):
        if not s:
            return None
        dt_ = parse_datetime(s)
        if dt_ is None:
            d = parse_date(s)
            if not d:
                return None
            dt_ = datetime.combine(d, time.min)
        return timezone.make_aware(dt_) if timezone.is_naive(dt_) else dt_

    def to_aware_end(s):
        if not s:
            return None
        dt_ = parse_datetime(s)
        if dt_ is None:
            d = parse_date(s)
            if not d:
                return None
            dt_ = datetime.combine(d, time.max)
        return timezone.make_aware(dt_) if timezone.is_naive(dt_) else dt_

    return to_aware_start(df), to_aware_end(dt)


class IsAdmin(BasePermission):
    """
    """
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_staff)


class AdminCallLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Admin-only list + detail for ALL outbound call logs (any owner).
    GET /api/admin/outbound-calls
    GET /api/admin/outbound-calls/{id}
    GET /api/admin/outbound-calls/{id}/download
    """
    permission_classes = [IsAdmin]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "lead__name",
        "lead__phone_number",
        "campaign__name",
        "agent__profile__display_name",
        "phone_number__number",
        "owner__email",
        "status",
    ]
    ordering_fields = [
        "created_at",
        "started_at",
        "ended_at",
        "duration_seconds",
        "score",
        "campaign__name",
        "agent__profile__display_name",
        "phone_number__number",
        "status",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            CallLog.objects
            .all()
            .select_related("campaign", "lead", "agent__profile", "phone_number", "owner")
            .order_by("-created_at")
        )
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        smin = self.request.query_params.get("score_min")
        smax = self.request.query_params.get("score_max")
        if smin is not None:
            qs = qs.filter(score__gte=int(float(smin)))
        if smax is not None:
            qs = qs.filter(score__lte=int(float(smax)))
        date_from, date_to = _parse_date_bounds(self.request)
        if date_from:
            qs = qs.filter(started_at__gte=date_from)
        if date_to:
            qs = qs.filter(started_at__lte=date_to)
        owner_user_name = (self.request.query_params.get("owner_user_name") or "").strip()
        if owner_user_name:
            qs = qs.filter(owner__user_name__iexact=owner_user_name)
        return qs

    @action(detail=False, methods=["get"], url_path="owners")
    def owners(self, request):
        values = (
            CallLog.objects
            .values_list("owner__user_name", flat=True)
            .exclude(owner__user_name__isnull=True)
            .exclude(owner__user_name="")
            .distinct()
            .order_by("owner__user_name")
        )
        return Response({"results": [{"name": v} for v in values]})

    def get_serializer_class(self):
        return CallLogDetailSerializer if self.action == "retrieve" else AdminCallLogRowSerializer

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        call = self.get_object()
        if call.recording_url:
            return HttpResponseRedirect(call.recording_url)
        if call.audio_file:
            filename = f"call-{call.id}.mp3"
            return FileResponse(
                call.audio_file.open("rb"),
                as_attachment=True,
                filename=filename,
                content_type="audio/mpeg",
            )
        return Response({"detail": "No recording available."}, status=404)


class AdminSupportCallViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["conversation_id", "user_display_name", "status"]
    ordering_fields = ["started_at", "finished_at", "duration_seconds", "score", "status"]
    ordering = ["-started_at"]

    def get_queryset(self):
        qs = (
            CallSession.objects
            .select_related("profile", "profile__owner", "embed", "embed__owner")
            .filter(embed__isnull=False)
            .order_by("-started_at", "-id")
        )
        smin = self.request.query_params.get("score_min")
        smax = self.request.query_params.get("score_max")
        if smin is not None:
            qs = qs.filter(score__gte=int(float(smin)))
        if smax is not None:
            qs = qs.filter(score__lte=int(float(smax)))
        date_from, date_to = _parse_date_bounds(self.request)
        if date_from:
            qs = qs.filter(started_at__gte=date_from)
        if date_to:
            qs = qs.filter(started_at__lte=date_to)
        owner_user_name = (self.request.query_params.get("owner_user_name") or "").strip()
        if owner_user_name:
            qs = qs.filter(embed__owner__user_name__iexact=owner_user_name)
        return qs

    def get_serializer_class(self):
        return SupportCallDetailAdminSerializer if self.action == "retrieve" else SupportCallListAdminSerializer

    @action(detail=False, methods=["get"], url_path="owners")
    def owners(self, request):
        values = (
            CallSession.objects
            .filter(embed__isnull=False)
            .values_list("embed__owner__user_name", flat=True)
            .exclude(embed__owner__user_name__isnull=True)
            .exclude(embed__owner__user_name="")
            .distinct()
            .order_by("embed__owner__user_name")
        )
        return Response({"results": [{"name": v} for v in values]})

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        sess = self.get_object()
        if sess.recording_url:
            return HttpResponseRedirect(sess.recording_url)
        if sess.audio_file:
            filename = f"support-call-{sess.id}.mp3"
            return FileResponse(
                sess.audio_file.open("rb"),
                as_attachment=True,
                filename=filename,
                content_type="audio/mpeg",
            )
        return Response({"detail": "No recording available."}, status=404)
