from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
import logging

from vai.campaigns.models import Campaign, CampaignLead, CallLog
from vai.campaigns.eleven import start_outbound_call_via_elevenlabs
from vai.settings import CAMPAIGN_MAX_PARALLEL_CALLS

logger = logging.getLogger(__name__)

def _inside_ksa_window(now_utc=None):
    now_utc = now_utc or timezone.now()
    ksa_now = now_utc.astimezone(ZoneInfo("Asia/Riyadh"))
    logger.debug(f"ksa_now={ksa_now.isoformat()}")
    return 0 <= ksa_now.hour < 24

@shared_task
def dispatch_campaign_calls():
    from vai.billing.models import Subscription
    logger.info("dispatch_campaign_calls: invoked")
    if not _inside_ksa_window():
        logger.info("dispatch_campaign_calls: outside KSA window")
        return
    campaigns = (Campaign.objects
                 .select_related("agent__profile", "phone_number", "owner")
                 .filter(status=Campaign.Status.STARTED))
    logger.info(f"dispatch_campaign_calls: found {campaigns.count()} started campaigns")
    for campaign in campaigns:
        logger.info(f"campaign id={campaign.id} name={campaign.name}")
        owner = campaign.owner
        buckets = []
        for s in Subscription.objects.filter(user=owner, status__in=["active", "trialing"]).select_related("plan_content_type"):
            try:
                components = s.plan.components()
                logger.debug(f"subscription id={s.id} components={components}")
                if "outbound_calling" in components:
                    s.initialize_or_rollover_usage_buckets()
                    b = s.get_active_bucket("outbound_calling")
                    if b:
                        buckets.append(b)
                        logger.debug(f"active bucket id={getattr(b,'id',None)} seconds_included={getattr(b,'seconds_included',None)} seconds_used={getattr(b,'seconds_used',None)} unlimited={getattr(b,'unlimited',None)}")
            except Exception as e:
                logger.exception(f"subscription processing failed id={getattr(s,'id',None)} err={e}")
                continue
        if not buckets:
            logger.info("no eligible buckets, skipping campaign")
            continue
        unlimited = any(b.unlimited for b in buckets)
        if unlimited:
            minutes_remaining = 10**9
        else:
            remaining_seconds = sum(max(0, b.seconds_included - b.seconds_used) for b in buckets)
            minutes_remaining = remaining_seconds // 60
        logger.info(f"minutes_remaining={minutes_remaining} unlimited={unlimited}")
        if minutes_remaining < 1 and not unlimited:
            logger.info("no minutes remaining, skipping campaign")
            continue
        if unlimited:
            target_calls = 5
        elif minutes_remaining < 25:
            target_calls = 1
        else:
            expected_minutes_per_call = 3
            cap = int(minutes_remaining // expected_minutes_per_call)
            target_calls = max(3, min(5, cap))
        if target_calls < 1:
            logger.info("target_calls < 1, skipping")
            continue
        logger.info(f"target_calls={target_calls}")
        leads_qs = (CampaignLead.objects
                    .select_for_update(skip_locked=True)
                    .filter(
                        campaign=campaign,
                        status=CampaignLead.LeadStatus.NEW,
                        call_count=0,
                        lead__phone_number__regex=r'^\+?[0-9\s\-\(\)]+$',
                    )
                    .select_related("lead"))[:target_calls]
        with transaction.atomic():
            batch = list(leads_qs)
            logger.info(f"selected {len(batch)} leads for dispatch")
            for cl in batch:
                log = CallLog.objects.create(
                    owner=campaign.owner,
                    campaign=campaign,
                    lead=cl.lead,
                    agent=campaign.agent,
                    phone_number=campaign.phone_number,
                    status=CallLog.Status.DISPATCHED,
                    started_at=timezone.now(),
                    provider=campaign.phone_number.provider,
                )
                logger.info(f"calllog created id={log.id} lead_id={cl.lead_id} to={cl.lead.phone_number}")
                cl.call_count = 1
                cl.last_attempted_at = timezone.now()
                cl.save(update_fields=["call_count", "last_attempted_at", "updated_at"])
                try:
                    start_outbound_call.delay(log.id)
                    logger.info(f"queued start_outbound_call for call_log_id={log.id}")
                except Exception as e:
                    logger.exception(f"failed to queue start_outbound_call call_log_id={log.id} err={e}")
                    log.status = CallLog.Status.FAILED
                    log.dispatch_error = f"Failed to queue task: {str(e)}"
                    log.analysis = {"error": f"Failed to queue task: {str(e)}", "stage": "queue"}
                    log.ended_at = timezone.now()
                    log.save(update_fields=["status", "dispatch_error", "analysis", "ended_at", "updated_at"])
        _maybe_complete_campaign(campaign.id)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def start_outbound_call(self, call_log_id: int):
    from vai.campaigns.models import CallLog
    from django.db.models import F
    logger.info(f"start_outbound_call invoked call_log_id={call_log_id} attempt={self.request.retries + 1}")

    # Increment dispatch attempts counter
    CallLog.objects.filter(id=call_log_id).update(dispatch_attempts=F('dispatch_attempts') + 1)

    try:
        log = CallLog.objects.select_related(
            "campaign__voice_profile", "campaign__agent__profile",
            "campaign__lead_list", "lead", "phone_number"
        ).get(id=call_log_id, status=CallLog.Status.DISPATCHED)
    except CallLog.DoesNotExist:
        logger.error(f"call_log not found or not in DISPATCHED state call_log_id={call_log_id}")
        # Try to record error on the CallLog if it exists but in wrong state
        CallLog.objects.filter(id=call_log_id).update(
            dispatch_error=f"Task executed but CallLog not in DISPATCHED state at {timezone.now().isoformat()}"
        )
        return
    campaign = log.campaign
    lead = log.lead
    phone = log.phone_number
    agent = campaign.agent
    try:
        logger.info(f"starting outbound call call_log_id={log.id} agent_id={agent.eleven_agent_id} phone_provider={phone.provider} to={lead.phone_number}")
        conversation_id, call_id = start_outbound_call_via_elevenlabs(
            agent_id=agent.eleven_agent_id,
            agent_phone_number_id=phone.provider_phone_id,
            to_number=lead.phone_number,
            campaign_prompt=None,
            voice_id=campaign.voice_profile.eleven_voice_id or None,
            dynamic_vars={
                "lead_id": lead.id,
                "lead_name": lead.name,
                "lead_company": lead.company,
                "lead_position": lead.position,
                "lead_country": lead.country,
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
            },
            provider=phone.provider,
        )
        logger.info(f"elevenlabs call initiated call_log_id={log.id} conversation_id={conversation_id} call_id={call_id}")
        log.provider_conversation_id = conversation_id
        log.provider_call_id = call_id
        log.status = CallLog.Status.RINGING
        log.save(update_fields=["provider_conversation_id", "provider_call_id", "status", "updated_at"])
        _maybe_complete_campaign(campaign.id)
    except Exception as e:
        logger.exception(f"elevenlabs outbound call failed call_log_id={log.id} err={e} attempt={self.request.retries + 1}")

        # Check if we should retry
        if self.request.retries < self.max_retries:
            logger.warning(f"Retrying call_log_id={log.id}, attempt {self.request.retries + 2} of {self.max_retries + 1}")
            log.dispatch_error = f"Attempt {self.request.retries + 1} failed: {str(e)}"
            log.save(update_fields=["dispatch_error", "updated_at"])
            raise self.retry(exc=e)
        else:
            # Final failure after all retries exhausted
            logger.error(f"All retries exhausted for call_log_id={log.id}")
            log.status = CallLog.Status.FAILED
            log.dispatch_error = f"All {self.max_retries + 1} attempts failed. Last error: {str(e)}"
            log.analysis = {"error": str(e), "stage": "elevenlabs", "attempts": self.request.retries + 1}
            log.ended_at = timezone.now()
            log.save(update_fields=["status", "dispatch_error", "analysis", "ended_at", "updated_at"])
            _maybe_complete_campaign(campaign.id)

def _maybe_complete_campaign(campaign_id: int):
    from vai.campaigns.models import Campaign, CampaignLead, CallLog
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(id=campaign_id)
        if campaign.status != Campaign.Status.STARTED:
            logger.debug(f"_maybe_complete_campaign: campaign not started id={campaign_id} status={campaign.status}")
            return
        any_new_left = CampaignLead.objects.filter(
            campaign=campaign,
            status=CampaignLead.LeadStatus.NEW,
            call_count=0,
        ).exists()
        any_dispatched_logs = CallLog.objects.filter(
            campaign=campaign,
            status=CallLog.Status.DISPATCHED,
        ).exists()
        logger.debug(f"_maybe_complete_campaign: any_new_left={any_new_left} any_dispatched_logs={any_dispatched_logs} id={campaign_id}")
        if not any_new_left and not any_dispatched_logs:
            campaign.complete()
            logger.info(f"campaign completed id={campaign_id}")


@shared_task
def cleanup_stale_dispatched_calls():
    """Mark calls stuck in DISPATCHED for >10 minutes as FAILED.

    This handles cases where:
    - Celery worker crashed during task execution
    - Task was lost due to broker issues
    - Any other scenario where a call got stuck
    """
    from vai.campaigns.models import CallLog

    stale_threshold = timezone.now() - timedelta(minutes=10)
    stale_calls = CallLog.objects.filter(
        status=CallLog.Status.DISPATCHED,
        created_at__lt=stale_threshold
    )

    count = stale_calls.count()
    if count:
        logger.warning(f"Found {count} stale DISPATCHED calls, marking as FAILED")

        # Get IDs and campaign IDs before update for logging and campaign completion check
        stale_data = list(stale_calls.values_list('id', 'campaign_id'))

        stale_calls.update(
            status=CallLog.Status.FAILED,
            dispatch_error="Timed out - stuck in DISPATCHED for >10 minutes",
            analysis={"error": "Timed out - stuck in DISPATCHED for >10 minutes", "stage": "timeout"},
            ended_at=timezone.now()
        )

        # Log each stale call
        for call_id, campaign_id in stale_data:
            logger.info(f"Marked stale call as FAILED call_log_id={call_id} campaign_id={campaign_id}")

        # Check if any campaigns can now complete
        unique_campaign_ids = set(campaign_id for _, campaign_id in stale_data)
        for campaign_id in unique_campaign_ids:
            _maybe_complete_campaign(campaign_id)

    return count
