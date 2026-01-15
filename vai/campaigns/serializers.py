# campaigns/serializers.py
from datetime import timedelta
from django.db import transaction
from django.db.models import Count, Q, OuterRef, Subquery, IntegerField
from django.utils import timezone
from rest_framework import serializers

from vai.campaigns.eleven import create_elevenlabs_agent
from vai.campaigns.models import Campaign, CampaignLead, CallLog
from vai.leads.views import _sentiment_from_score
from vai.lists.models import LeadList
from vai.phone_numbers.models import PhoneNumberRequest  # adjust app label if needed
from vai.leads.models import Lead  # NEW: used for V-AI DB filters
import os

BASE_SALES_PROMPT = """Role & Boundaries
- You are making polite outbound sales/qualification calls.
- Do not claim to be "AI," a "robot," or "automated." If asked directly, briefly acknowledge you're calling via an automated system for a sales team, then continue naturally.
- Keep introductions neutral; avoid personal names or specific company identifiers.

Goals (in order)
1) Gain permission to continue (≤10 seconds).
2) Ask 2–3 short discovery questions to assess fit.
3) Share one concise benefit statement (≤20 seconds).
4) Propose a clear next step (brief demo, send a one-pager, or schedule a call-back).
5) Exit politely if not a fit or not interested.

Voice & Delivery
- Warm, calm, confident; vary cadence and intonation; avoid monotone.
- Short sentences (≈7–14 words). Natural micro-pauses. No filler ("umm," "you know").
- One idea per turn. Aim for 6–12 seconds per speaking turn.
- Barge-in friendly: stop immediately if the other party starts speaking.

Openings (neutral; pick one)
- "Hello—thanks for picking up. I have a quick idea that could be useful. Is this a bad time?"
- "Hi there. If I share a 30-second overview, you can tell me if it's relevant. Sound good?"
- "Hello. I'm reaching out with something that might help your team. Do you have a minute?"

Discovery (ask 2–3 max; adapt freely)
- "How are you handling this today?"
- "What would you most like to improve over the next quarter?"
- "If you could get one quick win here, what would it be?"

Benefit Framing (general; no jargon)
- Express a single, practical outcome: save time, cut costs, improve reliability, or boost visibility.
- Tie the outcome to a near-term horizon ("in weeks, not months").

Next Step (offer one clear option)
- "Would a quick demo this week make sense, or should I send a one-pager first?"
- "I can share a brief summary by email or message—what's easiest?"
- "Happy to call back at a better time—what works for you?"

Objections (acknowledge + one question)
- "Totally understand. If timing's tight, should I send a short overview and circle back next week?"
- "Fair point. Would a 10-minute walkthrough help you evaluate fit faster?"
- "No worries. Would you prefer I follow up by message instead?"

Politeness & Compliance
- If asked to stop: "Understood—thanks for your time. Have a great day."
- Don't collect sensitive personal data. Don't make guarantees or exaggerated claims.
- Keep a positive close whether or not there's a next step.

Operational Guardrails
- Keep uninterrupted turns under 12 seconds; aim for ~40/60 talk/listen balance.
- Paraphrase lines to avoid sounding scripted; vary phrasing across calls.
- Use neutral greetings ("Hello," "Hi there") to fit any context.
- Do not disclose internal tooling unless asked; keep it brief and high-level.

[Dynamic variables available at runtime; use naturally in conversation]
- lead_id={{lead_id}}, lead_name={{lead_name}}, lead_company={{lead_company}},
  lead_position={{lead_position}}, lead_country={{lead_country}},
  campaign_id={{campaign_id}}, campaign_name={{campaign_name}}
"""

FIRST_MESSAGE = "Hello—quick one. I've got a 30-second idea. Is now a bad time?"

class CampaignDetailSerializer(serializers.ModelSerializer):
    # already present on list serializer
    phone_number = serializers.CharField(source="phone_number.number")
    agent_name = serializers.CharField(source="agent.profile.display_name")
    lead_list_name = serializers.CharField(source="lead_list.name", read_only=True)

    # counters for the top cards
    called_count = serializers.SerializerMethodField()
    answered_count = serializers.SerializerMethodField()
    sentiment_counts = serializers.SerializerMethodField()

    total_leads = serializers.IntegerField(read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id", "name", "status",
            "started_at", "ended_at",
            "prompt",
            "phone_number", "agent_name", "lead_list_name",
            "total_leads",
            "called_count", "answered_count",
            "sentiment_counts",
        ]

    def get_called_count(self, obj: Campaign) -> int:
        return CallLog.objects.filter(campaign=obj).count()

    def get_answered_count(self, obj: Campaign) -> int:
        return CallLog.objects.filter(
            campaign=obj,
            status__in=[CallLog.Status.ANSWERED, CallLog.Status.COMPLETED],
        ).count()

    def get_sentiment_counts(self, obj: Campaign) -> dict:

        last_call = (CallLog.objects
                     .filter(campaign=obj,
                             lead_id=OuterRef("lead_id"),
                             status=CallLog.Status.COMPLETED)
                     .order_by("-created_at")
                     .values("score")[:1])

        lead_scores = (CampaignLead.objects
                       .filter(campaign=obj)
                       .annotate(last_score=Subquery(last_call, output_field=IntegerField()))
                       .values_list("last_score", flat=True))

        pos = neg = neu = 0
        for s in lead_scores:
            sent = _sentiment_from_score(s)
            if sent == "positive":
                pos += 1
            elif sent == "negative":
                neg += 1
            elif sent == "neutral":
                neu += 1
        return {"positive": pos, "neutral": neu, "negative": neg}


class CampaignLeadRowSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    phone_number = serializers.CharField(source="lead.phone_number", read_only=True)
    sentiment = serializers.SerializerMethodField()
    engagement_score = serializers.SerializerMethodField()
    last_interaction_at = serializers.SerializerMethodField()
    latest_call_id = serializers.SerializerMethodField()

    class Meta:
        model = CampaignLead
        fields = ["id", "lead_name", "phone_number", "sentiment", "last_interaction_at", "engagement_score", "latest_call_id"]

    def _latest_completed_call(self, obj):
        return (CallLog.objects
                .filter(
                    campaign_id=obj.campaign_id,
                    lead_id=obj.lead_id,
                    status=CallLog.Status.COMPLETED,
                )
                .order_by("-created_at")
                .values("score", "ended_at")
                .first())

    def get_engagement_score(self, obj):
        annotated = getattr(obj, "last_score", None)
        if annotated is not None:
            return annotated
        last = self._latest_completed_call(obj)
        return (last or {}).get("score")

    def get_sentiment(self, obj):
        score = self.get_engagement_score(obj)
        return _sentiment_from_score(score)

    def get_last_interaction_at(self, obj):
        annotated = getattr(obj, "last_interaction_at", None)
        if annotated:
            return annotated
        last = self._latest_completed_call(obj)
        candidates = [
            last.get("ended_at") if last else None,
            obj.last_contacted_at,
            obj.last_attempted_at,
            obj.created_at,
        ]
        candidates = [c for c in candidates if c is not None]
        return max(candidates) if candidates else obj.created_at
    def get_latest_call_id(self, obj):
        annotated = getattr(obj, "latest_call_id", None)
        if annotated is not None:
            return annotated
        last = self._latest_completed_call(obj)
        return (last or {}).get("id")



class CampaignCreateSerializer(serializers.ModelSerializer):
    voice_profile_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    voice_id = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    lead_list_id = serializers.IntegerField(write_only=True, required=False)
    phone_number_id = serializers.IntegerField(write_only=True)
    status = serializers.ChoiceField(choices=Campaign.Status.choices, default=Campaign.Status.STARTED)
    use_vai_database = serializers.BooleanField(write_only=True, required=False, default=False)
    vai_country = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    vai_industry = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    vai_position = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    agent_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Campaign
        fields = [
            "id", "name", "status",
            "voice_profile_id", "voice_id",
            "lead_list_id", "phone_number_id", "prompt",
            "use_vai_database", "vai_country", "vai_industry", "vai_position",
            "agent_id",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        user = self.context["request"].user
        selected_profile = None
        chosen_voice_id = None

        agent_id = attrs.get("agent_id")
        if agent_id:
            try:
                from voices.models import Agent
                agent = Agent.objects.select_related("profile", "profile__owner").get(
                    id=agent_id, profile__owner=user
                )
            except Exception:
                raise serializers.ValidationError("Agent not found.")
            if agent.profile.status != "ready":
                raise serializers.ValidationError("Selected agent's voice profile is not ready.")
            selected_profile = agent.profile
            chosen_voice_id = selected_profile.eleven_voice_id

        if not selected_profile and attrs.get("voice_profile_id"):
            from voices.models import VoiceProfile
            try:
                vp = VoiceProfile.objects.get(id=attrs["voice_profile_id"], owner=user)
            except VoiceProfile.DoesNotExist:
                raise serializers.ValidationError("Voice profile not found.")
            if not vp.eleven_voice_id:
                raise serializers.ValidationError("Selected voice profile has no ElevenLabs voice_id.")
            if vp.status != "ready":
                raise serializers.ValidationError("Selected voice profile is not ready.")
            selected_profile = vp
            chosen_voice_id = vp.eleven_voice_id

        if not selected_profile and attrs.get("voice_id"):
            raw_voice_id = (attrs["voice_id"] or "").strip()
            if not raw_voice_id:
                raise serializers.ValidationError("voice_id is empty.")
            from voices.models import VoiceProfile
            vp = (
                VoiceProfile.objects
                .filter(eleven_voice_id=raw_voice_id)
                .order_by("-created_at")
                .first()
            )
            if not vp:
                default_id_en = os.getenv("ELEVEN_DEFAULT_VOICE_ID")
                default_id_ar = os.getenv("ELEVEN_DEFAULT_VOICE_ID_AR")
                if raw_voice_id == default_id_ar:
                    display_name = "Default Voice (Arabic)"
                elif raw_voice_id == default_id_en:
                    display_name = "Default Voice (English)"
                else:
                    display_name = "Default Voice"
                vp = VoiceProfile.objects.create(
                    display_name=display_name,
                    eleven_voice_id=raw_voice_id,
                    status="ready",
                )
            selected_profile = vp
            chosen_voice_id = raw_voice_id

        if not selected_profile or not chosen_voice_id:
            raise serializers.ValidationError("Please select a voice (voice_profile_id or voice_id).")

        try:
            pn = PhoneNumberRequest.objects.get(id=attrs["phone_number_id"], owner=user)
        except PhoneNumberRequest.DoesNotExist:
            raise serializers.ValidationError("Phone number not found.")

        if pn.status != PhoneNumberRequest.Status.ENABLED:
            raise serializers.ValidationError("Selected phone number is not enabled.")
        if not pn.provider_phone_id:
            raise serializers.ValidationError("Selected phone number is missing provider_phone_id.")

        use_vai = bool(attrs.get("use_vai_database", False))
        lead_ids_override = None
        resolved_list = None

        if use_vai:
            country = (attrs.get("vai_country") or "").strip()
            industry = (attrs.get("vai_industry") or "").strip()
            position = (attrs.get("vai_position") or "").strip()
            if not country and not industry and not position:
                raise serializers.ValidationError("Please select at least an industry, position, or a country for V-AI database leads.")
            q = Q(owner__isnull=True)
            if country:
                q &= Q(country__iexact=country)
            if industry:
                q &= Q(industry__iexact=industry)
            if position:
                q &= Q(position__iexact=position)
            lead_ids = list(Lead.objects.filter(q).values_list("id", flat=True))
            if not lead_ids:
                raise serializers.ValidationError("No leads found in V-AI database for the selected filters.")
            attrs["_use_vai"] = True
            attrs["_vai_country"] = country or None
            attrs["_vai_industry"] = industry or None
            attrs["_vai_position"] = position or None
            attrs["_lead_ids_override"] = lead_ids
        else:
            if not attrs.get("lead_list_id"):
                raise serializers.ValidationError("List not found.")
            try:
                lead_list = LeadList.objects.get(id=attrs["lead_list_id"], owner=user)
            except LeadList.DoesNotExist:
                raise serializers.ValidationError("List not found.")
            resolved_list = lead_list

        attrs["_voice_profile"] = selected_profile
        attrs["_voice_id"] = chosen_voice_id
        attrs["_phone"] = pn
        if resolved_list:
            attrs["_lead_list"] = resolved_list
        if lead_ids_override is not None:
            attrs["_lead_ids_override"] = lead_ids_override
        return attrs

    @transaction.atomic
    def create(self, validated):
        user = self.context["request"].user
        voice_profile = validated.pop("_voice_profile")
        voice_id = validated.pop("_voice_id")
        phone = validated.pop("_phone")

        use_vai = bool(validated.pop("_use_vai", False))
        vai_country = validated.pop("_vai_country", None)
        vai_industry = validated.pop("_vai_industry", None)
        vai_position = validated.pop("_vai_position", None)
        lead_ids_override = validated.pop("_lead_ids_override", None)
        lead_list = validated.pop("_lead_list", None)

        campaign_specific = (validated.get("prompt") or "").strip()
        final_prompt = BASE_SALES_PROMPT + (
            "\n\n---\n\nAdditional campaign-specific instructions:\n" + campaign_specific
            if campaign_specific else ""
        )

        used_raw_voice_id = bool(validated.get("voice_id") and not validated.get("voice_profile_id") and not validated.get("agent_id"))
        vp_lang = (getattr(voice_profile, "language", "") or "English").strip().lower()
        if used_raw_voice_id:
            default_id_ar = os.getenv("ELEVEN_DEFAULT_VOICE_ID_AR")
            default_id_en = os.getenv("ELEVEN_DEFAULT_VOICE_ID")
            language_code = "ar" if voice_id == default_id_ar else "en"
        else:
            language_code = "ar" if vp_lang == "arabic" else "en"

        first_message_en = "Hello—quick one. I've got a 30-second idea. Is now a bad time?"
        first_message_ar = "مرحبًا—سأكون سريعًا. لدي فكرة تستغرق 30 ثانية. هل هذا وقت غير مناسب؟"
        first_message = first_message_ar if language_code.startswith("ar") else first_message_en

        eleven_agent_id, agent_config_payload = create_elevenlabs_agent(
            name=validated["name"],
            voice_id=voice_id,
            prompt=final_prompt,
            first_message=first_message,
            llm_model="GPT-OSS-20B",
            temperature=0.60,
            enable_end_call=True,
            language=language_code,
        )

        from voices.models import Agent
        new_agent = Agent.objects.create(
            profile=voice_profile,
            eleven_agent_id=eleven_agent_id,
            config=agent_config_payload,
        )

        if use_vai:
            label_country = vai_country or "Any"
            label_industry = vai_industry or "Any"
            label_position = vai_position or "Any"
            name = f"V-AI ({label_industry} / {label_country} / {label_position})"
            lead_list = LeadList.objects.create(owner=user, name=name, country=vai_country or "")

        campaign = Campaign.objects.create(
            owner=user,
            name=validated["name"],
            agent=new_agent,
            voice_profile=voice_profile,
            lead_list=lead_list,
            phone_number=phone,
            prompt=validated.get("prompt", ""),
            status=validated.get("status", Campaign.Status.STOPPED),
            started_at=timezone.now() if validated.get("status") == Campaign.Status.STARTED else None,
        )

        if lead_ids_override is not None:
            lead_ids = lead_ids_override
        else:
            lead_ids = list(lead_list.leads.values_list("id", flat=True))

        objs = (CampaignLead(campaign=campaign, lead_id=lid) for lid in lead_ids)
        batch, chunk = [], 1000
        for i, obj in enumerate(objs, start=1):
            batch.append(obj)
            if i % chunk == 0:
                CampaignLead.objects.bulk_create(batch, ignore_conflicts=True)
                batch.clear()
        if batch:
            CampaignLead.objects.bulk_create(batch, ignore_conflicts=True)

        return campaign




class CampaignListSerializer(serializers.ModelSerializer):
    total_leads = serializers.IntegerField(read_only=True)
    contacted_leads = serializers.IntegerField(read_only=True)
    positive_leads = serializers.IntegerField(read_only=True)
    phone_number = serializers.CharField(source="phone_number.number")
    agent_name = serializers.CharField(source="agent.profile.display_name")

    volume = serializers.SerializerMethodField()

    def get_volume(self, obj):
        secs = getattr(obj, "total_duration_seconds", 0) or 0
        return secs // 60

    class Meta:
        model = Campaign
        fields = [
            "id", "name", "status",
            "total_leads", "contacted_leads", "positive_leads",
            "phone_number", "agent_name",
            "created_at",
            "volume",             # NEW
        ]


class CampaignRenameSerializer(serializers.ModelSerializer):
    """Strictly allow editing the name only."""
    class Meta:
        model = Campaign
        fields = ["name"]

    def validate_name(self, value):
        user = self.context["request"].user
        qs = Campaign.objects.filter(owner=user, name=value).exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("You already have a campaign with this name.")
        return value


class CallLogListSerializer(serializers.ModelSerializer):
    campaign_name = serializers.CharField(source="lead.name", read_only=True)
    agent_name = serializers.CharField(source="agent.profile.display_name", read_only=True)
    phone_number = serializers.CharField(source="lead.phone_number", read_only=True)
    audio_file_url = serializers.SerializerMethodField()

    def get_audio_file_url(self, obj):
        file = getattr(obj, "audio_file", None)
        if not file:
            return None
        try:
            url = file.url
        except Exception:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    class Meta:
        model = CallLog
        fields = [
            "id",
            "campaign_name",
            "phone_number",
            "agent_name",
            "status",
            "started_at",
            "ended_at",
            "duration_seconds",
            "score",
            "created_at",
            "recording_url",
            "audio_file_url",
        ]


class CallLogDetailSerializer(CallLogListSerializer):
    transcript_text = serializers.CharField(read_only=True)
    transcript_json = serializers.JSONField(read_only=True)
    analysis = serializers.JSONField(read_only=True)
    audio_file = serializers.FileField(read_only=True)

    class Meta(CallLogListSerializer.Meta):
        fields = CallLogListSerializer.Meta.fields + [
            "transcript_text",
            "transcript_json",
            "analysis",
            "audio_file",
        ]


from rest_framework import serializers as _srl  # avoid clobbering above import
from voices.models import CallSession

class SupportCallListSerializer(_srl.ModelSerializer):
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


class AdminCallLogRowSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    agent_name = serializers.CharField(source="agent.profile.display_name", read_only=True)
    phone_number = serializers.CharField(source="phone_number.number", read_only=True)
    owner_email = serializers.EmailField(source="owner.email", read_only=True)
    owner_user_name = serializers.CharField(source="owner.user_name", read_only=True)
    audio_file_url = serializers.SerializerMethodField()

    def get_audio_file_url(self, obj):
        file = getattr(obj, "audio_file", None)
        if not file:
            return None
        try:
            url = file.url
        except Exception:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    class Meta:
        model = CallLog
        fields = [
            "id",
            "owner_email",
            "owner_user_name",
            "lead_name",
            "phone_number",
            "agent_name",
            "status",
            "started_at",
            "ended_at",
            "duration_seconds",
            "score",
            "created_at",
            "recording_url",
            "audio_file_url",
        ]



class SupportCallListAdminSerializer(_srl.ModelSerializer):
    owner_email = serializers.EmailField(source="embed.owner.email", read_only=True)
    owner_user_name = serializers.CharField(source="embed.owner.user_name", read_only=True)
    audio_file_url = serializers.SerializerMethodField()

    def get_audio_file_url(self, obj):
        file = getattr(obj, "audio_file", None)
        if not file:
            return None
        try:
            url = file.url
        except Exception:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    class Meta:
        model = CallSession
        fields = [
            "id",
            "conversation_id",
            "owner_email",
            "owner_user_name",
            "user_display_name",
            "status",
            "started_at",
            "finished_at",
            "duration_seconds",
            "score",
            "recording_url",
            "audio_file_url",
        ]


class SupportCallDetailAdminSerializer(SupportCallListAdminSerializer):
    transcript_text = serializers.CharField(read_only=True)
    audio_file = serializers.FileField(read_only=True)

    class Meta(SupportCallListAdminSerializer.Meta):
        fields = SupportCallListAdminSerializer.Meta.fields + [
            "transcript_text",
            "audio_file",
        ]
