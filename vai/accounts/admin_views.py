from django.contrib.auth import get_user_model
from rest_framework import filters, viewsets, status
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .serializers import AdminUserListSerializer, AdminUserUpdateSerializer

User = get_user_model()


class IsAdmin(BasePermission):
    """
    Custom permission to only allow admin users (is_staff=True).
    """
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_staff)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200


class AdminUserViewSet(viewsets.ModelViewSet):
    """
    Admin-only viewset for managing users.
    Provides list, retrieve, update, partial_update, destroy, and custom deactivate actions.
    """
    queryset = User.objects.all().order_by("-date_joined")
    authentication_classes = [JWTAuthentication, SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["email", "user_name"]
    ordering_fields = ["date_joined", "email", "user_name", "is_active", "is_staff", "last_login"]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return AdminUserUpdateSerializer
        return AdminUserListSerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if request.user.pk == instance.pk:
            return Response({"detail": "You cannot delete your own account."}, status=status.HTTP_400_BAD_REQUEST)
        if instance.is_superuser and User.objects.filter(is_superuser=True).count() == 1:
            return Response({"detail": "Cannot delete the last superuser."}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        u = self.get_object()
        u.is_active = False
        u.save(update_fields=["is_active"])
        return Response({"detail": "User deactivated."})