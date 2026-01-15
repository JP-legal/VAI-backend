from django.db import models
from django.db.models import Q, UniqueConstraint
from django.core.validators import RegexValidator
from django.conf import settings

class Lead(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leads",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    position = models.CharField(max_length=255, blank=True)

    phone_number = models.CharField(
        max_length=16, blank=True,
        validators=[RegexValidator(r'^\+[1-9]\d{7,14}$', "Enter a valid E.164 number like +14155550123.")],
    )
    email = models.EmailField(blank=True)
    language = models.CharField(max_length=64, blank=True)
    company = models.CharField(max_length=255, blank=True)
    industry = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=128, blank=True)
    address = models.CharField(max_length=512, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["owner", "email"]),
        ]
        constraints = [
            UniqueConstraint(
                fields=["owner", "phone_number"],
                condition=~Q(phone_number=""),
                name="unique_owner_phone_number",
            ),
            UniqueConstraint(
                fields=["phone_number"],
                condition=Q(owner__isnull=True) & ~Q(phone_number=""),
                name="unique_unowned_phone_number",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.company})"