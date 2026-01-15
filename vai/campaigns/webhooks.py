import base64
import os
import json
import re
from math import floor
from datetime import datetime, timezone as utc, timedelta
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from voices.models import CallSession, VoiceProfile, EmbeddableAgent
from vai.campaigns.models import CallLog
from textblob import TextBlob

def _parse_dt(val):
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if timezone.is_aware(val) else timezone.make_aware(val, utc)
    if isinstance(val, (int, float)) or (isinstance(val, str) and val.strip().replace(".", "", 1).isdigit()):
        try:
            return datetime.fromtimestamp(float(val), tz=utc)
        except Exception:
            return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if timezone.is_aware(dt) else timezone.make_aware(dt, utc)
        except Exception:
            return None
    return None

def _normalize_to_int_1_10(value):
    try:
        x = float(value)
    except Exception:
        return None
    if 0.0 <= x <= 1.0:
        scaled = int(round(x * 10))
        if x > 0.0 and scaled == 0:
            scaled = 1
        x = scaled
    elif 1.0 < x <= 10.0:
        x = int(round(x))
    elif 10.0 < x <= 100.0:
        x = int(round(x / 10.0))
    else:
        x = int(round(x))
    if x < 1:
        x = 1
    if x > 10:
        x = 10
    return x

def _extract_integer_1_10(text):
    try:
        data = json.loads(text)
        for key in ["score", "interest_score", "helpfulness_score", "positivity_score"]:
            if key in data:
                v = int(data[key])
                return max(1, min(10, v))
    except Exception:
        pass
    m = re.search(r"\b([1-9]|10)\b", text)
    if not m:
        return None
    return int(m.group(1))

def _call_openai(prompt_text, sample):
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if not api_key or not sample:
            return None
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt_text},
                    {"role": "user", "content": "Transcript (speaker-labelled):\n\n" + sample + "\n\nReturn JSON exactly like: {\"score\": 7}"},
                ],
            )
            content = (resp.choices[0].message.content or "").strip()
        except Exception:
            import openai
            openai.api_key = api_key
            resp = openai.ChatCompletion.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": prompt_text},
                    {"role": "user", "content": "Transcript (speaker-labelled):\n\n" + sample + "\n\nReturn JSON exactly like: {\"score\": 7}"},
                ],
            )
            content = (resp["choices"][0]["message"]["content"] or "").strip()
        score = _extract_integer_1_10(content)
        return score
    except Exception:
        return None

def _compute_sales_interest_score(transcript_text):
    sample = transcript_text[:20000]
    prompt_text = (
        "You are a strict evaluator of outbound sales call transcripts. "
        "Output a single JSON object with an integer field \"score\" from 1-10 representing CUSTOMER INTEREST in the offer "
        "(1 = no interest/hostile, 10 = extremely interested/clear buying intent). Output JSON only.\n\n"
        "SCOPE\n"
        "- Judge only the prospective customer's interest and buying intent. Ignore agent pleasantries unless they reflect customer response.\n\n"
        "SCORING DIMENSIONS\n"
        "- Outcome & next steps (60%): meeting set, demo booked, trial started, purchase/PO, referral to decision-maker, permission to follow up with a concrete time.\n"
        "- Explicit interest signals (25%): curiosity, positive objections, budget/timeline questions, agreeing to share info, discussing stakeholders.\n"
        "- Friction vs. refusal (15%): refusals, opt-outs, hang-ups, hostility, legal do-not-call requests.\n\n"
        "HARD RULES\n"
        "- Immediate refusal, do-not-call, hang-up, or hostile language from the customer -> 1-2.\n"
        "- Explicit refusal with no permission to follow up -> 1-3.\n"
        "- Neutral/busy with no refusal and no next step -> 4.\n"
        "- Light interest (asks to email info, vague follow-up) without a scheduled time -> 5.\n"
        "- Clear permission to follow up with time/day or agrees to continue later -> 6.\n"
        "- Meeting/demo scheduled, or introduced to decision-maker -> 7-8.\n"
        "- Strong buying signals (trial started, budget/timeline aligned, decision window discussed) -> 8-9.\n"
        "- Commitment/purchase intent clearly stated or firm next step with key stakeholder -> 9-10.\n"
        "- Very short transcript (<30 tokens) containing refusal terms -> cap at 3.\n"
        "- Do not inflate score due to agent enthusiasm; base on customer words/actions.\n\n"
        "EVIDENCE PRIORITY\n"
        "1) Explicit commitments or scheduled events.\n"
        "2) Concrete interest signals (budget/timeline/stakeholders).\n"
        "3) Tone and cooperative engagement only if 1) and 2) are ambiguous.\n\n"
        "DISAMBIGUATION\n"
        "- If mixed signals, anchor score on the strongest outcome signal.\n"
        "- If the customer pushes for email-only with no time commitment, treat as weaker than scheduled follow-up.\n\n"
        "FEW-SHOT CALIBRATION\n"
        "Transcript:\n"
        "agent: Hi, this is Sam from Alphabets. Do you have a minute?\n"
        "user: Not interested. Remove me.\n"
        "agent: Understood.\n"
        "Output:\n"
        "{\"score\": 1}\n"
        "Transcript:\n"
        "agent: We help with workflow automation. Quick chat?\n"
        "user: I'm busy, can you call tomorrow at 2?\n"
        "agent: Sure, sending invite.\n"
        "Output:\n"
        "{\"score\": 6}\n"
        "Transcript:\n"
        "agent: We cut infra costs by 20%.\n"
        "user: Can you send pricing and a deck? Let's meet next Tuesday at 11, I'll invite my CTO.\n"
        "agent: Perfect.\n"
        "Output:\n"
        "{\"score\": 8}\n"
        "Transcript:\n"
        "agent: Hi! We offer a pilot.\n"
        "user: Let's start a two-week trial. We have budget approval pending for Q4.\n"
        "agent: Great.\n"
        "Output:\n"
        "{\"score\": 9}"
    )
    score = _call_openai(prompt_text, sample)
    if score is not None:
        return score
    try:
        polarity = TextBlob(transcript_text).sentiment.polarity
        score_tb = int(round((polarity + 1.0) * 5.0))
        return max(1, min(10, score_tb))
    except Exception:
        return None

def _compute_support_helpfulness_score(transcript_text):
    sample = transcript_text[:20000]
    prompt_text = (
        "You are a strict evaluator of customer support transcripts. "
        "Output a single JSON object with an integer field \"score\" from 1-10 representing AGENT HELPFULNESS and CUSTOMER SATISFACTION "
        "(1 = unhelpful/incorrect and upset customer, 10 = fully resolved, accurate, and happy customer). Output JSON only.\n\n"
        "SCOPE\n"
        "- Judge whether the agent effectively solved the user's problem, provided accurate information, and left the customer satisfied.\n"
        "- Prioritize the customer's outcome and expressed satisfaction over the agent's politeness.\n\n"
        "SCORING DIMENSIONS\n"
        "- Resolution quality (50%): problem solved, clear steps that work, confirmed outcome, or precise workaround documented.\n"
        "- Accuracy & completeness (25%): correct info, no contradictions, cites relevant policies/links, anticipates follow-up questions.\n"
        "- Experience & demeanor (25%): empathy, clarity, reasonable handling time, minimal runaround, no blame-shifting.\n\n"
        "HARD RULES\n"
        "- Issue unresolved, incorrect guidance, or misleading info -> 1-3 depending on severity.\n"
        "- Partial answer or deflection without a path to resolution -> 3-4.\n"
        "- Helpful guidance but not fully resolved; user neutral/accepting -> 5-6.\n"
        "- Clearly resolved with confirmation from user -> 7-8.\n"
        "- Resolved plus proactive extra help (education, prevention steps, follow-up ticket/summary) and user expresses satisfaction -> 9-10.\n"
        "- Rudeness or dismissiveness from agent -> cap at 3.\n"
        "- Excessive delays, long holds with no progress -> reduce by 1-2.\n"
        "- If escalation occurs: with clear ownership, urgency, and next step time window -> up to 6; without clarity -> 3-4.\n"
        "- Very short transcript (<30 tokens) with unresolved request -> cap at 3.\n\n"
        "EVIDENCE PRIORITY\n"
        "1) Explicit user confirmation that the issue is resolved or instructions worked.\n"
        "2) Concrete artifacts: ticket created, replacement issued, refund processed, link to doc, reproducible steps.\n"
        "3) Tone and empathy as a tiebreaker.\n\n"
        "DISAMBIGUATION\n"
        "- If mixed signals, anchor score on the final outcome stated by the user.\n"
        "- If the agent is polite but wrong, score low. Accuracy outweighs niceness.\n\n"
        "FEW-SHOT CALIBRATION\n"
        "Transcript:\n"
        "user: My login keeps failing.\n"
        "agent: Try resetting your router.\n"
        "user: That doesn't make sense.\n"
        "agent: I don't know then.\n"
        "Output:\n"
        "{\"score\": 2}\n"
        "Transcript:\n"
        "user: I can't access invoices.\n"
        "agent: Permissions were missing; I added the Billing role. Please refresh.\n"
        "user: I see them now, thanks.\n"
        "Output:\n"
        "{\"score\": 8}\n"
        "Transcript:\n"
        "user: My order is late.\n"
        "agent: I expedited shipping and refunded fees. You'll get it by Thursday. Email with tracking sent.\n"
        "user: Perfect, appreciate it.\n"
        "Output:\n"
        "{\"score\": 9}"
    )
    score = _call_openai(prompt_text, sample)
    if score is not None:
        return score
    try:
        polarity = TextBlob(transcript_text).sentiment.polarity
        score_tb = int(round((polarity + 1.0) * 5.0))
        return max(1, min(10, score_tb))
    except Exception:
        return None

@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
def unified_elevenlabs_webhook(request):
    from django.core.mail import send_mail
    from django.conf import settings
    from vai.billing.models import Subscription

    def _subs_with_component(user, component):
        subs = []
        for s in Subscription.objects.filter(user=user, status__in=["active", "trialing"]).select_related("plan_content_type"):
            try:
                if component in s.plan.components():
                    s.initialize_or_rollover_usage_buckets()
                    b = s.get_active_bucket(component)
                    if b:
                        subs.append((s, b))
            except Exception:
                continue
        return subs

    def _remaining_and_buckets(user, component):
        buckets = _subs_with_component(user, component)
        unlimited = any(b.unlimited for _, b in buckets)
        if unlimited:
            return 10**12, True, buckets
        rem = 0
        for _, b in buckets:
            rem += max(0, b.seconds_included - b.seconds_used)
        return rem, False, buckets

    def _total_overage_seconds(buckets):
        total = 0
        for _, b in buckets:
            if not b.unlimited:
                total += max(0, b.seconds_used - b.seconds_included)
        return total

    def _send_alert_no_bucket(user, call_type, call_id):
        try:
            send_mail(
                "No active usage bucket",
                f"User: {getattr(user, 'email', str(user.id))}\nCall Type: {call_type}\nCall ID: {call_id}",
                settings.DEFAULT_FROM_EMAIL,
                ["ali@xonboard.io"],
                fail_silently=True,
            )
        except Exception:
            pass

    def _send_overage_alert(user, component, overage_seconds, call_type, call_id):
        try:
            send_mail(
                "User exceeded minutes by >5 minutes",
                f"User: {getattr(user, 'email', str(user.id))}\nComponent: {component}\nOverage seconds: {int(overage_seconds)}\nCall Type: {call_type}\nCall ID: {call_id}",
                settings.DEFAULT_FROM_EMAIL,
                ["ali@xonboard.io"],
                fail_silently=True,
            )
        except Exception:
            pass

    def _record_usage(user, component, seconds, call_type, call_id):
        rem_before, unlimited, buckets = _remaining_and_buckets(user, component)
        if not buckets:
            _send_alert_no_bucket(user, call_type, call_id)
            return
        over_before = _total_overage_seconds(buckets)
        if unlimited:
            sub, _b = next(((s, b) for s, b in buckets if b.unlimited), buckets[0])
            sub.record_usage_seconds(component, int(seconds))
        else:
            secs_left = int(seconds)
            sortable = []
            for s, b in buckets:
                remaining = max(0, b.seconds_included - b.seconds_used)
                sortable.append((remaining, b.period_end, s, b))
            sortable.sort(key=lambda t: (-t[0], t[1]))
            for remaining, _end, s, b in sortable:
                if secs_left <= 0:
                    break
                if remaining <= 0:
                    continue
                to_use = min(remaining, secs_left)
                s.record_usage_seconds(component, int(to_use))
                secs_left -= to_use
            if secs_left > 0:
                fallback = sorted(buckets, key=lambda sb: sb[1].period_end)[0][0]
                fallback.record_usage_seconds(component, int(secs_left))
        _, _, buckets_after = _remaining_and_buckets(user, component)
        over_after = _total_overage_seconds(buckets_after)
        if over_before <= 300 < over_after:
            _send_overage_alert(user, component, over_after, call_type, call_id)

    data_top = request.data if isinstance(request.data, dict) else {}
    body = data_top.get("data") if isinstance(data_top.get("data"), dict) else data_top
    conversation_id = body.get("conversation_id") or body.get("id")
    if not conversation_id:
        return Response({"detail": "missing conversation_id"}, status=400)
    agent_id = body.get("agent_id")
    user_display_name = "Visitor"
    started_at = _parse_dt(body.get("started_at") or body.get("event_timestamp"))
    finished_at = _parse_dt(body.get("finished_at") or body.get("updated_at"))
    md = body.get("metadata") or {}
    duration = (
        body.get("duration_seconds")
        or body.get("duration_secs")
        or md.get("call_duration_secs")
    )
    transcript_text = ""
    tr = body.get("transcript")
    if isinstance(tr, list):
        parts = []
        for t in tr:
            if not isinstance(t, dict):
                continue
            msg = ((t.get("message") or t.get("text") or t.get("content") or "")).strip()
            if msg:
                role = (t.get("role") or t.get("speaker") or "").strip()
                parts.append(f"{role}: {msg}" if role else msg)
        transcript_text = "\n".join(parts)
    elif isinstance(tr, str):
        transcript_text = tr.strip()
    if not transcript_text:
        transcript_text = (body.get("transcript_text") or "").strip()
    def _get_payload_score(b):
        def get_in(d, *keys):
            cur = d
            for k in keys:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    return None
            return cur
        candidates = [
            ("score",),
            ("quality_score",),
            ("qualityScore",),
            ("analysis", "score"),
            ("analysis", "quality_score"),
            ("analysis", "qualityScore"),
            ("metrics", "score"),
            ("metrics", "quality_score"),
            ("metrics", "qualityScore"),
        ]
        for path in candidates:
            val = get_in(b, *path)
            if val is not None:
                n = _normalize_to_int_1_10(val)
                if n is not None:
                    return n
        return None
    payload_score = _get_payload_score(body)
    if isinstance(md, dict):
        user_display_name = md.get("user_name") or md.get("user_id") or user_display_name
    cid = body.get("conversation_initiation_client_data") or {}
    dyn = cid.get("dynamic_variables") or {}
    if dyn.get("user_name"):
        user_display_name = dyn["user_name"]
    u = body.get("user")
    if isinstance(u, dict) and (u.get("display_name") or "").strip():
        user_display_name = u["display_name"].strip()
    try:
        clog = CallLog.objects.select_related("campaign", "lead", "owner").get(
            provider_conversation_id=conversation_id
        )
        # Track if this CallLog was already completed to prevent duplicate usage recording
        was_already_completed = clog.status == CallLog.Status.COMPLETED

        full_audio = body.get("full_audio")
        if full_audio and hasattr(clog, "audio_file"):
            try:
                audio_bytes = base64.b64decode(full_audio)
                fname = f"conversation_{conversation_id}.mp3"
                clog.audio_file.save(fname, ContentFile(audio_bytes), save=False)
            except Exception:
                pass
        if transcript_text:
            clog.transcript_text = transcript_text[:1_000_000]
        score = payload_score
        if score is None and transcript_text:
            ai_score = _compute_sales_interest_score(transcript_text)
            if ai_score is not None:
                score = ai_score
        if score is None and transcript_text:
            try:
                polarity = TextBlob(transcript_text).sentiment.polarity
                score_tb = int(round((polarity + 1.0) * 5.0))
                score = max(1, min(10, score_tb))
            except Exception:
                pass
        if score is not None:
            clog.score = int(score)
            clog.is_positive = (clog.score >= 8)
        if duration is not None:
            clog.duration_seconds = duration
        try:
            status_completed = CallLog.Status.COMPLETED
        except Exception:
            status_completed = "completed"
        clog.status = status_completed
        clog.ended_at = finished_at or timezone.now()
        if not getattr(clog, "started_at", None):
            if started_at:
                clog.started_at = started_at
            elif duration and clog.ended_at:
                clog.started_at = clog.ended_at - timedelta(seconds=duration)
        clog.save()
        sec = None
        try:
            if duration is not None:
                sec = int(float(duration))
            elif clog.duration_seconds:
                sec = int(float(clog.duration_seconds))
            elif clog.started_at and clog.ended_at:
                sec = int(max(0, (clog.ended_at - clog.started_at).total_seconds()))
        except Exception:
            sec = None
        if sec and sec > 0 and not was_already_completed:
            billable_seconds = int(((sec + 59) // 60) * 60)
            _record_usage(clog.owner, "outbound_calling", billable_seconds, "outbound", clog.id)
        return Response(
            {"ok": True, "path": "campaign", "id": clog.id, "conversation_id": conversation_id},
            status=200,
        )
    except CallLog.DoesNotExist:
        pass
    embed = None
    if agent_id:
        embed = EmbeddableAgent.objects.select_related("profile", "owner").filter(eleven_agent_id=agent_id).first()
    if not embed:
        return Response({"ok": False, "path": "support", "reason": "unknown_eleven_agent_id", "conversation_id": conversation_id}, status=200)
    profile = embed.profile
    call, created = CallSession.objects.get_or_create(
        conversation_id=conversation_id,
        defaults={
            "profile": profile,
            "embed": embed,
            "user_display_name": user_display_name,
            "started_at": started_at or timezone.now(),
        },
    )
    # Track if this call was already completed to prevent duplicate usage recording
    was_already_completed = not created and getattr(call, "status", "") == "completed"

    changed = False
    def _set(field, value):
        nonlocal changed
        if value is not None and getattr(call, field, None) != value:
            setattr(call, field, value)
            changed = True
    _set("embed", embed)
    _set("agent_id", agent_id)
    _set("started_at", started_at)
    _set("finished_at", finished_at)
    if duration is not None:
        _set("duration_seconds", duration)
    if transcript_text:
        _set("transcript_text", transcript_text[:1_000_000])
    score = payload_score
    if score is None and transcript_text:
        ai_score = _compute_support_helpfulness_score(transcript_text)
        if ai_score is not None:
            score = ai_score
    if score is None and transcript_text:
        try:
            polarity = TextBlob(transcript_text).sentiment.polarity
            score_tb = int(round((polarity + 1.0) * 5.0))
            score = max(1, min(10, score_tb))
        except Exception:
            pass
    if score is not None:
        _set("score", int(score))
    if (call.user_display_name or "") != (user_display_name or ""):
        _set("user_display_name", user_display_name or "Visitor")
    if finished_at and getattr(call, "status", "") != "completed":
        _set("status", "completed")
    if changed:
        call.save()
    full_audio = body.get("full_audio")
    if full_audio and hasattr(call, "audio_file"):
        try:
            audio_bytes = base64.b64decode(full_audio)
            fname = f"conversation_{conversation_id}.mp3"
            call.audio_file.save(fname, ContentFile(audio_bytes), save=False)
            changed = True
        except Exception:
            pass
    if changed:
        call.save()
    sec = None
    try:
        if duration is not None:
            sec = int(float(duration))
        elif call.duration_seconds:
            sec = int(float(call.duration_seconds))
        elif call.started_at and call.finished_at:
            sec = int(max(0, (call.finished_at - call.started_at).total_seconds()))
    except Exception:
        sec = None
    if sec and sec > 0 and call.embed and call.embed.owner_id and not was_already_completed:
        _record_usage(call.embed.owner, "support_agent", sec, "support", call.id)
    return Response({
        "ok": True,
        "path": "support",
        "id": call.id,
        "conversation_id": call.conversation_id,
        "created": created,
    }, status=200)
