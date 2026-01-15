def simple_ai_reply(user_text: str) -> str:
    """
    SUPER simple reply generator. Replace with an LLM later if you want.
    Keeps agent replies short (1–2 sentences).
    """
    txt = (user_text or "").strip().lower()
    if not txt:
        return "I didn’t catch that. Could you repeat briefly?"

    if "name" in txt or "i am" in txt or "i'm" in txt:
        return "Nice to meet you! What kind of work are you focused on lately?"

    if "project" in txt or "working" in txt or "build" in txt:
        return "That sounds interesting. What’s the main challenge you’re solving?"

    if "from" in txt or "based" in txt or "live" in txt:
        return "Cool! How does your location influence your work or routine?"

    # default: acknowledge + keep them talking
    return "Got it. Tell me a bit more in your own words."
