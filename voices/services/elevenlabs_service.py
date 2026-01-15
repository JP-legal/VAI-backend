import os, requests, mimetypes, io
from typing import List, Optional
from io import BytesIO
from pydub import AudioSegment

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
BASE_URL = "https://api.elevenlabs.io"


def _headers(json: bool = False) -> dict:
    if not ELEVEN_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    h = {"xi-api-key": ELEVEN_API_KEY}
    if json:
        h["Content-Type"] = "application/json"
    return h
def display_language_to_code(display_name: str) -> str:
    """
    Convert 'English'/'Arabic' to the short code expected by ElevenLabs config.
    """
    return "ar" if normalize_display_language(display_name) == "Arabic" else "en"
def normalize_display_language(name: Optional[str]) -> str:
    """
    Coerce any input into one of ('English', 'Arabic'), defaulting to English.
    """
    s = (name or "").strip().lower()
    if s.startswith("ar"):  # 'arabic' or 'ar'
        return "Arabic"
    return "English"

def _pick_tts_model(language: Optional[str]) -> str:
    lang = (language or "en").lower()
    if lang in ("en", "en-us", "en-gb"):
        return os.getenv("ELEVEN_TTS_MODEL_ID_EN", "eleven_turbo_v2")
    return os.getenv("ELEVEN_TTS_MODEL_ID_MULTI", "eleven_turbo_v2_5")


def create_instant_voice_clone(voice_name: str, file_paths: List[str]) -> str:
    files = []
    for p in file_paths:
        mime = "audio/mpeg"
        pl = p.lower()
        if pl.endswith(".wav"):
            mime = "audio/wav"
        elif pl.endswith(".webm"):
            mime = "audio/webm"
        elif pl.endswith(".ogg"):
            mime = "audio/ogg"
        elif pl.endswith(".m4a"):
            mime = "audio/mp4"
        files.append(("files", (os.path.basename(p), open(p, "rb"), mime)))
    resp = requests.post(f"{BASE_URL}/v1/voices/add", headers=_headers(), files=files, data={"name": voice_name,"remove_background_noise":True}, timeout=120)
    for _, (fn, fh, _) in files:
        try:
            fh.close()
        except:
            pass
    resp.raise_for_status()
    j = resp.json()
    voice_id = j.get("voice_id") or (j.get("voice") or {}).get("voice_id")
    if not voice_id:
        raise RuntimeError(f"Unexpected ElevenLabs response (no voice_id): {j}")
    return voice_id



def create_agent(voice_id: Optional[str], name: str, first_message: str, language: str) -> str:
    v_id = voice_id or os.getenv("ELEVEN_DEFAULT_VOICE_ID")
    if not v_id:
        raise RuntimeError("No voice_id provided and ELEVEN_DEFAULT_VOICE_ID not set")
    model_id = _pick_tts_model(language)
    payload = {
        "conversation_config": {
            "conversation": {"text_only": False, "max_duration_seconds": 900},
            "tts": {"voice_id": v_id, "model_id": model_id},
            "agent": {"first_message": first_message or "", "language": language or "en"},
            "asr": {"provider": "elevenlabs", "quality": "high"},
        },
        "name": name,
        "tags": ["vai"],
    }
    resp = requests.post(f"{BASE_URL}/v1/convai/agents/create", headers=_headers(json=True), json=payload, timeout=45)
    if not resp.ok:
        raise RuntimeError(f"Create agent failed {resp.status_code}: {resp.text}")
    return resp.json().get("agent_id")


def transcribe_with_scribe(file_path: str, language_code: Optional[str] = None) -> dict:
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=ELEVEN_API_KEY)
        with open(file_path, "rb") as f:
            result = client.speech_to_text.convert(file=BytesIO(f.read()), model_id="scribe_v1", diarize=True, tag_audio_events=True, language_code=language_code or None)
        return result.model_dump() if hasattr(result, "model_dump") else dict(result)
    except Exception as e:
        return {"error": str(e)}


def delete_voice(voice_id: str) -> None:
    r = requests.delete(f"{BASE_URL}/v1/voices/{voice_id}", headers=_headers(), timeout=30)
    r.raise_for_status()


def tts_to_mp3_bytes(text: str, voice_id: Optional[str] = None) -> bytes:
    v_id = voice_id or os.getenv("ELEVEN_DEFAULT_VOICE_ID")
    if not v_id:
        raise RuntimeError("ELEVEN_DEFAULT_VOICE_ID is not set and no voice_id was provided")
    url = f"{BASE_URL}/v1/text-to-speech/{v_id}"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    payload = {"text": text, "model_id": os.getenv("ELEVEN_TTS_MODEL_ID", "eleven_turbo_v2_5")}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"TTS failed {resp.status_code}: {resp.text}")
    return resp.content


def build_agent_monologue_mp3(text: str, voice_id: Optional[str] = None) -> bytes:
    return tts_to_mp3_bytes(text, voice_id=voice_id)


def build_agent_program_mp3(lines: List[str], line_pause_ms: int = 1200, target_ms: int = 30000, voice_id: Optional[str] = None) -> bytes:
    combined = AudioSegment.silent(duration=300)
    pause = AudioSegment.silent(duration=line_pause_ms)
    for line in lines:
        mp3_bytes = tts_to_mp3_bytes(line, voice_id=voice_id)
        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        combined += seg + pause
        if len(combined) >= target_ms:
            break
    if len(combined) > target_ms:
        combined = combined[:target_ms]
    else:
        combined += AudioSegment.silent(duration=max(0, target_ms - len(combined)))
    out_io = io.BytesIO()
    combined.export(out_io, format="mp3")
    return out_io.getvalue()


def transcribe_with_scribe_http(file_path: str, language_code: Optional[str] = None) -> dict:
    if not ELEVEN_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    url = f"{BASE_URL}/v1/speech-to-text"
    mime = mimetypes.guess_type(file_path)[0] or "audio/wav"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime)}
        data = {"model_id": "scribe_v1", "diarize": "false", "tag_audio_events": "false"}
        if language_code:
            data["language_code"] = language_code
        resp = requests.post(url, headers={"xi-api-key": ELEVEN_API_KEY}, files=files, data=data, timeout=60)
        try:
            js = resp.json()
        except Exception:
            raise RuntimeError(f"Scribe HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.ok:
            raise RuntimeError(f"Scribe {resp.status_code}: {js}")
        return js


def get_conversation_token(agent_id: str) -> str:
    resp = requests.get(f"{BASE_URL}/v1/convai/conversation/token", headers=_headers(), params={"agent_id": agent_id}, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Token fetch failed {resp.status_code}: {resp.text}")
    data = resp.json()
    return data.get("token") or (data.get("conversation") or {}).get("token")
def delete_agent(agent_id: str) -> None:
    r = requests.delete(f"{BASE_URL}/v1/convai/agents/{agent_id}", headers=_headers(), timeout=30)
    if not r.ok:
        raise RuntimeError(f"Delete agent failed {r.status_code}: {r.text}")

def update_agent(agent_id: str, voice_id: str, language: str, first_message: str, prompt: str = "") -> None:
    """
    Update an existing ElevenLabs ConvAI agent in-place.
    """
    model_id = _pick_tts_model(language)
    payload = {
        "conversation_config": {
            "tts": {"voice_id": voice_id, "model_id": model_id},
            "agent": {
                "language": language,
                "first_message": first_message or "",
                "prompt": {"prompt": prompt or ""},
            },
        }
    }
    r = requests.patch(
        f"{BASE_URL}/v1/convai/agents/{agent_id}",
        headers=_headers(json=True),
        json=payload,
        timeout=45,
    )
    if not r.ok:
        raise RuntimeError(f"Update agent failed {r.status_code}: {r.text}")

def create_speaking_agent(voice_id: str, name: str, first_message: str, prompt: str, language: str) -> str:
    model_id = _pick_tts_model(language)
    payload = {
        "conversation_config": {
            "conversation": {"text_only": False, "max_duration_seconds": 900},
            "tts": {"voice_id": voice_id, "model_id": model_id},
            "agent": {"first_message": first_message or "Hello! How can I help?", "language": language or "en", "prompt": {"prompt": prompt or ""}},
            "asr": {"provider": "elevenlabs", "quality": "high"},
        },
        "name": name or "Website Assistant",
        "tags": ["embed", "website"],
    }
    resp = requests.post(f"{BASE_URL}/v1/convai/agents/create", headers=_headers(json=True), json=payload, timeout=45)
    if not resp.ok:
        raise RuntimeError(f"Create agent failed {resp.status_code}: {resp.text}")
    return resp.json().get("agent_id")


class ElevenSimError(RuntimeError):
    pass


def simulate_agent_text(agent_id: str, user_text: str) -> str:
    if not ELEVEN_API_KEY:
        raise ElevenSimError("ELEVENLABS_API_KEY not set")
    url = f"{BASE_URL}/v1/convai/simulate-conversation"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "simulation_specification": {
            "agent_config": {"agent_id": agent_id},
            "simulated_user_config": {"type": "scripted", "user_script": [{"type": "text", "text": user_text}]},
            "turn_config": {"max_turns": 1, "stop_after_first_agent_message": True},
        }
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
    except requests.RequestException as e:
        raise ElevenSimError(f"network error: {e!s}")
    if not r.ok:
        raise ElevenSimError(f"simulate {r.status_code}: {r.text}")
    try:
        js = r.json()
    except Exception:
        raise ElevenSimError("simulate returned non-JSON body")
    conv = js.get("simulated_conversation") or []
    agent_reply = None
    for m in conv:
        role = (m.get("role") or "").lower()
        if role in ("agent", "assistant"):
            t = (m.get("message") or "").strip()
            if t:
                agent_reply = t
                break
    if not agent_reply:
        for m in reversed(conv):
            role = (m.get("role") or "").lower()
            if role in ("agent", "assistant"):
                t = (m.get("message") or "").strip()
                if t:
                    agent_reply = t
                    break
    return agent_reply or "(no response)"

def get_or_create_default_agent(language: str) -> str:
    from ..models import Agent
    is_en = (language or "en").lower().startswith("en")
    name = "Default Clone EN" if is_en else "Default Clone AR"
    lang_code = "en" if is_en else "ar"
    row = Agent.objects.filter(is_system=True, config__language=lang_code).order_by("-id").first()
    if row and row.eleven_agent_id:
        return row.eleven_agent_id
    first_message = "Hi! I’ll ask a few short questions to build your voice clone... What’s your full name?" if lang_code == "en" else "مرحباً! سأطرح عليك أسئلة قصيرة لإنشاء نسخة من صوتك. ما اسمك الكامل؟"
    default_voice_id = os.getenv("ELEVEN_DEFAULT_VOICE_ID") if is_en else os.getenv("ELEVEN_DEFAULT_VOICE_ID_AR")
    agent_id = create_agent(default_voice_id, name=name, first_message=first_message, language=lang_code)
    Agent.objects.create(profile=None, eleven_agent_id=agent_id, config={"name": name, "language": lang_code, "voice_id": default_voice_id}, is_system=True)
    return agent_id