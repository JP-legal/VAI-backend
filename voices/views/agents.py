from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from ..models import Agent, VoiceProfile, TempClone, EmbeddableAgent
from ..serializers import AgentSerializer, VoiceProfileSerializer
from ..services.elevenlabs_service import (
    get_conversation_token,
    get_or_create_default_agent,
    create_instant_voice_clone,
    create_agent,
    delete_voice,
    delete_agent,
    update_agent,
)
from rest_framework import status
from django.core.files import File
from ..utils.audio import to_mp3, to_wav
from ._helpers import (
    AUTH_CLASSES,
)
import os,re
def _unique_agent_name(user, base: str) -> str:
    base = (base or "Agent").strip()
    exists = set(
        Agent.objects.filter(profile__owner=user).values_list("config__name", flat=True)
    )
    if base not in exists:
        return base
    i = 2
    while True:
        cand = f"{base} ({i})"
        if cand not in exists:
            return cand
        i += 1

class DefaultCloneToken(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request):
        lang = (request.data.get("language") or "en").lower()
        agent_id = get_or_create_default_agent(lang)
        token = get_conversation_token(agent_id)
        return Response({"token": token, "agent_id": agent_id}, status=200)

class UploadTempCloneSample(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        f = request.FILES.get("audio")
        lang = (request.POST.get("language") or request.data.get("language") or "en").lower()
        if not f:
            return Response({"detail": "audio file is required"}, status=400)

        t = TempClone.objects.create(owner=request.user, file=f, language=lang)

        try:
            ext = os.path.splitext(t.file.name)[1].lower()
            if ext not in (".wav",):
                wav_path = to_wav(t.file.path)
                with open(wav_path, "rb") as wf:
                    t.file.delete(save=False)
                    t.file.save(os.path.basename(wav_path), File(wf), save=True)
        except Exception:
            pass

        return Response({"temp_id": t.id}, status=201)

class BuildCloneFromTemp(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @transaction.atomic
    def post(self, request):
        temp_id = request.data.get("temp_id")
        display_name = (request.data.get("display_name") or "My Voice").strip()
        language = (request.data.get("language") or "en").lower()
        t = get_object_or_404(TempClone, pk=temp_id, owner=request.user)
        voice_id = create_instant_voice_clone(display_name, [t.file.path])
        agent_name = _unique_agent_name(request.user, display_name)
        opener = "I’m now speaking in your cloned voice. Do you hear me clearly? Tell me how you’d like to use this voice." if language == "en" else "أنا الآن أتحدث بصوتك المستنسخ. هل تسمعني جيداً؟ أخبرني كيف تود استخدام هذا الصوت."
        agent_id = create_agent(voice_id, name=agent_name, first_message=opener, language=language)
        agent = Agent.objects.create(profile=None, eleven_agent_id=agent_id, config={"name": agent_name, "language": language, "voice_id": voice_id}, is_system=False)
        t.display_name = display_name
        t.eleven_voice_id = voice_id
        t.eleven_agent_id = agent_id
        t.save(update_fields=["display_name","eleven_voice_id","eleven_agent_id"])
        return Response({"temp_id": t.id, "voice_id": voice_id, "agent": AgentSerializer(agent).data}, status=200)

class DiscardTempClone(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @transaction.atomic
    def post(self, request):
        voice_id = request.data.get("voice_id")
        agent_id = request.data.get("agent_id")
        if agent_id:
            try:
                delete_agent(agent_id)
            except Exception:
                pass
            Agent.objects.filter(eleven_agent_id=agent_id, profile__isnull=True).delete()
        if voice_id:
            try:
                delete_voice(voice_id)
            except Exception:
                pass
        TempClone.objects.filter(owner=request.user, eleven_voice_id=voice_id, eleven_agent_id=agent_id).delete()
        return Response({"ok": True}, status=200)
def _reject_duplicate_profile_name(user, name: str, *, exclude_id: int | None = None):
    q = VoiceProfile.objects.filter(owner=user, display_name__iexact=(name or "").strip())
    if exclude_id:
        q = q.exclude(id=exclude_id)
    if q.exists():
        return Response(
            {"detail": f"A voice profile named “{name.strip()}” already exists."},
            status=status.HTTP_409_CONFLICT,
        )
    return None
class SaveFromStaged(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @transaction.atomic
    def post(self, request):
        display_name = (request.data.get("display_name") or "My Voice").strip()
        dup = _reject_duplicate_profile_name(request.user, display_name)
        if dup: return dup
        language = (request.data.get("language") or "en").lower()
        voice_id = request.data.get("voice_id")
        agent_id = request.data.get("agent_id")
        if not voice_id or not agent_id:
            return Response({"detail": "voice_id and agent_id are required"}, status=400)
        profile = VoiceProfile.objects.create(owner=request.user, display_name=display_name, language={"en":"English","ar":"Arabic"}.get(language, "English"), eleven_voice_id=voice_id, eleven_agent_id=agent_id, status="ready")
        Agent.objects.filter(eleven_agent_id=agent_id).update(profile=profile, config={"name": _unique_agent_name(request.user, display_name), "language": language, "voice_id": voice_id})
        return Response(VoiceProfileSerializer(profile).data, status=201)

class DeleteVoiceAndAgent(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @transaction.atomic
    def delete(self, request, profile_id: int):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        if EmbeddableAgent.objects.filter(owner=request.user, voice_id=profile.eleven_voice_id).exists():
            return Response({"detail": "voice is used by an embed agent"}, status=409)
        from vai.campaigns.models import Campaign
        if Campaign.objects.filter(owner=request.user, voice_profile=profile).exists():
            return Response({"detail": "voice is used by a campaign"}, status=409)
        if profile.eleven_agent_id:
            try:
                delete_agent(profile.eleven_agent_id)
            except Exception:
                pass
            Agent.objects.filter(profile=profile, eleven_agent_id=profile.eleven_agent_id).delete()
        if profile.eleven_voice_id:
            try:
                delete_voice(profile.eleven_voice_id)
            except Exception:
                pass
        profile.eleven_agent_id = None
        profile.eleven_voice_id = None
        profile.status = "draft"
        profile.save(update_fields=["eleven_agent_id","eleven_voice_id","status"])
        return Response({"ok": True}, status=200)

class SaveReclone(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, profile_id: int):
        from voices.models import Agent, EmbeddableAgent  # keep local to avoid import cycles

        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        new_voice_id = request.data.get("voice_id")
        new_agent_id = request.data.get("agent_id")  # still used as the profile's default agent id
        display_name = (request.data.get("display_name") or profile.display_name).strip()

        dup = _reject_duplicate_profile_name(request.user, display_name, exclude_id=profile.id)
        if dup:
            return dup

        language = (request.data.get("language") or "en").lower()
        if not new_voice_id or not new_agent_id:
            return Response({"detail": "voice_id and agent_id are required"}, status=400)

        old_voice_id = profile.eleven_voice_id
        old_agent_id = profile.eleven_agent_id

        # --- update the profile itself ---
        profile.display_name = display_name
        profile.language = {"en": "English", "ar": "Arabic"}.get(language, "English")
        profile.eleven_voice_id = new_voice_id
        profile.eleven_agent_id = new_agent_id
        profile.status = "ready"
        profile.save(update_fields=["display_name", "language", "eleven_voice_id", "eleven_agent_id", "status"])

        # optional: ensure we have a local row for the *profile's* default agent,
        # but DON'T attach this agent to existing campaigns.
        Agent.objects.get_or_create(
            eleven_agent_id=new_agent_id,
            defaults={
                "profile": profile,
                "config": {
                    "name": _unique_agent_name(request.user, display_name),
                    "language": language,
                    "voice_id": new_voice_id,
                },
            },
        )

        # --- update existing campaign agents in-place (do NOT swap the FK) ---
        from vai.campaigns.models import Campaign
        camps = (
            Campaign.objects.filter(owner=request.user, voice_profile=profile)
            .select_related("agent")
        )

        if camps.exists():
            fm_en = "Hello—quick one. I've got a 30-second idea. Is now a bad time?"
            fm_ar = "مرحبًا—سأكون سريعًا. لدي فكرة تستغرق 30 ثانية. هل هذا وقت غير مناسب؟"
            fm = fm_ar if language.startswith("ar") else fm_en

            for c in camps:
                agent = c.agent
                if not agent:  # defensive
                    continue

                # remote: update the existing ElevenLabs agent for this campaign
                try:
                    update_agent(agent.eleven_agent_id, new_voice_id, language, fm)
                except Exception:
                    # keep going; we'll still update local state
                    pass

                # local mirror: update Agent.config but keep the same Agent row
                cfg = dict(agent.config or {})
                cfg.update({"language": language, "voice_id": new_voice_id})
                agent.config = cfg
                agent.save(update_fields=["config"])
                # touch campaign timestamp but DO NOT change c.agent
                c.save(update_fields=["updated_at"])

        # --- update any embeddables that used the old voice ---
        embeds = EmbeddableAgent.objects.filter(owner=request.user, voice_id=old_voice_id)
        if embeds.exists():
            fm_en = "Hi! I’m your website assistant. I can help with sales and support questions."
            fm_ar = "مرحبًا! أنا مساعد موقعك. أستطيع المساعدة في المبيعات ودعم العملاء."
            efm = fm_ar if language.startswith("ar") else fm_en

            for e in embeds:
                try:
                    update_agent(e.eleven_agent_id, new_voice_id, language, efm)
                except Exception:
                    pass
                e.voice_id = new_voice_id
                e.language = language
                e.save(update_fields=["voice_id", "language", "updated_at"])

        # --- cleanup: old voice and old *profile* default agent if unused anywhere ---
        try:
            if old_voice_id and old_voice_id != new_voice_id:
                delete_voice(old_voice_id)
        except Exception:
            pass

        if old_agent_id and old_agent_id != new_agent_id:
            from vai.campaigns.models import Campaign as Cg
            in_use = (
                Cg.objects.filter(agent__eleven_agent_id=old_agent_id).exists()
                or EmbeddableAgent.objects.filter(eleven_agent_id=old_agent_id).exists()
            )
            if not in_use:
                try:
                    delete_agent(old_agent_id)
                    Agent.objects.filter(eleven_agent_id=old_agent_id).delete()
                except Exception:
                    pass

        return Response(VoiceProfileSerializer(profile).data, status=200)


class CreateAgent(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def post(self, request, profile_id: int):
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        use_default = bool(request.data.get("use_default_voice"))
        language = request.data.get("language", "en")
        name = request.data.get("name", "User Voice Agent")
        first_message = request.data.get("first_message", "Hello! Let's chat.")

        voice_id = None if use_default else profile.eleven_voice_id
        if not use_default and not voice_id:
            return Response({"detail": "voice not cloned yet"}, status=400)

        try:
            agent_id = create_agent(
                voice_id, name=name, first_message=first_message, language=language
            )
            agent = Agent.objects.create(
                profile=profile,
                eleven_agent_id=agent_id,
                config={"name": name, "language": language, "voice_id": voice_id},
            )
            return Response(AgentSerializer(agent).data, status=201)
        except Exception as e:
            return Response({"detail": str(e)}, status=502)


class CreateAgentWithVoice(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def post(self, request):
        voice_id = request.data.get("voice_id")
        if not voice_id:
            return Response({"detail": "voice_id is required"}, status=400)

        name = request.data.get("name") or "User Voice Agent"
        language = request.data.get("language") or "en"
        first_message = request.data.get("first_message") or "Hello! How can I help?"

        profile_id = request.data.get("profile_id")
        profile = (
            get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
            if profile_id
            else None
        )

        try:
            agent_id = create_agent(
                voice_id, name=name, first_message=first_message, language=language
            )
        except Exception as e:
            return Response({"detail": str(e)}, status=502)

        agent = Agent.objects.create(
            profile=profile,
            eleven_agent_id=agent_id,
            config={"name": name, "language": language, "voice_id": voice_id},
        )
        return Response(AgentSerializer(agent).data, status=201)


class ListMyAgents(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def get(self, request):
        qs = (
            Agent.objects.select_related("profile")
            .filter(profile__owner=request.user)
            .order_by("-created_at")
        )
        return Response(AgentSerializer(qs, many=True).data, status=200)


class ListMyAgentRows(APIView):
    """
    GET /api/my/agent-rows/
    Returns unified rows:
      - one row per Agent
      - one row per VoiceProfile with a voice id
      - plus a synthetic "default voice" row if env set
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rows = []

        qs = (Agent.objects
              .select_related("profile")
              .filter(profile__owner=request.user)
              .order_by("-updated_at", "-created_at", "-id"))

        for a in qs:
            cfg = a.config or {}
            raw_name = (cfg.get("name") or "").strip()
            if re.search(r"\(preview\)\s*$", raw_name, flags=re.IGNORECASE):
                continue
            clean_name = re.sub(r"\s*\(preview\)\s*$", "", raw_name, flags=re.IGNORECASE) or "Agent"
            lang = (cfg.get("language") or "—")
            rows.append({
                "_kind": "agent",
                "id": str(a.id) if a.id is not None else (a.eleven_agent_id or ""),
                "eleven_agent_id": a.eleven_agent_id or "",
                "voice_id": getattr(getattr(a, "profile", None), "eleven_voice_id", None),
                "profile_id": getattr(a, "profile_id", None),
                "code": clean_name,
                "name": lang,
                "created": a.created_at.isoformat() if a.created_at else None,
                "updated": a.updated_at.isoformat() if a.updated_at else (a.created_at.isoformat() if a.created_at else None),
            })

        from ..models import VoiceProfile
        vqs = (VoiceProfile.objects
               .filter(owner=request.user, eleven_voice_id__isnull=False)
               .only("id", "display_name", "eleven_voice_id", "status", "created_at")
               .order_by("-created_at"))

        for p in vqs:
            name = (p.display_name or "").strip()
            if re.search(r"\(preview\)\s*$", name, flags=re.IGNORECASE):
                continue
            clean = re.sub(r"\s*\(preview\)\s*$", "", name, flags=re.IGNORECASE)
            rows.append({
                "_kind": "voice",
                "profile_id": p.id,
                "voice_id": p.eleven_voice_id,
                "code": clean or f"Voice #{p.id}",
                "name": "—",
                "created": p.created_at.isoformat() if p.created_at else None,
                "updated": "—",
                "status": p.status,
                "is_default": False,
            })

        default_id = os.getenv("ELEVEN_DEFAULT_VOICE_ID")
        if default_id:
            rows.append({
                "_kind": "voice",
                "profile_id": None,
                "voice_id": default_id,
                "code": "Default Voice",
                "name": "—",
                "created": None,
                "updated": "—",
                "status": "ready",
                "is_default": True,
            })

        rows.sort(key=lambda r: (r.get("updated") or r.get("created") or ""), reverse=True)
        return Response(rows, status=200)


class GetConversationToken(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = AUTH_CLASSES

    def post(self, request, agent_id: str):
        _ = get_object_or_404(Agent, eleven_agent_id=agent_id)
        try:
            token = get_conversation_token(agent_id)
            return Response({"token": token}, status=200)
        except Exception as e:
            return Response({"detail": f"token error: {e}"}, status=502)
