from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, user_name="", **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        is_active = extra_fields.pop("is_active", True)
        email_verified = extra_fields.pop("email_verified", False)
        user = self.model(email=email, user_name=user_name, **extra_fields)
        user.set_password(password)
        user.is_active = is_active
        user.email_verified = email_verified
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, user_name="", **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("email_verified", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, user_name=user_name, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    user_name = models.CharField(max_length=150, blank=True)
    email_verified = models.BooleanField(default=False)
    outbound_calling = models.BooleanField(default=True)
    outbound_customization = models.BooleanField(default=False)
    cs_service_agent = models.BooleanField(default=True)
    cs_use_vai_database = models.BooleanField(default=False)
    stripe_customer_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    phone_number = models.CharField(max_length=32, blank=True, help_text="E.164 format, e.g. +14155550100")
    country = models.CharField(max_length=100, blank=True, help_text="ISO 3166-1 alpha-2, e.g. US")
    address = models.CharField(max_length=255, blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email