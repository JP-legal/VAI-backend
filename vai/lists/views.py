from django.db.models import Count, Value, IntegerField, ProtectedError, Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, permissions, filters, authentication, status
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import LeadList
from .serializers import LeadListSerializer, LeadMinimalSerializer
from rest_framework_simplejwt.authentication import JWTAuthentication

from ..campaigns.models import Campaign
from ..leads.models import Lead


class DefaultPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

class IsOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return getattr(obj, "owner_id", None) == request.user.id
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

class LeadListViewSet(viewsets.ModelViewSet):
    serializer_class = LeadListSerializer
    permission_classes = [IsOwner]
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    pagination_class = DefaultPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "country"]
    ordering_fields = ["name", "created_at", "updated_at", "leads_count", "campaigns_linked"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return (
            LeadList.objects
            .filter(owner=self.request.user)
            .annotate(
                leads_count=Count("leads", distinct=True),
                campaigns_linked=Count("campaigns", distinct=True),
            )
        )

    @action(detail=False, methods=["get"], url_path="countries")
    def countries(self, request):
        qs = (
            Lead.objects
            .filter(owner=request.user)
            .exclude(country="")
            .values("country")
            .annotate(count=Count("id"))
            .order_by("country")
        )
        data = [{"label": r["country"], "value": r["country"], "count": r["count"]} for r in qs]
        return Response(data)

    @action(detail=True, methods=["get"], url_path="leads")
    def leads(self, request, pk=None):
        lead_list = self.get_object()
        qs = lead_list.leads.filter(owner=request.user)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(country__icontains=search)
            )

        ordering = request.query_params.get("ordering") or "name"
        qs = qs.order_by(ordering)

        page = self.paginate_queryset(qs)
        serializer = LeadMinimalSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["delete"], url_path=r"leads/(?P<lead_id>\d+)")
    def remove_lead(self, request, pk=None, lead_id=None):
        lead_list = self.get_object()
        try:
            lead = lead_list.leads.get(id=lead_id, owner=request.user)
        except Lead.DoesNotExist:
            return Response({"detail": "Lead not found on this list."}, status=status.HTTP_404_NOT_FOUND)

        lead_list.leads.remove(lead)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="leads/available")
    def available_leads(self, request, pk):
        lead_list = LeadList.objects.get(id=pk)

        qs = (
            Lead.objects
            .filter(owner=request.user)
            .exclude(lists=lead_list)
        )

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(country__icontains=search) |
                Q(position__icontains=search) |
                Q(language__icontains=search)
            )
        ordering = request.query_params.get("ordering") or "name"
        qs = qs.order_by(ordering)

        page = self.paginate_queryset(qs)
        serializer = LeadMinimalSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["post"], url_path="leads/bulk_add")
    def bulk_add_leads(self, request, pk=None):
        lead_list = self.get_object()
        lead_ids = request.data.get("lead_ids") or []

        if not isinstance(lead_ids, list):
            return Response({"detail": "lead_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        cleaned_ids = []
        for x in lead_ids:
            try:
                cleaned_ids.append(int(x))
            except (TypeError, ValueError):
                pass

        if not cleaned_ids:
            return Response({"detail": "No valid lead ids provided."}, status=status.HTTP_400_BAD_REQUEST)

        to_add_qs = (
            Lead.objects
            .filter(owner=request.user, id__in=cleaned_ids)
            .exclude(lists=lead_list)
        )

        lead_list.leads.add(*list(to_add_qs))

        return Response(
            {"added": to_add_qs.count()},
            status=status.HTTP_201_CREATED
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        qs = Campaign.objects.filter(lead_list=instance)

        if qs.exists():
            sample = list(qs.only("id", "name", "status")[:50])
            return Response(
                {
                    "detail": "Cannot delete this list because it is used by one or more campaigns.",
                    "code": "lead_list_in_use",
                    "count": qs.count(),
                    "campaign_ids": [c.id for c in sample],
                    "campaigns": [f"{c.name} ({c.get_status_display()})" for c in sample],
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "detail": "Cannot delete this list because it is referenced by protected objects.",
                    "code": "protected_error",
                },
                status=status.HTTP_409_CONFLICT,
            )
