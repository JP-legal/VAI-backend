from __future__ import annotations

from django.utils.dateparse import parse_datetime, parse_date
from django.db.models import Q
from rest_framework import generics, permissions
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.http import FileResponse

from ._helpers import AUTH_CLASSES
from ..models import CallSession
from ..serializers import SupportCallRowSerializer, SupportCallDetailSerializer
from datetime import datetime, time, timedelta
from django.utils import timezone

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 200


class ListSupportLogs(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES
    serializer_class = SupportCallRowSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        u = self.request.user
        qs = (
            CallSession.objects.select_related("embed", "profile")
            .filter(embed__owner=u)
            .order_by("-finished_at", "-created_at", "-id")
        )
        name = (self.request.query_params.get("name") or "").strip()
        if name:
            qs = qs.filter(user_display_name__icontains=name)
        try:
            mn = float(self.request.query_params.get("min_score", "0"))
            qs = qs.filter(Q(score__gte=mn))
        except Exception:
            pass
        try:
            mx = float(self.request.query_params.get("max_score", "10"))
            qs = qs.filter(Q(score__lte=mx))
        except Exception:
            pass
        tz = timezone.get_current_timezone()
        for raw in self.request.query_params.getlist("dr"):
            try:
                rule, val = raw.split(":", 1)
                day = parse_date(val)
                if not day:
                    ddt = parse_datetime(val)
                    if not ddt:
                        continue
                    if timezone.is_naive(ddt):
                        ddt = timezone.make_aware(ddt, tz)
                    day = ddt.astimezone(tz).date()
                start = timezone.make_aware(datetime.combine(day, time.min), tz)
                end = start + timedelta(days=1)
                if rule == "is":
                    qs = qs.filter(started_at__gte=start, started_at__lt=end)
                elif rule == "is_not":
                    qs = qs.exclude(started_at__gte=start, started_at__lt=end)
                elif rule == "is_after":
                    qs = qs.filter(started_at__gte=end)
                elif rule == "is_before":
                    qs = qs.filter(started_at__lt=start)
            except Exception:
                continue
        ordering = (self.request.query_params.get("ordering") or "").strip()
        allowed = {
            "user_display_name", "started_at", "finished_at", "duration_seconds", "score",
            "-user_display_name", "-started_at", "-finished_at", "-duration_seconds", "-score"
        }
        if ordering in allowed:
            qs = qs.order_by(ordering, "-id")
        return qs


class SupportLogDetail(generics.RetrieveAPIView):
    """
    GET /api/support/logs/<call_id>/
    """
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES
    serializer_class = SupportCallDetailSerializer
    lookup_url_kwarg = "call_id"

    def get_queryset(self):
        u = self.request.user
        return (
            CallSession.objects.select_related("profile")
            .filter(Q(profile__owner=u) | Q(profile__isnull=True))
            .order_by("-finished_at", "-created_at", "-id")
        )


class SupportLogDownload(generics.RetrieveAPIView):
    """
    GET /api/support/logs/<call_id>/download/
    """
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES
    lookup_url_kwarg = "call_id"

    def get_queryset(self):
        u = self.request.user
        return (
            CallSession.objects.select_related("profile")
            .filter(Q(profile__owner=u) | Q(profile__isnull=True))
        )

    def get(self, request, *args, **kwargs):
        call = self.get_object()
        audio = getattr(call, "audio_file", None)
        if audio:
            return FileResponse(
                audio.open("rb"),
                as_attachment=True,
                filename=f"support-call-{call.id}.mp3",
                content_type="audio/mpeg",
            )
        return Response({"detail": "No recording available."}, status=404)
