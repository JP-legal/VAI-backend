from django.conf import settings
from django.db import models
from django.utils import timezone

class Campaign(models.Model):
    class Status(models.TextChoices):
        STARTED = "started", "Started"
        STOPPED = "stopped", "Stopped"
        COMPLETED = "completed", "Completed"
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=255)
    agent = models.ForeignKey('voices.Agent', on_delete=models.PROTECT, related_name='campaigns')
    voice_profile = models.ForeignKey('voices.VoiceProfile', on_delete=models.PROTECT, related_name='campaigns')
    lead_list = models.ForeignKey('lists.LeadList', on_delete=models.PROTECT, related_name='campaigns')
    phone_number = models.ForeignKey('phone_numbers.PhoneNumberRequest', on_delete=models.PROTECT, related_name='campaigns')

    prompt = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STOPPED)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    leads = models.ManyToManyField('leads.Lead', through='CampaignLead', related_name='campaigns')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("owner", "name")]
        indexes = [
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["owner", "name"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"

    def start(self):
        if self.status != Campaign.Status.STARTED:
            self.status = Campaign.Status.STARTED
            self.save(update_fields=["status", "started_at", "updated_at"])

    def stop(self):
        if self.status != Campaign.Status.STOPPED:
            self.status = Campaign.Status.STOPPED
            self.save(update_fields=["status", "ended_at", "updated_at"])
    def complete(self):  # NEW convenience helper
        if self.status != Campaign.Status.COMPLETED:
            self.status = Campaign.Status.COMPLETED
            self.ended_at = timezone.now()
            self.save(update_fields=["status", "ended_at", "updated_at"])


class CampaignLead(models.Model):
    class LeadStatus(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        POSITIVE = "positive", "Positive"
        NEGATIVE = "negative", "Negative"
        DNC = "dnc", "Do Not Call"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="campaign_leads")
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name="campaign_leads")
    status = models.CharField(max_length=16, choices=LeadStatus.choices, default=LeadStatus.NEW)

    # Attempts vs contacts
    call_count = models.IntegerField(default=0)
    last_attempted_at = models.DateTimeField(null=True, blank=True)      # NEW
    first_contacted_at = models.DateTimeField(null=True, blank=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("campaign", "lead")]
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["campaign", "updated_at"]),
            models.Index(fields=["campaign", "last_attempted_at"]),
        ]

    def __str__(self):
        return f"CampaignLead(campaign={self.campaign_id}, lead={self.lead_id}, status={self.status})"


class CallLog(models.Model):
    class Status(models.TextChoices):
        DISPATCHED = "dispatched", "Dispatched"
        RINGING = "ringing", "Ringing"
        NO_ANSWER = "no_answer", "No Answer"
        ANSWERED = "answered", "Answered"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="call_logs")
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="call_logs")
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name="call_logs")
    agent = models.ForeignKey('voices.Agent', on_delete=models.PROTECT, related_name="call_logs")
    phone_number = models.ForeignKey('phone_numbers.PhoneNumberRequest', on_delete=models.PROTECT, related_name="call_logs")

    provider = models.CharField(max_length=32, default="twilio")  # twilio|sip|telnyx|vonage etc.
    provider_conversation_id = models.CharField(max_length=128, blank=True, null=True)  # ElevenLabs conversation_id
    provider_call_id = models.CharField(max_length=128, blank=True, null=True)          # e.g., Twilio callSid

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DISPATCHED)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    recording_url = models.URLField(blank=True, null=True)
    audio_file = models.FileField(upload_to="calls/outbound/", blank=True, null=True)

    transcript_text = models.TextField(blank=True, default="")
    transcript_json = models.JSONField(blank=True, null=True)
    analysis = models.JSONField(blank=True, null=True)

    score = models.PositiveSmallIntegerField(null=True, blank=True)  # 1..10
    is_positive = models.BooleanField(null=True, blank=True)         # derived from score > 7

    # Error tracking for dispatch failures
    dispatch_error = models.TextField(blank=True, null=True)  # Quick error reference
    dispatch_attempts = models.PositiveSmallIntegerField(default=0)  # Number of task execution attempts

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["campaign", "created_at"]),
            models.Index(fields=["lead", "created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["provider_conversation_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"CallLog(campaign={self.campaign_id}, lead={self.lead_id}, status={self.status})"