from django.conf import settings
from django.db import models
import uuid


class PhoneNumberRequest(models.Model):
    class Status(models.TextChoices):
        # Request workflow (no number yet)
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

        # Number lifecycle (has number)
        ENABLED = "enabled", "Enabled"
        DISABLED = "disabled", "Disabled"
        SUSPENDED = "suspended", "Suspended"  # admin lock

    class Provider(models.TextChoices):
        TWILIO = "twilio", "Twilio"
        SIP = "sip", "SIP Trunk"
        TELNYX = "telnyx", "Telnyx"
        VONAGE = "vonage", "Vonage"

    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)

    # For requests, this is the requester.
    # For numbers, this is always the **current assignee** (owner of the number).
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phone_number_requests",
    )

    # If NULL => it's a **request** record (not yet a number).
    # If set  => it's an **actual phone number** owned by `owner`.
    number = models.CharField(max_length=64, blank=True, null=True, unique=True)
    country = models.CharField(max_length=128)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )

    # ElevenLabs outbound plumbing
    provider = models.CharField(
        max_length=16, choices=Provider.choices, default=Provider.TWILIO
    )
    provider_phone_id = models.CharField(
        max_length=128, blank=True, null=True
    )  # ElevenLabs "agent_phone_number_id"

    # audit / workflow fields
    assigned_on = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="processed_phone_number_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["owner", "country"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["status"]),
            models.Index(fields=["number"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        label = self.number or f"request:{self.country}"
        return f"{self.owner} - {label} ({self.status})"
