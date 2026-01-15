# accounts/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RegisterAPI,
    VerifyEmailAPI,
    LoginAPI,
    AdminLoginAPI,
    LogoutAPI,
    ChangePasswordAPI,
    ForgotPasswordAPI,
    ResetPasswordConfirmAPI,
    MeAPI,
    SendVerificationEmailAPI,
)
from .admin_views import AdminUserViewSet

app_name = "accounts"
router = DefaultRouter()
router.register("admin/users", AdminUserViewSet, basename="admin-users")
urlpatterns = [
 path("register", RegisterAPI.as_view(), name="register_api"),
 path("verify-email/<str:uidb64>/<str:token>", VerifyEmailAPI.as_view(), name="verify_email_api"),
 path("login", LoginAPI.as_view(), name="login_api"),
 path("admin-login", AdminLoginAPI.as_view(), name="admin_login_api"),
 path("logout", LogoutAPI.as_view(), name="logout_api"),
 path("change-password", ChangePasswordAPI.as_view(), name="change_password_api"),
 path("forgot-password", ForgotPasswordAPI.as_view(), name="forgot_password_api"),
 path("reset-password/<str:uidb64>/<str:token>", ResetPasswordConfirmAPI.as_view(), name="reset_password_confirm_api"),
 path("me", MeAPI.as_view(), name="me_api"),
 path("send-verification", SendVerificationEmailAPI.as_view(), name="send_verification_api"),  # <-- add this

 path("", include(router.urls)),

]
