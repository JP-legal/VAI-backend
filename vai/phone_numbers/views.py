from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Q

from rest_framework import viewsets, permissions, filters, authentication, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import PhoneNumberRequest
from .serializers import (
    PhoneNumberRequestSerializer,
    AdminPhoneNumberSerializer,
    AdminPhoneNumberRequestSerializer,
    AdminApproveSerializer,
    AdminRejectSerializer,
    AdminAssignSerializer,
    AdminCreateNumberSerializer,
)
from .emails import (
    send_request_received_email,
    send_request_rejected_email,
    send_request_approved_email,
    send_number_assigned_email,
)
from ..lists.views import DefaultPagination


User = get_user_model()


# ------------------------ End-user permissions ------------------------
class IsOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return getattr(obj, "owner_id", None) == request.user.id

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


# ------------------------ End-user ViewSet (requests + manage own numbers) ------------------------
class PhoneNumberRequestViewSet(viewsets.ModelViewSet):
    """
    User-only surface:
     - list/create/delete own requests
     - enable/disable own numbers (but cannot enable when suspended by admin)
    """
    serializer_class = PhoneNumberRequestSerializer
    permission_classes = [IsOwner]
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    pagination_class = DefaultPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["country", "number", "status"]
    ordering_fields = ["country", "status", "created_at", "updated_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        # Users can see:
        #   a) numbers: number IS NOT NULL
        #   b) requests: number IS NULL AND status in (pending, rejected)
        # Approved requests are hidden from end users until a real number exists
        base = PhoneNumberRequest.objects.filter(owner=self.request.user)
        return base.filter(Q(number__isnull=False) | Q(number__isnull=True, status__in=[
            PhoneNumberRequest.Status.PENDING,
        ]))

    @transaction.atomic
    def perform_create(self, serializer):
        # User creates a **request** only (not a number)
        instance = serializer.save(
            owner=self.request.user,
            status=PhoneNumberRequest.Status.PENDING,
            number=None,
            provider_phone_id=None,
            rejection_reason=None,
            assigned_on=None,
        )
        send_request_received_email(self.request.user, instance)
        return instance

    def update(self, request, *args, **kwargs):
        return Response(status=405)

    def partial_update(self, request, *args, **kwargs):
        return Response(status=405)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        # Users can cancel only if it's still pending and not yet assigned a number
        instance = self.get_object()
        if not (instance.status == PhoneNumberRequest.Status.PENDING and instance.number is None):
            return Response(
                {"detail": "Only pending requests can be canceled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def enable(self, request, pk=None):
        instance: PhoneNumberRequest = self.get_object()
        if instance.number is None:
            return Response(
                {"detail": "Cannot enable a request; only phone numbers can be enabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instance.status == PhoneNumberRequest.Status.SUSPENDED:
            return Response(
                {"detail": "This number is suspended by an administrator."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if instance.status != PhoneNumberRequest.Status.DISABLED:
            return Response(
                {"detail": "Only disabled numbers can be enabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not instance.provider_phone_id:
            return Response(
                {"detail": "provider_phone_id is missing for this number."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.status = PhoneNumberRequest.Status.ENABLED
        instance.save(update_fields=["status", "updated_at"])
        data = self.get_serializer(instance).data
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def disable(self, request, pk=None):
        instance: PhoneNumberRequest = self.get_object()
        if instance.number is None:
            return Response(
                {"detail": "Cannot disable a request; only phone numbers can be disabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instance.status == PhoneNumberRequest.Status.SUSPENDED:
            return Response(
                {"detail": "This number is suspended by an administrator."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if instance.status != PhoneNumberRequest.Status.ENABLED:
            return Response(
                {"detail": "Only enabled numbers can be disabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.status = PhoneNumberRequest.Status.DISABLED
        instance.save(update_fields=["status", "updated_at"])
        data = self.get_serializer(instance).data
        return Response(data, status=status.HTTP_200_OK)


# ------------------------ Admin ViewSet ------------------------
class AdminPhoneNumberViewSet(viewsets.GenericViewSet):
    """
    Admin-only surface, mapped to the Angular admin UI:

      GET    /admin/phone-numbers                -> list numbers (assigned/unassigned)
      GET    /admin/phone-numbers/requests       -> list user requests (only pending, no number)

      POST   /admin/phone-numbers                -> add new number (+ optional assign)
      POST   /admin/phone-numbers/{id}/assign    -> assign/reassign number to a user
      POST   /admin/phone-numbers/{id}/enable    -> enable number
      POST   /admin/phone-numbers/{id}/disable   -> SUSPEND number   (admin lock)
      POST   /admin/phone-numbers/{id}/unsuspend -> move from suspended -> disabled

      POST   /admin/phone-numbers/{id}/requests/approve -> approve a request (NO number yet)
      POST   /admin/phone-numbers/{id}/requests/reject  -> reject with reason
    """
    permission_classes = [permissions.IsAdminUser]
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    pagination_class = DefaultPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["number", "country", "owner__username", "owner__email"]
    ordering_fields = ["number", "country", "status", "assigned_on", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return PhoneNumberRequest.objects.all()

    # -------- Lists --------
    def list(self, request):
        # Numbers tab: `number IS NOT NULL`
        qs = self.filter_queryset(self.get_queryset().exclude(number__isnull=True))
        page = self.paginate_queryset(qs)
        data = AdminPhoneNumberSerializer(page, many=True).data
        return self.get_paginated_response(data)

    @action(detail=False, methods=["get"], url_path="requests")
    def requests_list(self, request):
        # Requested Numbers tab: only PENDING requests with no number
        qs = self.filter_queryset(
            self.get_queryset().filter(number__isnull=True, status=PhoneNumberRequest.Status.PENDING)
        )
        page = self.paginate_queryset(qs)
        data = AdminPhoneNumberRequestSerializer(page, many=True).data
        return self.get_paginated_response(data)

    # -------- Create (Add New Number) --------
    @transaction.atomic
    def create(self, request):
        serializer = AdminCreateNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attrs = serializer.validated_data

        owner = None
        owner_id = attrs.pop("owner_id", None)
        if owner_id:
            owner = User.objects.get(id=owner_id)

        number_status = attrs.get("status") or PhoneNumberRequest.Status.DISABLED

        number_obj = PhoneNumberRequest.objects.create(
            owner=owner if owner else request.user,  # temporary owner if unassigned
            number=attrs["number"],
            country=attrs["country"],
            status=number_status,
            provider=attrs["provider"],
            provider_phone_id=attrs["provider_phone_id"],
            assigned_on=timezone.now() if owner else None,
            processed_by=request.user,
            rejection_reason=None,
        )

        # If an explicit owner was set (assign), email them and move ownership.
        if owner and owner != request.user:
            number_obj.owner = owner
            number_obj.save(update_fields=["owner"])
            send_number_assigned_email(owner, number_obj)

        return Response(AdminPhoneNumberSerializer(number_obj).data, status=201)

    # -------- Assignment / Reassignment --------
    @action(detail=True, methods=["post"], url_path="assign")
    @transaction.atomic
    def assign(self, request, pk=None):
        obj = PhoneNumberRequest.objects.get(pk=pk)
        if obj.number is None:
            raise serializers.ValidationError("You can only assign actual numbers (not requests).")

        payload = AdminAssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        new_owner = User.objects.get(id=payload.validated_data["user_id"])

        obj.owner = new_owner
        obj.assigned_on = timezone.now()
        obj.save(update_fields=["owner", "assigned_on", "updated_at"])

        # Email new owner
        send_number_assigned_email(new_owner, obj)

        return Response(AdminPhoneNumberSerializer(obj).data)

    # -------- Toggle status (numbers only) --------
    @action(detail=True, methods=["post"], url_path="enable")
    @transaction.atomic
    def enable(self, request, pk=None):
        obj = PhoneNumberRequest.objects.get(pk=pk)
        if obj.number is None:
            raise serializers.ValidationError("Cannot enable a request; only numbers can be enabled.")
        if not obj.provider_phone_id:
            raise serializers.ValidationError("provider_phone_id is required to enable a number.")
        obj.status = PhoneNumberRequest.Status.ENABLED
        obj.save(update_fields=["status", "updated_at"])
        return Response(AdminPhoneNumberSerializer(obj).data)

    @action(detail=True, methods=["post"], url_path="disable")
    @transaction.atomic
    def disable(self, request, pk=None):
        """
        Admin 'disable' acts as a SUSPEND (admin lock). Users cannot re-enable while suspended.
        """
        obj = PhoneNumberRequest.objects.get(pk=pk)
        if obj.number is None:
            raise serializers.ValidationError("Cannot disable a request; only numbers can be disabled.")
        obj.status = PhoneNumberRequest.Status.SUSPENDED
        obj.save(update_fields=["status", "updated_at"])
        return Response(AdminPhoneNumberSerializer(obj).data)
    @action(detail=False, methods=["get"], url_path="users")
    def users(self, request):
        """
        Lightweight user list for selects.
        Optional search: ?q=<text> matches username or email (case-insensitive).
        """
        q = (request.query_params.get("q") or "").strip()
        users_qs = User.objects.all()
        if q:
            users_qs = users_qs.filter(
                filters.Q(username__icontains=q) | filters.Q(email__icontains=q)
            )

        # keep it small; front-end selects don’t need all users
        users_qs = users_qs.order_by("id")[:200]
        results = []
        for u in users_qs:
            label = getattr(u, "username", None) or getattr(u, "email", None) or f"User #{u.id}"
            if getattr(u, "email", None) and getattr(u, "username", None):
                label = f"{u.username} ({u.email})"
            results.append({"id": u.id, "label": label})
        return Response({"results": results})

    @action(detail=True, methods=["post"], url_path="unsuspend")
    @transaction.atomic
    def unsuspend(self, request, pk=None):
        """
        Lift admin suspension; return the number to DISABLED (user may enable it afterwards).
        """
        obj = PhoneNumberRequest.objects.get(pk=pk)
        if obj.number is None:
            raise serializers.ValidationError("Only numbers can be unsuspended.")
        if obj.status != PhoneNumberRequest.Status.SUSPENDED:
            return Response({"detail": "Number is not suspended."}, status=400)
        obj.status = PhoneNumberRequest.Status.ENABLED
        obj.save(update_fields=["status", "updated_at"])
        return Response(AdminPhoneNumberSerializer(obj).data)

    # -------- Approve / Reject a specific request --------
    @action(detail=True, methods=["post"], url_path="requests/approve")
    @transaction.atomic
    def approve_request(self, request, pk=None):
        """
        Approves a pending request WITHOUT creating a number.
        - Sets status to APPROVED
        - Keeps number/provider fields empty
        - Sends 'approved' email
        - The record remains in DB but is hidden from both admin requests list and user lists
        """
        obj = PhoneNumberRequest.objects.get(pk=pk)
        if not (obj.number is None and obj.status == PhoneNumberRequest.Status.PENDING):
            return Response(
                {"detail": "Only pending requests can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = AdminApproveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        obj.status = PhoneNumberRequest.Status.APPROVED
        obj.assigned_on = None
        obj.processed_by = request.user
        obj.rejection_reason = None
        obj.save(update_fields=["status", "assigned_on", "processed_by", "rejection_reason", "updated_at"])

        send_request_approved_email(obj.owner, obj)
        return Response(AdminPhoneNumberRequestSerializer(obj).data, status=200)

    @action(detail=True, methods=["post"], url_path="requests/reject")
    @transaction.atomic
    def reject_request(self, request, pk=None):
        """
        Rejects a pending request with a reason and emails the requester.
        """
        obj = PhoneNumberRequest.objects.get(pk=pk)
        if not (obj.number is None and obj.status == PhoneNumberRequest.Status.PENDING):
            return Response(
                {"detail": "Only pending requests can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = AdminRejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        obj.status = PhoneNumberRequest.Status.REJECTED
        obj.rejection_reason = payload.validated_data["rejection_reason"]
        obj.processed_by = request.user
        obj.save(update_fields=["status", "rejection_reason", "processed_by", "updated_at"])

        send_request_rejected_email(obj.owner, obj)
        return Response(AdminPhoneNumberRequestSerializer(obj).data, status=200)
