from __future__ import annotations

import base64
import json
import os
import secrets
from urllib.parse import urlparse

import requests
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ._helpers import (
    AUTH_CLASSES,
    ELEVEN_API,
    ELEVEN_KEY,
    _build_prompt_from_pages,
    _clean_origin,
    _cors,
    _fetch_many,
)
from ..models import EmbeddableAgent, VoiceProfile
from ..serializers import EmbeddableAgentSerializer
from ..services.elevenlabs_service import (
    create_speaking_agent,
    tts_to_mp3_bytes,
)
from vai.billing.models import Subscription, SupportAgentPlan, OutboundCallingPlan, BundlePlan
from django.contrib.contenttypes.models import ContentType

def _has_active_support_subscription(user):
    now = timezone.now()
    active = ["trialing", "active", "incomplete", "past_due"]
    sa_ct = ContentType.objects.get_for_model(SupportAgentPlan)
    bu_ct = ContentType.objects.get_for_model(BundlePlan)
    sub = Subscription.objects.filter(user=user, plan_content_type__in=[sa_ct, bu_ct], status__in=active).order_by("-started_at").first()
    if not sub:
        return None
    if sub.current_period_end and sub.current_period_end < now:
        return None
    return sub

def _support_customizations_enabled(sub: Subscription | None):
    if not sub:
        return False
    return True
    # comps = getattr(sub.plan, "components", None)
    # if not callable(comps):
    #     return False
    # cfg = comps().get("support_agent", {})
    # return bool(cfg.get("customizations_enabled", False))

class PublicEmbedToken(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, public_id):
        from vai.billing.models import Subscription
        embed = get_object_or_404(EmbeddableAgent, public_id=public_id)
        origin = request.headers.get("Origin") or ""
        if _clean_origin(origin) != _clean_origin(embed.website_origin):
            return Response({"detail": "Origin not allowed"}, status=403)
        owner = embed.owner
        owner_id = getattr(owner, "id", None)
        buckets = []
        for s in Subscription.objects.filter(user=owner, status__in=["active", "trialing"]).select_related("plan_content_type"):
            try:
                if "support_agent" in s.plan.components():
                    s.initialize_or_rollover_usage_buckets()
                    b = s.get_active_bucket("support_agent")
                    if b:
                        buckets.append(b)
            except Exception:
                continue
        if not buckets:
            return Response({"detail": "Subscription inactive or no active bucket"}, status=403)
        if any(b.unlimited for b in buckets):
            pass
        else:
            remaining = sum(max(0, b.seconds_included - b.seconds_used) for b in buckets)
            if remaining <= 0:
                return Response({"detail": "Out of minutes"}, status=403)
        return Response(
            {
                "agentId": embed.eleven_agent_id,
                "themeColor": embed.theme_color or None,
                "fontFamily": embed.font_family or None,
            },
            status=200,
        )

class GeneratePromptFromLinks(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = AUTH_CLASSES

    def options(self, request, *args, **kwargs):
        resp = Response(status=204)
        origin = request.headers.get("Origin", "*")
        return _cors(resp, origin)

    def post(self, request):
        links = request.data.get("links") or []
        if not isinstance(links, list) or not links:
            return _cors(Response({"detail": "Provide a non-empty 'links' array."}, status=400), request.headers.get("Origin", "*"))

        display_name = (request.data.get("display_name") or "Website Assistant").strip()
        website_origin = (request.data.get("website_origin") or "").strip()
        language = (request.data.get("language") or "en").strip()
        voice_id = (request.data.get("voice_id") or "").strip() or None

        pages = _fetch_many(links)
        prompt_text = _build_prompt_from_pages(display_name, website_origin, language, pages)

        audio_b64 = None
        if voice_id:
            try:
                mp3 = tts_to_mp3_bytes(prompt_text[:2500], voice_id=voice_id)
                audio_b64 = base64.b64encode(mp3).decode("utf-8")
            except Exception:
                pass

        resp = Response(
            {
                "prompt": prompt_text,
                "audio_b64": audio_b64,
                "pages_seen": [{"url": p["url"], "chars": len(p["text"])} for p in pages],
                "generated_at": timezone.now().isoformat(),
            },
            status=200,
        )
        return _cors(resp, request.headers.get("Origin", "*"))

def _short_public_id() -> str:
    return secrets.token_urlsafe(10)

def _origin_normalize(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    u = urlparse(s)
    scheme = u.scheme or "https"
    hostport = u.netloc or u.path
    hostport = hostport.rstrip("/")
    return f"{scheme}://{hostport}".lower()

class GetEmbedByProfile(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def get(self, request, profile_id: int):
        sub = _has_active_support_subscription(request.user)
        if not sub:
            return Response({"detail": "Support subscription required"}, status=403)
        profile = get_object_or_404(VoiceProfile, pk=profile_id, owner=request.user)
        try:
            embed = EmbeddableAgent.objects.get(owner=request.user, profile=profile)
        except EmbeddableAgent.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        return Response(EmbeddableAgentSerializer(embed).data, status=200)

class SaveEmbed(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    class _SaveSerializer(serializers.Serializer):
        profile_id = serializers.IntegerField()
        display_name = serializers.CharField(max_length=120)
        website_origin = serializers.CharField(max_length=255)
        voice_id = serializers.CharField(max_length=128)
        prompt = serializers.CharField(allow_blank=True, default="")
        language = serializers.CharField(max_length=8, default="en")
        theme_color = serializers.CharField(required=False, allow_blank=True)
        font_family = serializers.CharField(required=False, allow_blank=True)
        modes = serializers.JSONField(required=False)
        source_links = serializers.ListField(child=serializers.CharField(allow_blank=False), required=False, allow_empty=True)

    def post(self, request):
        sub = _has_active_support_subscription(request.user)
        if not sub:
            return Response({"detail": "Support subscription required"}, status=403)
        data = self._SaveSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data

        profile = get_object_or_404(VoiceProfile, eleven_voice_id=v["voice_id"])
        origin = _origin_normalize(v["website_origin"])

        raw_links = v.get("source_links") or []
        cleaned_links = []
        seen = set()
        for s in raw_links:
            s = (s or "").strip()
            if not s:
                continue
            if "://" not in s:
                s = f"https://{s}"
            s_norm = s.rstrip("/")
            k = s_norm.lower()
            if k in seen:
                continue
            cleaned_links.append(s_norm)
            seen.add(k)

        embed = EmbeddableAgent.objects.filter(owner=request.user).first()
        created = False
        if embed is None:
            embed = EmbeddableAgent(
                owner=request.user,
                profile=profile,
                display_name=v["display_name"],
                website_origin=origin,
                voice_id=v["voice_id"],
                prompt=v["prompt"],
                theme_color=v.get("theme_color", "") or "",
                font_family=v.get("font_family", "") or "",
                modes=v.get("modes", {}) or {},
                eleven_agent_id="",
                public_id=_short_public_id(),
            )
            created = True
        else:
            if not _support_customizations_enabled(sub):
                incoming_theme = v.get("theme_color", "")
                incoming_font = v.get("font_family", "")
                incoming_modes = v.get("modes", {}) or {}
                if (incoming_theme and incoming_theme != embed.theme_color) or (incoming_font and incoming_font != embed.font_family) or (incoming_modes and incoming_modes != embed.modes):
                    return Response({"detail": "Your plan does not allow customizations"}, status=403)
            embed.profile = profile
            embed.display_name = v["display_name"]
            embed.website_origin = origin
            embed.voice_id = v["voice_id"]
            embed.prompt = v["prompt"]
            if _support_customizations_enabled(sub):
                embed.theme_color = v.get("theme_color", "") or ""
                embed.font_family = v.get("font_family", "") or ""
                embed.modes = v.get("modes", {}) or {}

        embed.source_links = cleaned_links

        from ..services.elevenlabs_service import (
            normalize_display_language,
            display_language_to_code,
            update_agent,
            create_speaking_agent,
        )

        display_lang = normalize_display_language(profile.language)
        lang_code = display_language_to_code(display_lang)
        embed.language = display_lang
        embed.save()

        first_message_en = "Hi! I’m your website assistant. I can help with sales and support questions."
        first_message_ar = "مرحبًا! أنا مساعد موقعك. أستطيع المساعدة في المبيعات ودعم العملاء."
        first_message = first_message_ar if lang_code == "ar" else first_message_en

        try:
            if embed.eleven_agent_id:
                update_agent(
                    agent_id=embed.eleven_agent_id,
                    voice_id=embed.voice_id,
                    language=lang_code,
                    first_message=first_message,
                    prompt=embed.prompt or "",
                )
            else:
                agent_id = create_speaking_agent(
                    voice_id=embed.voice_id,
                    name=embed.display_name,
                    first_message=first_message,
                    prompt=embed.prompt or "",
                    language=lang_code,
                )
                if agent_id and agent_id != embed.eleven_agent_id:
                    embed.eleven_agent_id = agent_id
                    embed.save(update_fields=["eleven_agent_id", "updated_at"])
        except Exception as e:
            return Response({"detail": f"Agent provisioning failed: {e}"}, status=502)

        kb_urls = []
        if origin:
            kb_urls.append(origin.rstrip("/"))
        kb_urls.extend([u.rstrip("/") for u in cleaned_links])
        kb_seen = set()
        kb_deduped = []
        for u in kb_urls:
            k = u.lower()
            if k in kb_seen:
                continue
            kb_seen.add(k)
            kb_deduped.append(u)
        for u in kb_deduped:
            r = requests.post(
                f"{ELEVEN_API}/v1/convai/agents/{embed.eleven_agent_id}/add-to-knowledge-base",
                headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
                json={"url": u},
                timeout=45,
            )

        return Response({"embed": EmbeddableAgentSerializer(embed).data, "created": created}, status=200)

class GetMyEmbed(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def get(self, request):
        sub = _has_active_support_subscription(request.user)
        if not sub:
            return Response({"detail": "Support subscription required"}, status=403)
        embed = EmbeddableAgent.objects.filter(owner=request.user).order_by("-updated_at", "-created_at").first()
        if not embed:
            return Response({"detail": "Not found."}, status=404)
        return Response(EmbeddableAgentSerializer(embed).data, status=200)

class GetEmbedByOwner(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = AUTH_CLASSES

    def get(self, request, owner_id: int):
        try:
            owner_id = int(owner_id)
        except (TypeError, ValueError):
            return Response({"detail": "Bad owner id."}, status=400)
        if request.user.id != owner_id:
            return Response({"detail": "Not found."}, status=404)
        sub = _has_active_support_subscription(request.user)
        if not sub:
            return Response({"detail": "Support subscription required"}, status=403)
        embed = EmbeddableAgent.objects.filter(owner_id=owner_id).order_by("-updated_at", "-created_at").first()
        if not embed:
            return Response({"detail": "Not found."}, status=404)
        return Response(EmbeddableAgentSerializer(embed).data, status=200)
