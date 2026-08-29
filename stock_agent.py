import json
import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TIER1_SYSTEM_PROMPT = """You are a stock tip detector for Indian stock markets (NSE/BSE).
Respond ONLY with JSON: {"is_tip": true/false, "confidence": 0.0-1.0}
A stock tip contains a buy/sell recommendation with a specific stock name and at least one of: entry price, stop-loss, or target.
General market commentary, news, greetings, or discussion is NOT a tip."""

TIER2_SYSTEM_PROMPT = """You are a stock trade signal extractor for Indian markets (NSE/BSE).
Extract the trade signal from the message and return ONLY valid JSON with this exact structure:
{
  "symbol": "TRADING_SYMBOL (e.g. RELIANCE, INFY, TCS)",
  "exchange": "NSE or BSE",
  "action": "BUY or SELL",
  "entry_min": <number>,
  "entry_max": <number>,
  "stop_loss": <number or null>,
  "targets": [<number>, ...],
  "allocation_pct": <number or null>,
  "confidence": <0.0-1.0>,
  "reasoning": "<one line explanation>"
}
Rules:
- Use the NSE trading symbol (e.g., "Reliance Industries" -> "RELIANCE", "Infosys" -> "INFY")
- If only one entry price is given, use it for both entry_min and entry_max
- If stop-loss is not mentioned, set it to null
- If allocation percentage is not mentioned, set it to null
- If you cannot determine the symbol or entry price, return null
- Do NOT wrap in markdown code blocks"""

CONFIDENCE_THRESHOLD = 0.6

_cost_tracker = {"calls": 0, "total_tokens": 0, "cost_usd": 0.0, "db_conn": None}


def set_cost_db(conn):
    _cost_tracker["db_conn"] = conn


def get_session_costs():
    return {
        "calls": _cost_tracker["calls"],
        "total_tokens": _cost_tracker["total_tokens"],
        "cost_usd": _cost_tracker["cost_usd"],
    }


async def _call_openrouter(messages, api_key, model, http_client, context=None):
    resp = await http_client.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": 0.1},
        timeout=30,
    )
    if resp.status_code != 200:
        import logging
        logging.error(f"OpenRouter error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage", {})

    _cost_tracker["calls"] += 1
    _cost_tracker["total_tokens"] += usage.get("total_tokens", 0)
    _cost_tracker["cost_usd"] += usage.get("cost", 0)

    conn = _cost_tracker["db_conn"]
    if conn:
        from db import save_api_cost
        await save_api_cost(
            conn, service="openrouter", model=model, endpoint="chat/completions",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=usage.get("cost", 0),
            context=context,
        )

    content = data["choices"][0]["message"]["content"]
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(content)


async def detect_signal(text, api_key, model, http_client):
    messages = [
        {"role": "system", "content": TIER1_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    return await _call_openrouter(messages, api_key, model, http_client, context="tier1_detect")


async def extract_trade(text, context_messages, api_key, model, http_client):
    context_block = ""
    if context_messages:
        context_block = "Recent messages from the same channel for context:\n" + "\n".join(f"- {m}" for m in context_messages) + "\n\n"
    user_content = f"{context_block}Extract the trade signal from this message:\n{text}"
    messages = [
        {"role": "system", "content": TIER2_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result = await _call_openrouter(messages, api_key, model, http_client, context="tier2_extract")
    if result is None or not isinstance(result, dict):
        return None
    if not result.get("symbol") or result.get("entry_min") is None:
        return None
    return result


async def analyze_message(text, context_messages, api_key, tier1_model, tier2_model, http_client):
    detection = await detect_signal(text, api_key, tier1_model, http_client)
    if not detection or not detection.get("is_tip") or detection.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        return None
    return await extract_trade(text, context_messages, api_key, tier2_model, http_client)
