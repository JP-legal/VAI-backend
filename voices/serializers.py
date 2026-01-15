from rest_framework import serializers
from .models import VoiceProfile, VoiceSample, Agent, EmbeddableAgent, CallSession, CallTurn

class VoiceSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceSample
        fields = ["id", "file", "duration_seconds", "mime_type", "created_at"]

class VoiceProfileSerializer(serializers.ModelSerializer):
    samples = VoiceSampleSerializer(many=True, read_only=True)
    class Meta:
        model = VoiceProfile
        fields = ["id", "display_name", "language", "eleven_voice_id", "eleven_agent_id", "status", "samples", "created_at"]

LANGUAGE_MAP = {"en": "English", "ar": "Arabic", "fr": "French"}

class AgentSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    voice_id = serializers.SerializerMethodField()
    class Meta:
        model = Agent
        fields = ["id", "eleven_agent_id", "name", "language", "voice_id", "created_at", "updated_at"]
    def get_name(self, obj):
        return (obj.config or {}).get("name") or ""
    def get_language(self, obj):
        code = (obj.config or {}).get("language") or ""
        return LANGUAGE_MAP.get(code, code)
    def get_voice_id(self, obj):
        return (obj.config or {}).get("voice_id") or None

class CallTurnSerializer(serializers.ModelSerializer):
    audio_url = serializers.SerializerMethodField()
    class Meta:
        model = CallTurn
        fields = ("order", "role", "text", "audio_url", "created_at")
    def get_audio_url(self, obj):
        try:
            return obj.audio_file.url if obj.audio_file else None
        except Exception:
            return None

class SupportCallRowSerializer(serializers.ModelSerializer):
    date_iso = serializers.SerializerMethodField()
    class Meta:
        model = CallSession
        fields = ("id", "user_display_name", "started_at", "date_iso", "duration_seconds", "score")
    def get_date_iso(self, obj):
        return obj.started_at.isoformat() if obj.started_at else None

class SupportLogDetailSerializer(serializers.ModelSerializer):
    audio_url = serializers.SerializerMethodField()
    started_at = serializers.SerializerMethodField()
    finished_at = serializers.SerializerMethodField()
    class Meta:
        model = CallSession
        fields = [
            "id",
            "user_display_name",
            "started_at",
            "finished_at",
            "duration_seconds",
            "score",
            "transcript_text",
            "audio_url",
        ]
    def get_audio_url(self, obj):
        f = getattr(obj, "audio_file", None)
        if f and getattr(f, "name", None):
            try:
                return f.url
            except Exception:
                pass
        return getattr(obj, "recording_url", None)
    def get_started_at(self, obj):
        val = getattr(obj, "started_at", None)
        if val:
            return val
        return getattr(obj, "created_at", None)
    def get_finished_at(self, obj):
        val = getattr(obj, "finished_at", None)
        return val or None

class SupportCallListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallSession
        fields = [
            "id",
            "conversation_id",
            "user_display_name",
            "started_at",
            "finished_at",
            "duration_seconds",
            "score",
            "status",
        ]

class SupportCallDetailSerializer(SupportCallListSerializer):
    audio_url = serializers.SerializerMethodField()
    transcript_text = serializers.CharField(read_only=True)
    class Meta(SupportCallListSerializer.Meta):
        fields = SupportCallListSerializer.Meta.fields + ["audio_url", "transcript_text"]
    def get_audio_url(self, obj):
        request = self.context.get("request")
        if getattr(obj, "audio_file", None):
            return request.build_absolute_uri(obj.audio_file.url) if request else obj.audio_file.url
        if getattr(obj, "recording_url", None):
            return obj.recording_url
        return request.build_absolute_uri(f"/api/support/logs/{obj.id}/download/") if request else None
class EmbeddableAgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmbeddableAgent
        fields = [
            "id",
            "public_id",
            "display_name",
            "website_origin",
            "voice_id",
            "prompt",
            "language",
            "theme_color",
            "font_family",
            "source_links",
            "modes",
            "eleven_agent_id",
            "created_at",
            "updated_at",
        ]

