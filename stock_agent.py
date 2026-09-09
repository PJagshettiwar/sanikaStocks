import asyncio
import json
import logging

import httpx

log = logging.getLogger("stock_agent")

from config import LLM_BASE_URL, LLM_API_KEY, LLM_PROVIDER

TIER1_SYSTEM_PROMPT = """You are a stock tip detector for Indian stock markets (NSE).
Respond ONLY with JSON: {"is_tip": true/false, "confidence": 0.0-1.0}
A stock tip contains a buy/sell recommendation with a specific stock name and at least one of: entry price, stop-loss, or target.
Messages saying "exit", "sell", "book profits", or "book partial profits" with a stock name ARE tips even without a price. Return is_tip: true for these.
General market commentary, news, greetings, or discussion is NOT a tip.
Messages saying "hold", "continue to hold", or "trail SL" are NOT new tips. Return is_tip: false for these."""

TIER2_SYSTEM_PROMPT = """You are a stock trade signal extractor for Indian markets (NSE).
Extract the trade signal from the message and return ONLY valid JSON with this exact structure:
{
  "symbol": "TRADING_SYMBOL (e.g. RELIANCE, INFY, TCS)",
  "exchange": "NSE",
  "action": "BUY or SELL",
  "sell_pct": <1-100, only for SELL, default 100>,
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
- For SELL/exit messages: set entry_min and entry_max to 0, stop_loss to null, targets to []
- sell_pct is the percentage to sell (e.g. "exit 50%" = 50, "exit" = 100, "book partial profits" = 50)
- Do NOT wrap in markdown code blocks"""

CONFIDENCE_THRESHOLD = 0.6

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2
_BASE_DELAY = 1.0
_MAX_DELAY = 10.0


def _retry_after(resp, fallback: float) -> float:
    """RFC 9110 allows Retry-After to be an HTTP date, which float() can't parse."""
    try:
        return float(resp.headers.get("Retry-After", fallback))
    except (TypeError, ValueError):
        return fallback

_cost_tracker = {"calls": 0, "total_tokens": 0, "cost_usd": 0.0, "db_conn": None}


def set_cost_db(conn):
    _cost_tracker["db_conn"] = conn


def get_session_costs():
    return {
        "calls": _cost_tracker["calls"],
        "total_tokens": _cost_tracker["total_tokens"],
        "cost_usd": _cost_tracker["cost_usd"],
    }


async def _call_llm(messages, model, http_client, context=None):
    url = f"{LLM_BASE_URL}/chat/completions"

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await http_client.post(
                url,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages, "temperature": 0.1},
                timeout=30,
            )
        except httpx.TransportError as e:
            if attempt == _MAX_RETRIES:
                raise
            delay = _BASE_DELAY * (2 ** attempt)
            log.warning("LLM request failed (attempt %d/%d): %s. Retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES + 1, e, delay)
            await asyncio.sleep(delay)
            continue

        if resp.status_code in RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
            delay = _BASE_DELAY * (2 ** attempt)
            if resp.status_code == 429:
                delay = min(_retry_after(resp, delay), _MAX_DELAY)
            log.warning("LLM error %d (attempt %d/%d). Retrying in %.1fs",
                        resp.status_code, attempt + 1, _MAX_RETRIES + 1, delay)
            await asyncio.sleep(delay)
            continue

        break

    if resp.status_code != 200:
        log.error("LLM error %d: %s", resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage", {})

    cost = usage.get("cost", 0) or float(resp.headers.get("x-cost", 0))

    _cost_tracker["calls"] += 1
    _cost_tracker["total_tokens"] += usage.get("total_tokens", 0)
    _cost_tracker["cost_usd"] += cost

    conn = _cost_tracker["db_conn"]
    if conn:
        from db import save_api_cost
        await save_api_cost(
            conn, service=LLM_PROVIDER, model=model, endpoint="chat/completions",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=cost,
            context=context,
        )

    content = data["choices"][0]["message"]["content"]
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        log.warning("LLM returned non-JSON (%s, %s): %.200s", model, context, content)
        return None


async def detect_signal(text, model, http_client, context="tier1_detect"):
    messages = [
        {"role": "system", "content": TIER1_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    return await _call_llm(messages, model, http_client, context=context)


async def extract_trade(text, context_messages, model, http_client):
    context_block = ""
    if context_messages:
        context_block = "Recent messages from the same channel for context:\n" + "\n".join(f"- {m}" for m in context_messages) + "\n\n"
    user_content = f"{context_block}Extract the trade signal from this message:\n{text}"
    messages = [
        {"role": "system", "content": TIER2_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result = await _call_llm(messages, model, http_client, context="tier2_extract")
    if result is None or not isinstance(result, dict):
        return None
    if not result.get("symbol"):
        return None
    if result.get("action") not in ("BUY", "SELL"):
        log.warning("LLM returned invalid action: %s", result.get("action"))
        return None
    if result.get("exchange") != "NSE":
        log.warning("LLM returned non-NSE exchange: %s, overriding to NSE", result.get("exchange"))
        result["exchange"] = "NSE"

    is_sell = result["action"] == "SELL"

    if is_sell:
        result["entry_min"] = 0
        result["entry_max"] = 0
        result["stop_loss"] = None
        result["targets"] = []
        sell_pct = result.get("sell_pct", 100)
        if not isinstance(sell_pct, (int, float)) or sell_pct < 1 or sell_pct > 100:
            sell_pct = 100
        result["sell_pct"] = int(sell_pct)
    else:
        if result.get("entry_min") is None:
            return None
        if not isinstance(result.get("entry_min"), (int, float)) or result["entry_min"] <= 0:
            log.warning("LLM returned invalid entry_min: %s", result.get("entry_min"))
            return None
        if not isinstance(result.get("entry_max"), (int, float)) or result["entry_max"] <= 0:
            log.warning("LLM returned invalid entry_max: %s", result.get("entry_max"))
            return None
        if result["entry_min"] > result["entry_max"]:
            result["entry_min"], result["entry_max"] = result["entry_max"], result["entry_min"]
        result["sell_pct"] = 0

    confidence = result.get("confidence", 0)
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        log.warning("LLM returned invalid confidence: %s", confidence)
        return None
    targets = result.get("targets", [])
    if not isinstance(targets, list):
        result["targets"] = []
    else:
        result["targets"] = [t for t in targets if isinstance(t, (int, float)) and 0 < t < 1_000_000]
    return result


async def analyze_message(text, context_messages, tier1_model, tier2_model, http_client):
    detection = await detect_signal(text, tier1_model, http_client)
    if not detection or not detection.get("is_tip") or detection.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        return None
    return await extract_trade(text, context_messages, tier2_model, http_client)
