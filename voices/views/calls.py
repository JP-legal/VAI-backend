from __future__ import annotations

import os

from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from ._helpers import AUTH_CLASSES
from ..models import VoiceProfile, CallSession, CallTurn
from ..serializers import VoiceProfileSerializer
from ..services.elevenlabs_service import (
    tts_to_mp3_bytes,
    build_agent_monologue_mp3,
    create_instant_voice_clone,
    transcribe_with_scribe_http,  # preferred HTTP STT
)
from ..utils.audio import to_mp3, to_wav_16k_mono
from ..services.reply_service import simple_ai_reply


class StartBackendCall(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def post(self, request, profile_id: int):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        language = request.data.get("language", "en")
        intro = {
            "en": "Hi! I’ll listen for about a minute. Start by saying your name and what you do.",
            "ar": "مرحباً! سأستمع لمدة دقيقة. ابدأ بذكر اسمك وما الذي تفعله.",
            "es": "¡Hola! Te escucharé por un minuto. Empieza con tu nombre y a qué te dedicas.",
            "fr": "Salut ! Je t’écoute pendant une minute. Commence par ton nom et ce que tu fais.",
        }.get(language, "Hi! I’ll listen for about a minute. Start by saying your name and what you do.")

        call = CallSession.objects.create(profile=profile, duration_seconds=60, status="pending")

        voice_id = os.getenv("ELEVEN_DEFAULT_VOICE_ID", None)
        try:
            mp3_bytes = build_agent_monologue_mp3(intro, voice_id=voice_id)
        except Exception as e:
            call.status = "failed"
            call.save(update_fields=["status"])
            return Response({"detail": f"{e}"}, status=502)

        fname = f"call_{call.id}_intro.mp3"
        call.audio_file.save(fname, ContentFile(mp3_bytes))
        call.status = "playing"
        call.save(update_fields=["audio_file", "status"])

        CallTurn.objects.create(call=call, order=0, role="agent", text=intro, audio_file=call.audio_file)

        audio_url = request.build_absolute_uri(call.audio_file.url)
        return Response(
            {
                "call_id": call.id,
                "audio_url": audio_url,
                "duration_seconds": call.duration_seconds,
                "turn": 0,
            },
            status=201,
        )


class UploadCallSample(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def post(self, request, call_id: int):
        call = get_object_or_404(CallSession, pk=call_id, profile__owner=request.user)
        profile = call.profile

        f = request.FILES.get("audio")
        if not f:
            return Response({"detail": "audio file is required"}, status=400)

        sample = profile.samples.create(file=f, mime_type=f.content_type or "")

        try:
            if sample.file.name.endswith((".webm", ".ogg", ".m4a", ".wav")):
                mp3_path = to_mp3(sample.file.path)
                from django.core.files import File
                with open(mp3_path, "rb") as mp3f:
                    sample.file.delete(save=False)
                    sample.file.save(os.path.basename(mp3_path), File(mp3f), save=True)
        except Exception:
            pass

        files = [s.file.path for s in profile.samples.all()] or [sample.file.path]
        try:
            voice_id = create_instant_voice_clone(profile.display_name, files)
        except Exception as e:
            return Response({"detail": f"clone failed: {e}"}, status=400)

        profile.eleven_voice_id = voice_id
        profile.status = "ready"
        profile.save(update_fields=["eleven_voice_id", "status"])

        call.status = "completed"
        call.completed_at = timezone.now()
        call.save(update_fields=["status", "completed_at"])

        return Response(VoiceProfileSerializer(profile).data, status=200)


class UploadCallChunk(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def post(self, request, call_id: int):
        call = get_object_or_404(CallSession, pk=call_id, profile__owner=request.user)
        if call.status not in ("pending", "playing"):
            return Response({"detail": "call not active"}, status=400)

        f = request.FILES.get("audio")
        if not f:
            return Response({"detail": "audio file is required"}, status=400)

        user_order = call.turns.count() + 1
        user_turn = CallTurn.objects.create(call=call, order=user_order, role="user")
        user_fname = f"call_{call.id}_turn_{user_order}_user.webm"
        user_turn.audio_file.save(user_fname, f)
        user_turn.save(update_fields=["audio_file"])

        lang = request.query_params.get("lang") or request.data.get("lang") or None

        user_text = ""
        try:
            wav_path = to_wav_16k_mono(user_turn.audio_file.path)
            tx = transcribe_with_scribe_http(wav_path, language_code=lang)
            user_text = (tx.get("text") or "").strip()
            user_turn.text = user_text
            user_turn.save(update_fields=["text"])
        except Exception:
            pass

        reply_text = simple_ai_reply(user_text)

        try:
            voice_id = os.getenv("ELEVEN_DEFAULT_VOICE_ID", None)
            mp3_bytes = tts_to_mp3_bytes(reply_text, voice_id=voice_id)
        except Exception as e:
            return Response({"detail": f"TTS failed: {e}"}, status=502)

        agent_order = call.turns.count() + 1
        agent_turn = CallTurn.objects.create(
            call=call, order=agent_order, role="agent", text=reply_text
        )
        agent_fname = f"call_{call.id}_turn_{agent_order}_agent.mp3"
        agent_turn.audio_file.save(agent_fname, ContentFile(mp3_bytes))
        agent_turn.save(update_fields=["audio_file"])

        audio_url = request.build_absolute_uri(agent_turn.audio_file.url)
        return Response(
            {"reply_text": reply_text, "audio_url": audio_url, "order": agent_order}, status=201
        )
