from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

logger = logging.getLogger(__name__)

# ==== Constants mirrored from your original file ====
ELEVEN_API = "https://api.elevenlabs.io"
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = 15

MAX_PAGES = 6
MAX_CHARS_PER_PAGE = 6000
FETCH_TIMEOUT = 10

EMBED_DEV_ALLOW_LOCALHOST = os.getenv("EMBED_DEV_ALLOW_LOCALHOST", "1") == "1"

AUTH_CLASSES = [JWTAuthentication, SessionAuthentication, BasicAuthentication]


# ==== Small helpers ====
def _cors(resp: HttpResponse, origin: Optional[str] = None) -> HttpResponse:
    """Later definition in your file (GET, POST, OPTIONS) — preserved."""
    o = origin or "*"
    resp["Access-Control-Allow-Origin"] = o
    resp["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp["Vary"] = "Origin"
    return resp


def _clean_origin(s: str) -> str:
    u = urlparse((s or "").strip())
    scheme = u.scheme or "https"
    netloc = u.netloc or u.path
    return f"{scheme}://{netloc}".lower()


def _safe_json(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None


def _fetch_clean_text(url: str) -> str:
    try:
        r = requests.get(
            url, timeout=FETCH_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()
        main = soup.find(["main", "article"]) or soup.body or soup
        txt = main.get_text(separator="\n", strip=True)
        return re.sub(r"\n{2,}", "\n", txt).strip()[:MAX_CHARS_PER_PAGE]
    except Exception as e:
        logger.warning("Fetch failed %s: %s", url, e)
        return f"[FETCH_ERROR] {url}: {e}"


def _fetch_many(urls: List[str]) -> List[dict]:
    urls = [u for u in urls if isinstance(u, str) and u.strip()]
    out: List[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(urls))) as ex:
        futs = {ex.submit(_fetch_clean_text, u): u for u in urls[:MAX_PAGES]}
        for fut in concurrent.futures.as_completed(futs):
            u = futs[fut]
            try:
                out.append({"url": u, "text": fut.result()})
            except Exception as e:
                out.append({"url": u, "text": f"[FETCH_ERROR] {u}: {e}"})
    order = {u: i for i, u in enumerate(urls)}
    out.sort(key=lambda d: order.get(d["url"], 1e9))
    return out


def _cache_key_for_prompt(links: List[str], display_name: str, website_origin: str, language: str) -> str:
    h = hashlib.sha256(
        json.dumps(
            {"links": links, "name": display_name, "site": website_origin, "lang": language},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"prompt:{h}"


def _build_prompt_from_pages(
    display_name: str, website_origin: str, language: str, pages: List[dict]
) -> str:
    """
    Same logic as your original (with caching & OpenAI call).
    Fallback path unchanged.
    """
    def fallback():
        facts = "\n".join(
            f"- ({p['url']}) {p['text'][:220].replace('\n', ' ')}…" for p in pages
        )
        return (
            f"You are {display_name} for {website_origin or 'the site'}.\n"
            f"1) Role & Tone: Friendly, concise, jargon-free. Don’t invent facts.\n"
            f"2) Core Capabilities: Answer FAQs, pricing, features, onboarding; offer links; collect contact info.\n"
            f"   Site facts:\n{facts}\n"
            f"3) Safety & Escalation: If unsure, say so and provide support/booking.\n"
            f"4) Style: {language}; short paragraphs; bullets for steps."
        )

    if not OPENAI_API_KEY:
        return fallback()

    links = [p["url"] for p in pages]
    ck = _cache_key_for_prompt(links, display_name, website_origin, language)
    cached = cache.get(ck)
    if cached:
        return cached

    content = "\n\n".join(
        f"### {i+1}. {p['url']}\n{p['text']}" for i, p in enumerate(pages)
    )[:20000]
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are the website assistant '{display_name}' for {website_origin or 'the site'}."
                    f" Produce ONE system prompt (≤ 2500 chars) with sections: 1) Role & Tone, 2) Core Capabilities,"
                    f" 3) Safety & Escalation, 4) Style Rules. Use language: {language}."
                ),
            },
            {
                "role": "user",
                "content": f"Page extracts:\n\n{content}\n\nReturn ONLY the final system prompt.",
            },
        ],
        "temperature": 0.2,
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps(body),
            timeout=OPENAI_TIMEOUT,
        )
        r.raise_for_status()
        j = r.json()
        prompt = (j["choices"][0]["message"]["content"] or "").strip()
        if prompt:
            cache.set(ck, prompt, timeout=6 * 60 * 60)
        return prompt or fallback()
    except Exception as e:
        logger.warning("OpenAI failed: %s", e)
        return fallback()
