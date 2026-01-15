from datetime import datetime

from django.conf import settings
from django.contrib.auth import (
    authenticate,
    login,
    logout,
    update_session_auth_hash,
    get_user_model,
)
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.timezone import make_aware
from django.utils import timezone
from urllib.parse import urljoin

from rest_framework import authentication, permissions, status
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken

from .serializers import (
    RegistrationSerializer,
    LoginSerializer,
    UserSerializer,
    SelfUpdateSerializer,
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    ResetPasswordConfirmSerializer,
)
from .helpers import assign_free_trial, send_verification_email
from .tokens import email_verification_token
from vai.billing.services import stripe as stripe_svc

User = get_user_model()


class RegisterAPI(APIView):
    """POST /api/accounts/register — Register a new user"""
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = serializer.save()

        try:
            stripe_svc.get_or_create_customer(user)
        except Exception:
            pass

        try:
            assign_free_trial(user)
        except Exception:
            pass

        send_verification_email(user)

        access = AccessToken.for_user(user)
        exp_dt = datetime.fromtimestamp(access["exp"])
        if timezone.is_naive(exp_dt):
            exp_dt = make_aware(exp_dt, timezone.get_current_timezone())

        return Response(
            {
                "detail": "Registration successful. Check your email to verify your account.",
                "user": UserSerializer(user).data,
                "token": str(access),
                "expires": exp_dt.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailAPI(APIView):
    """GET /api/accounts/verify-email/<uidb64>/<token>/"""
    permission_classes = [permissions.AllowAny]

    def get(self, request, uidb64: str, token: str):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except Exception:
            user = None

        if user and email_verification_token.check_token(user, token):
            if not user.email_verified:
                user.email_verified = True
                user.save(update_fields=["email_verified"])
            return Response({"detail": "Email verified."}, status=status.HTTP_200_OK)

        return Response({"detail": "Invalid or expired verification link."}, status=status.HTTP_400_BAD_REQUEST)


class SendVerificationEmailAPI(APIView):
    """POST /api/accounts/send-verification — Resend verification email"""
    authentication_classes = [
        JWTAuthentication,
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
    ]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [UserRateThrottle]

    def post(self, request):
        user = request.user

        if getattr(user, "email_verified", False):
            return Response(
                {"detail": "Email is already verified."},
                status=status.HTTP_200_OK,
            )

        send_verification_email(user)
        return Response({"detail": "Verification email sent."}, status=status.HTTP_200_OK)


class LoginAPI(APIView):
    """POST /api/accounts/login/ — Returns user + 1-week JWT"""
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]
        login(request, user)

        access = AccessToken.for_user(user)
        exp_dt = datetime.fromtimestamp(access["exp"])
        if timezone.is_naive(exp_dt):
            exp_dt = make_aware(exp_dt, timezone.get_current_timezone())

        return Response(
            {
                "detail": "Logged in.",
                "user": UserSerializer(user).data,
                "token": str(access),
                "expires": exp_dt.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


class AdminLoginAPI(APIView):
    """POST /api/auth/admin-login — Admin-only login, returns JWT"""
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]

        if not user.is_staff:
            return Response(
                {"detail": "Admin access only."},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)

        access = AccessToken.for_user(user)
        exp_dt = datetime.fromtimestamp(access["exp"])
        if timezone.is_naive(exp_dt):
            exp_dt = make_aware(exp_dt, timezone.get_current_timezone())

        return Response(
            {
                "detail": "Logged in.",
                "user": UserSerializer(user).data,
                "token": str(access),
                "expires": exp_dt.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


class LogoutAPI(APIView):
    """POST /api/accounts/logout/"""
    authentication_classes = [authentication.SessionAuthentication, authentication.BasicAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeAPI(APIView):
    """GET/PATCH /api/accounts/me — Current user"""
    authentication_classes = [
        JWTAuthentication,
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
    ]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)

    def patch(self, request):
        ser = SelfUpdateSerializer(instance=request.user, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        user = ser.save()
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)

    def put(self, request):
        return self.patch(request)


class ChangePasswordAPI(APIView):
    """POST /api/accounts/change-password/"""
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_pw = serializer.validated_data["new_password1"]
        request.user.set_password(new_pw)
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        return Response({"detail": "Password changed."}, status=status.HTTP_200_OK)


class ForgotPasswordAPI(APIView):
    """POST /api/accounts/forgot-password — Request password reset"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            user = None
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_link = urljoin(settings.FRONTEND_DOMAIN, f"/auth/password-reset?uidb64={uid}&token={token}")
            subject = "Reset your password"
            text_body = f"Reset your password using the link: {reset_link}"
            html_body = f"<p>Reset your password using the link below:</p><p><a href=\"{reset_link}\">{reset_link}</a></p>"
            send_mail(subject, text_body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True, html_message=html_body)
        return Response({"detail": "If that email exists, you'll receive a reset link shortly."}, status=status.HTTP_200_OK)


class ResetPasswordConfirmAPI(APIView):
    """POST /api/accounts/reset-password/<uidb64>/<token> — Confirm password reset"""
    permission_classes = [permissions.AllowAny]

    def post(self, request, uidb64: str, token: str):
        serializer = ResetPasswordConfirmSerializer(
            data={**request.data, "uidb64": uidb64, "token": token}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except Exception:
            user = None
        if not user or not default_token_generator.check_token(user, token):
            return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)
        new_pw = serializer.validated_data["new_password1"]
        user.set_password(new_pw)
        user.save(update_fields=["password"])
        return Response({"detail": "Password has been reset."}, status=status.HTTP_200_OK)