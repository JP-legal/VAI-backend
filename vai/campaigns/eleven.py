from django.conf import settings
from typing import Optional
import os, requests, logging, json

ELEVEN_BASE = "https://api.elevenlabs.io"
HEADERS = {"xi-api-key": settings.ELEVENLABS_API_KEY, "Content-Type": "application/json"}
DEFAULT_ELEVEN_LLM = getattr(settings, "ELEVENLABS_DEFAULT_LLM", "qwen3-30b-a3b")
REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)

def _compact(obj):
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items() if v not in (None, {}, [])}
    if isinstance(obj, list):
        return [_compact(v) for v in obj if v is not None]
    return obj

def _pick_tts_model(language: Optional[str]) -> str:
    lang = (language or "en").lower()
    if lang in ("en", "en-us", "en-gb"):
        return os.getenv("ELEVEN_TTS_MODEL_ID_EN", "eleven_turbo_v2")
    return os.getenv("ELEVEN_TTS_MODEL_ID_MULTI", "eleven_turbo_v2_5")

def _json_dumps(data):
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return str(data)

def create_elevenlabs_agent(*, name: str, voice_id: str | None, prompt: str,
                            first_message: str, llm_model: str = "GPT-OSS-20B",
                            temperature: float = 0.60, enable_end_call: bool = True,
                            language: str | None = None):
    payload = {
        "name": name,
        "conversation_config": {
            "agent": {
                "first_message": first_message,
                "language": language,
                "prompt": {
                    "prompt": prompt,
                    "llm": DEFAULT_ELEVEN_LLM,
                    "temperature": temperature,
                    "built_in_tools": {"end_call": {}} if enable_end_call else {}
                }
            },
            "tts": {"voice_id": voice_id, "model_id": _pick_tts_model(language)} if voice_id else None,
        }
    }
    payload = _compact(payload)
    url = f"{ELEVEN_BASE}/v1/convai/agents/create"
    logger.info(f"elevenlabs request url={url} payload={_json_dumps(payload)}")
    try:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT)
        logger.info(f"elevenlabs response status={r.status_code} text={r.text}")
        r.raise_for_status()
        data = r.json()
        agent_id = data.get("agent_id")
        logger.info(f"elevenlabs agent created agent_id={agent_id}")
        return agent_id, payload
    except requests.RequestException as e:
        logger.exception(f"elevenlabs request error url={url} err={e}")
        raise

def _payload(agent_id, agent_phone_number_id, to_number,
             campaign_prompt=None, voice_id=None, dynamic_vars=None):
    data = {
        "agent_id": agent_id,
        "agent_phone_number_id": agent_phone_number_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {
            "dynamic_variables": {**(dynamic_vars or {})},
        }
    }
    override = {}
    if voice_id:
        override.setdefault("tts", {})["voice_id"] = voice_id
    if campaign_prompt:
        override.setdefault("agent", {}).setdefault("prompt", {})["prompt"] = campaign_prompt
    if override:
        data["conversation_initiation_client_data"]["conversation_config_override"] = override
    return data

def start_outbound_call_via_elevenlabs(*, agent_id, agent_phone_number_id, to_number,
                                       campaign_prompt=None, voice_id=None, dynamic_vars=None,
                                       provider="twilio"):
    if provider == "twilio":
        url = f"{ELEVEN_BASE}/v1/convai/twilio/outbound-call"
    else:
        url = f"{ELEVEN_BASE}/v1/convai/sip-trunk/outbound-call"
    payload = _payload(agent_id, agent_phone_number_id, to_number,
                       campaign_prompt=campaign_prompt, voice_id=voice_id, dynamic_vars=dynamic_vars)
    logger.info(f"elevenlabs outbound-call request url={url} payload={_json_dumps(payload)}")
    try:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT)
        logger.info(f"elevenlabs outbound-call response status={r.status_code} text={r.text}")
        r.raise_for_status()
        data = r.json()
        conversation_id = data.get("conversation_id")
        call_id = data.get("callSid") or data.get("sip_call_id")
        logger.info(f"elevenlabs outbound-call success conversation_id={conversation_id} call_id={call_id}")
        return conversation_id, call_id
    except requests.RequestException as e:
        logger.exception(f"elevenlabs outbound-call error url={url} err={e}")
        raise
