from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import models

class VoiceProfile(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    display_name = models.CharField(max_length=120, default="My Voice")
    language = models.CharField(max_length=120, default="English")
    eleven_voice_id = models.CharField(max_length=64, blank=True, null=True)
    eleven_agent_id = models.CharField(max_length=64, blank=True, null=True)
    status = models.CharField(max_length=32, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class VoiceSample(models.Model):
    profile = models.ForeignKey(VoiceProfile, on_delete=models.CASCADE, related_name="samples")
    file = models.FileField(upload_to="voice_samples/")
    duration_seconds = models.FloatField(default=0)
    mime_type = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Agent(models.Model):
    profile = models.ForeignKey(VoiceProfile, on_delete=models.CASCADE, related_name="agents", null=True, blank=True)
    eleven_agent_id = models.CharField(max_length=64)
    config = models.JSONField(default=dict, blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class TempClone(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="temp_clones")
    file = models.FileField(upload_to="voice_temp/")
    language = models.CharField(max_length=8, default="en")
    display_name = models.CharField(max_length=120, default="")
    eleven_voice_id = models.CharField(max_length=64, blank=True, null=True)
    eleven_agent_id = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

from django.conf import settings

class CallSession(models.Model):
    profile = models.ForeignKey('voices.VoiceProfile', on_delete=models.CASCADE, related_name='call_sessions')
    embed = models.ForeignKey('voices.EmbeddableAgent', on_delete=models.SET_NULL, related_name='calls', null=True, blank=True)
    conversation_id = models.CharField(max_length=128, unique=True)
    agent_id = models.CharField(max_length=128, blank=True, null=True)
    user_display_name = models.CharField(max_length=255, default='Visitor', blank=True)
    status = models.CharField(max_length=32, default='completed', blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    transcript_text = models.TextField(blank=True, default='')
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    recording_url = models.URLField(blank=True, null=True)
    audio_file = models.FileField(upload_to="calls/support/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation_id"]),
            models.Index(fields=["started_at"]),
        ]
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"SupportCall({self.conversation_id})"


class CallTurn(models.Model):
    call = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name='turns')
    order = models.IntegerField()
    role = models.CharField(max_length=10)  # 'user' | 'agent'
    text = models.TextField(blank=True, default='')
    audio_file = models.FileField(upload_to='calls/turns/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)



import uuid


class EmbeddableAgent(models.Model):
    LANGUAGE_EN = "English"
    LANGUAGE_AR = "Arabic"
    LANGUAGE_CHOICES = (
        (LANGUAGE_EN, LANGUAGE_EN),
        (LANGUAGE_AR, LANGUAGE_AR),
    )

    id = models.BigAutoField(primary_key=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="embeds",
    )
    profile = models.ForeignKey(
        'voices.VoiceProfile',
        on_delete=models.CASCADE,
        related_name="embeds",
    )

    display_name = models.CharField(max_length=120, default="Website Assistant")
    website_origin = models.CharField(max_length=255)

    # Voice + prompt
    voice_id = models.CharField(max_length=128)
    prompt = models.TextField(blank=True, default="")

    # Persist the UI language as a readable label (English | Arabic)
    language = models.CharField(
        max_length=8,
        choices=LANGUAGE_CHOICES,
        default=LANGUAGE_EN,
    )

    # NEW: persist user-provided source links used to generate the prompt
    # Stored as a JSON array of URL strings
    source_links = models.JSONField(default=list, blank=True)

    # UI appearance + modes
    theme_color = models.CharField(max_length=32, blank=True, default="")
    font_family = models.CharField(max_length=255, blank=True, default="")
    modes = models.JSONField(default=dict, blank=True)

    # ElevenLabs
    eleven_agent_id = models.CharField(max_length=128, blank=True, default="")
    public_id = models.CharField(max_length=36, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "profile"],
                name="uniq_embed_per_owner_profile",
            )
        ]

    def ensure_public_id(self):
        if not self.public_id:
            self.public_id = str(uuid.uuid4())