from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from ..models import VoiceProfile, VoiceSample
from ..serializers import VoiceProfileSerializer, VoiceSampleSerializer
from ..services.elevenlabs_service import delete_voice, create_instant_voice_clone
from ._helpers import (
    AUTH_CLASSES,
)
import os

from ..utils.audio import to_mp3


class ListMyVoices(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        profiles = VoiceProfile.objects.filter(owner=request.user, eleven_voice_id__isnull=False).order_by("-created_at")
        items = []
        for p in profiles:
            items.append({
                "profile_id": p.id,
                "voice_id": p.eleven_voice_id,
                "eleven_agent_id": p.eleven_agent_id,
                "label": p.display_name or f"Voice #{p.id}",
                "status": p.status,
                "is_default": False,
                "language": p.language,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            })
        default_id_en = os.getenv("ELEVEN_DEFAULT_VOICE_ID")
        default_id_ar = os.getenv("ELEVEN_DEFAULT_VOICE_ID_AR")
        if default_id_en:
            items.append({"profile_id": None, "voice_id": default_id_en, "label": "Default Voice (English)", "status": "ready", "is_default": True, "created_at": None})
        if default_id_ar:
            items.append({"profile_id": None, "voice_id": default_id_ar, "label": "Default Voice (Arabic)", "status": "ready", "is_default": True, "created_at": None})
        return Response(items, status=200)


class DeleteVoice(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @transaction.atomic
    def delete(self, request, profile_id):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        voice_id = profile.eleven_voice_id
        if voice_id:
            try:
                delete_voice(voice_id)
            except Exception:
                pass
        profile.eleven_voice_id = None
        profile.eleven_agent_id = None
        profile.status = "draft"
        profile.save(update_fields=["eleven_voice_id","eleven_agent_id","status"])
        return Response(status=204)


class EnsureProfile(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        display_name = (
            (request.data.get("display_name") or request.data.get("label") or "My Voice").strip()
        )
        language = (request.data.get("language") or "English").strip()
        profile = VoiceProfile.objects.create(
            owner=request.user,
            display_name=display_name,
            language=language,
            status="draft",
        )
        return Response(VoiceProfileSerializer(profile).data, status=201)


class UploadVoiceSample(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def post(self, request, profile_id: int):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        f = request.FILES.get("audio")
        if not f:
            return Response({"detail": "audio file is required"}, status=400)

        sample = VoiceSample.objects.create(
            profile=profile, file=f, mime_type=f.content_type or ""
        )
        try:
            if sample.file.name.endswith((".webm", ".ogg", ".m4a", ".wav")):
                mp3_path = to_mp3(sample.file.path)
                from django.core.files import File
                with open(mp3_path, "rb") as mp3f:
                    sample.file.delete(save=False)
                    sample.file.save(os.path.basename(mp3_path), File(mp3f), save=True)
        except Exception:
            pass

        return Response(VoiceSampleSerializer(sample).data, status=201)


class CloneVoice(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    @transaction.atomic
    def post(self, request, profile_id: int):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        if profile.samples.count() == 0:
            return Response({"detail": "need at least 1 sample"}, status=400)

        display_name = (request.data.get("display_name") or profile.display_name).strip()
        language = (request.data.get("language") or profile.language or "English").strip()
        files = [s.file.path for s in profile.samples.all()]
        try:
            voice_id = create_instant_voice_clone(display_name, files)
        except Exception as e:
            profile.status = "error"
            profile.save(update_fields=["status"])
            return Response({"detail": f"clone failed: {e}"}, status=400)
        profile.language = language
        profile.display_name = display_name
        profile.eleven_voice_id = voice_id
        profile.status = "ready"
        profile.save(update_fields=["display_name", "eleven_voice_id","language",  "status"])
        return Response(VoiceProfileSerializer(profile).data, status=200)




class ResetVoice(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, profile_id):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        if profile.eleven_voice_id:
            try:
                delete_voice(profile.eleven_voice_id)
            except Exception as e:
                print(f"[ResetVoice] warn delete {profile.eleven_voice_id}: {e}")
        profile.eleven_voice_id = None
        profile.status = "draft"
        profile.save(update_fields=["eleven_voice_id", "status"])
        return Response({"ok": True, "status": "draft"}, status=200)


class ListVoices(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def get(self, request, profile_id: int):
        p = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        items = []
        if p.eleven_voice_id:
            items.append({"voice_id": p.eleven_voice_id, "label": p.display_name or "My Voice"})
        default_id_en = os.getenv("ELEVEN_DEFAULT_VOICE_ID")
        default_id_ar = os.getenv("ELEVEN_DEFAULT_VOICE_ID_AR")
        if default_id_en and default_id_en != p.eleven_voice_id:
            items.append({"voice_id": default_id_en, "label": "Default Voice (English)"})
        if default_id_ar and default_id_ar != p.eleven_voice_id:
            items.append({"voice_id": default_id_ar, "label": "Default Voice (Arabic)"})
        return Response(items, status=200)

