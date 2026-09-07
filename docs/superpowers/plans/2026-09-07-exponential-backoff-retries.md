# Exponential Backoff Retries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry transient failures on the two external services the app actually depends on, so a network blip doesn't silently drop a trade signal.

**Architecture:** Retry lives inside the two methods every external call already flows through: `INDstocksBroker._request()` and `stock_agent._call_llm()`. No call site changes.

**Tech Stack:** Python asyncio, httpx. No new dependencies.

## Global Constraints

- 2 retries (3 attempts total). Delays 1s then 2s. Constants are module-level so tests can zero them.
- Retry on `httpx.TransportError` and status 500, 502, 503, 504.
- Never retry `POST /order`. A failed order may have reached the broker.
- **429 is handled differently per service, deliberately:**
  - Broker: raises `RateLimitError` immediately, no retry. Commit `f2c7915` added this with a persistent cooldown marker (`data/.auth_cooldown`) to stop the app hammering INDstocks auth. Do not fold 429 into the broker's retryable set.
  - LLM: retried, honouring `Retry-After`, capped at 30s so a large header value can't stall the poll loop.
- `authenticate()` is intentionally left without retry. It bypasses `_request` by design, and startup already retries it at `main.py:290-305`. A second retry layer would work against the `f2c7915` cooldown.
- Telegram is out of scope. Telethon already retries: `request_retries=5`, `connection_retries=5`, and `flood_sleep_threshold=60` auto-sleeps FloodWait up to a minute (`telethon/client/telegrambaseclient.py:255-260`).
- `market_data.py` is out of scope. `market_data.get_quote` has no production caller; every quote goes through `broker.get_quote` (`risk_engine.py:77`, `approval_bot.py:218`, `main.py:147`). Adding retry there is dead code. Do not create `tests/test_market_data.py` — it already exists with 3 passing tests.

---

### Task 1: Retry in INDstocksBroker._request()

**Files:**
- Modify: `brokers/indstocks.py` — add module constants above the class, replace `_request` (lines 69-80)
- Test: `tests/test_broker_indstocks.py` (append)

**Interfaces:**
- Consumes: `_request(method, url, **kwargs)` — signature unchanged
- Produces: same return type. Retries transient errors. 403 re-auth and 429 behaviour unchanged from today.

- [ ] **Step 1: Write the three failing tests**

Append to `tests/test_broker_indstocks.py`:

```python
@pytest.mark.asyncio
async def test_request_retries_on_500(monkeypatch):
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, json={"message": "Internal Server Error"},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    success_resp = httpx.Response(
        200, json={"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 25000}}},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[error_resp, success_resp])

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    assert await broker.get_balance() == 25000
    assert client.request.call_count == 2


@pytest.mark.asyncio
async def test_request_retries_on_network_error(monkeypatch):
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    success_resp = httpx.Response(
        200, json={"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 10000}}},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[httpx.ConnectError("connection refused"), success_resp])

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    assert await broker.get_balance() == 10000
    assert client.request.call_count == 2


@pytest.mark.asyncio
async def test_request_does_not_retry_order_post(monkeypatch):
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, json={"message": "Internal Server Error"},
        request=httpx.Request("POST", "https://api.indstocks.com/order"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=error_resp)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    with pytest.raises(httpx.HTTPStatusError):
        await broker._request("POST", "https://api.indstocks.com/order")
    assert client.request.call_count == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_broker_indstocks.py -k "retries_on_500 or retries_on_network_error or does_not_retry_order" -v`
Expected: the two retry tests FAIL (only 1 call made, error raised immediately). The order test may already pass — that is fine, it is a guard against the change.

- [ ] **Step 3: Add module constants**

In `brokers/indstocks.py`, below `log = logging.getLogger(__name__)` (line 15) and above `class RateLimitError`:

```python
RETRYABLE_STATUSES = {500, 502, 503, 504}
_MAX_RETRIES = 2
_BASE_DELAY = 1.0
```

- [ ] **Step 4: Replace _request**

Replace the whole `_request` method (currently lines 69-80):

```python
    async def _request(self, method: str, url: str, **kwargs):
        await self._ensure_auth()
        is_order = method == "POST" and url.endswith("/order")

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.request(method, url, headers=self._headers, **kwargs)
            except httpx.TransportError as e:
                if is_order or attempt == _MAX_RETRIES:
                    raise
                delay = _BASE_DELAY * (2 ** attempt)
                log.warning("Request to %s failed (attempt %d/%d): %s. Retrying in %.1fs",
                            url, attempt + 1, _MAX_RETRIES + 1, e, delay)
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 403:
                log.info("Token expired, re-authenticating")
                await self.authenticate()
                resp = await self._client.request(method, url, headers=self._headers, **kwargs)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", self.AUTH_COOLDOWN))
                raise RateLimitError(retry_after)

            if resp.status_code in RETRYABLE_STATUSES and not is_order and attempt < _MAX_RETRIES:
                delay = _BASE_DELAY * (2 ** attempt)
                log.warning("Request to %s returned %d (attempt %d/%d). Retrying in %.1fs",
                            url, resp.status_code, attempt + 1, _MAX_RETRIES + 1, delay)
                await asyncio.sleep(delay)
                continue

            resp.raise_for_status()
            return resp
```

Two things to keep exactly as written. The 403 block has no `is_order` guard — a 403 means the request was rejected, not processed, so re-auth-and-retry is safe for orders and this preserves today's behaviour. There is no `raise` after the loop: on the final attempt the transport path re-raises and the status path falls through to `raise_for_status()`, so the loop always exits by return or raise.

`asyncio` is already imported at line 1. No import change needed.

- [ ] **Step 5: Run the broker suite**

Run: `.venv/bin/python -m pytest tests/test_broker_indstocks.py -v`
Expected: 12 passed. The pre-existing `test_403_triggers_reauth_and_retry`, `test_request_429_raises_rate_limit_error` and the two auth 429 tests must still pass — they prove the `f2c7915` behaviour survived.

- [ ] **Step 6: Commit**

```bash
git add brokers/indstocks.py tests/test_broker_indstocks.py
git commit -m "feat: retry transient broker errors with backoff"
```

---

### Task 2: Retry in _call_llm()

**Files:**
- Modify: `stock_agent.py` — add `import asyncio` at line 1, add module constants, replace `_call_llm` (lines 55-98)
- Test: `tests/test_stock_agent.py` (append)

**Interfaces:**
- Consumes: `_call_llm(messages, model, http_client, context=None)` — signature unchanged
- Produces: parsed JSON dict or `None`, same as today. Raises `httpx.HTTPStatusError` after retries are exhausted.

- [ ] **Step 1: Write the three failing tests**

Append to `tests/test_stock_agent.py`:

```python
@pytest.mark.asyncio
async def test_detect_signal_retries_on_500(monkeypatch):
    monkeypatch.setattr("stock_agent._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, text="Internal Server Error",
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=[
        error_resp,
        _mock_llm_response('{"is_tip": true, "confidence": 0.92}'),
    ])

    result = await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert result["is_tip"] is True
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_detect_signal_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr("stock_agent._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, text="Internal Server Error",
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=error_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert client.post.call_count == 3


@pytest.mark.asyncio
async def test_detect_signal_no_retry_on_400(monkeypatch):
    monkeypatch.setattr("stock_agent._BASE_DELAY", 0)
    error_resp = httpx.Response(
        400, text="Bad Request",
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=error_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert client.post.call_count == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stock_agent.py -k "retries_on_500 or raises_after_max_retries or no_retry_on_400" -v`
Expected: the first two FAIL (1 call, immediate raise). The 400 test may already pass.

- [ ] **Step 3: Add the import and constants**

In `stock_agent.py`, add `import asyncio` as the first line (the file currently imports only `json`, `logging`, `httpx`).

Below `CONFIDENCE_THRESHOLD = 0.6` (line 38), add:

```python
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0
```

- [ ] **Step 4: Replace _call_llm**

Replace the whole function (currently lines 55-98). Everything from `if resp.status_code != 200:` down is unchanged from today — only the request itself is now wrapped in the loop:

```python
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
                delay = min(float(resp.headers.get("Retry-After", delay)), _MAX_DELAY)
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
```

The `min(..., _MAX_DELAY)` on the `Retry-After` line matters. Gemini can return a 429 with a multi-minute `Retry-After`, and without the cap the poll loop would block for that long.

- [ ] **Step 5: Run the agent suite**

Run: `.venv/bin/python -m pytest tests/test_stock_agent.py -v`
Expected: 17 passed. The cost-tracking path is exercised by the existing tests, so a passing suite confirms the tail of the function survived the edit intact.

- [ ] **Step 6: Run everything and check the clock**

Run: `.venv/bin/python -m pytest -q`
Expected: 94 passed, in under 3 seconds. The baseline is 88 tests in 1.96s. If the run takes 10s or more, a `monkeypatch.setattr` line is missing from one of the new tests.

- [ ] **Step 7: Commit**

```bash
git add stock_agent.py tests/test_stock_agent.py
git commit -m "feat: retry transient LLM errors with backoff"
```
