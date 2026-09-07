# Code review — fix/wallet-balance-and-api-retries

Reviewed 2026-09-07. Base `6a5fa82` → head `e8a2b37`, plus uncommitted changes to
`config.py`, `main.py`, `.gitignore`. Tests: 94 passed in 1.6s.

## What's in the branch

- `brokers/indstocks.py` — retry loop around every HTTP request: up to 2 retries with
  1s/2s backoff on connection errors and 500/502/503/504. Order POSTs excluded.
- `brokers/indstocks.py` — balance now read from `detailed_avl_balance.eq_cnc` instead
  of `available_balance`.
- `stock_agent.py` — same retry loop for LLM calls, plus 429 with `Retry-After`, each
  sleep capped at 30s.
- `stock_agent.py` — `_call_openrouter` renamed to `_call_llm`; `api_key` dropped from
  `detect_signal`, `extract_trade`, `analyze_message` and now read from config.
- `config.py` (uncommitted) — `LLM_PROVIDER` switch picking base URL, key and default
  models for Gemini or OpenRouter. Defaults to Gemini.
- `main.py` (uncommitted) — `/status` health check points at the configured provider.
- Tests — new cases for retry-then-succeed, give-up-after-max, no-retry-on-400,
  no-retry-on-order.

The retry loops themselves are clean, and excluding order POSTs from retry is the right
call.

## Serious

### 1. `config.py:13-16` — Gemini is the default but its key exists nowhere

`LLM_PROVIDER` defaults to `"gemini"`, so `LLM_API_KEY` comes from `GEMINI_API_KEY`.
That variable is not in `.env.example`, `docker-compose.yml`, the Dockerfile or anything
under `infra/`. The VM's `.env` was built from `.env.example`, so on the next restart the
bearer token is empty and every LLM call gets a 401. A 401 isn't in `RETRYABLE_STATUSES`,
so it raises straight into the poll handler and Telegram gets one error per message.

Making it worse, `OPENROUTER_API_KEY` went from `os.environ[...]` to
`os.getenv(..., "")`. Every other secret in that file fails fast at import with a named
key. Now a missing key starts cleanly and fails at every runtime call instead.

Fix: default `LLM_PROVIDER` to `openrouter` so the existing `.env` keeps working, and
read the active provider's key with `os.environ[...]` so a bad config dies at boot. Add
`LLM_PROVIDER` and `GEMINI_API_KEY` to `.env.example`.

### 2. `brokers/indstocks.py:121-127` — bad balance payload silently returns zero

`get_balance` is a trade gate, not a display value: `approval_bot.py:204` refuses the
order when balance is below the allocation amount. `float(funds.get("detailed_avl_balance", {}).get("eq_cnc", 0))`
returns `0.0` for any payload shape that doesn't match, and nothing is logged above
DEBUG. Every approval then answers "insufficient funds" with no visible cause.

You're changing this key *because* the old one was wrong, which means the response shape
isn't confirmed. That's exactly when it should raise. Raising also routes it through the
existing error paths — `main.py:112` notifies Telegram, `main.py:173` shows broker DOWN —
which matches the "notify on failure" rule.

No test covers the new key either. The two tests that touch it (`test_broker_indstocks.py:208,226`)
had their fixtures mechanically rewritten to the new shape, which proves nothing about
the real API.

### 3. `brokers/indstocks.py:89-92` — 403 re-auth now sits inside the retry loop

Before this branch, a 403 triggered exactly one `authenticate()` call. Now it's inside
the `for attempt` loop, so a 403-then-5xx sequence re-auths once per attempt: up to 3
auth calls and 6 requests for one logical request. That needs the server to hand back a
token it then rejects, so it's not the common path, but it's the shape of a degraded
gateway, and the auth endpoint only has a cooldown on 429. It's also the exact aggressive
broker traffic the cooldown was added to prevent.

Fix: track a `reauthed` flag so at most one re-auth happens per `_request`.

### 4. `tests/test_broker_indstocks.py:241-256` — the no-retry-on-orders test proves nothing

The test hand-writes `"https://api.indstocks.com/order"` and calls `_request` directly,
so `url.endswith("/order")` is trivially true. It never touches the URL `place_order`
actually builds at `indstocks.py:165`. If that URL ever gains a suffix or a query string
the guard stops matching and duplicate orders start going out — the worst thing this
change could do.

Two fixes, both worth doing. Replace the URL sniffing with an explicit
`_request(..., retry=False)` that `place_order` passes, so the guard can't drift. And
have the test drive `broker.place_order(order)` and assert one call. There's also no test
for a transport error on the order path, which is the likelier duplicate-order case: a
connection reset can land after the broker already accepted the order.

## Moderate

### 5. `stock_agent.py:87` — `Retry-After` as an HTTP date crashes the retry

`float(resp.headers.get("Retry-After", delay))` raises `ValueError` on the
`Wed, 21 Oct 2015 07:28:00 GMT` form, which RFC 9110 allows and Cloudflare-fronted
responses do send. `ValueError` isn't caught anywhere in `_call_llm`, so a recoverable
rate limit becomes an unhandled exception. Same shape at `brokers/indstocks.py:59,95`,
though those pre-date this branch. Parse defensively and fall back to the computed
backoff.

### 6. `stock_agent.py:9` — tests only collect because a real `.env` is sitting there

`from config import ...` at module level means importing `stock_agent` requires every
mandatory env var. `env -i python -c "import stock_agent"` dies with
`KeyError: 'TELEGRAM_API_ID'`. There is no `tests/conftest.py` and `pytest.ini` sets no
env, so `test_stock_agent.py` and `test_integration.py` collect only because the
production `.env` happens to be in the working directory. A clean checkout, a CI runner
or a container build fails at collection.

This doesn't affect the running app — `main.py` imports config anyway. Fix with a
`tests/conftest.py` that sets dummy values before import. `load_dotenv()` doesn't
override existing env vars, so that also makes the tests deterministic.

### 7. A single poll iteration can now block for minutes

`_MAX_DELAY` caps each individual sleep at 30s, not the whole call. Each LLM attempt also
carries its own `timeout=30`. Worst case per message is roughly 3 minutes across two
tiers, before any broker retries. `poll_channels` processes messages serially in one
task, so a handful of rate-limited messages stalls the loop past the 10-minute poll
interval and APScheduler starts skipping runs. Consider a per-poll deadline, or cap the
LLM `Retry-After` well below 30s given the loop only runs every 10 minutes anyway.

### 8. Commits carry work their messages don't mention

`c44cea2 "feat: retry transient broker errors with backoff"` also rewrites balance
parsing. `e8a2b37 "feat: retry transient LLM errors with backoff"` also does the whole
OpenRouter-to-multi-provider migration: the rename, dropping `api_key` from four public
functions, and changing the `db.api_costs.service` value. None of that is in
`docs/superpowers/plans/2026-09-07-exponential-backoff-retries.md`, which scoped the
change to "no call site changes". Reverting the retry feature would silently revert the
provider switch. Worth splitting before merge.

## Nits

- `stock_agent.py:64-93` and `brokers/indstocks.py:77-103` are the same 18-line loop
  twice, with different retryable-status sets and separately declared but identical
  `_MAX_RETRIES` / `_BASE_DELAY`. Nothing stops one drifting from the other.
- No test sends a 429 carrying `Retry-After` to the LLM path, so `_MAX_DELAY` and the
  header parse are both unexercised. `test_integration.py:435` uses a bare 429, which
  takes the plain backoff branch.
- `main.py:188,194` computes `config.LLM_PROVIDER.capitalize()` twice and renders
  OpenRouter as "Openrouter".
- `stock_agent.py:111` — `service` changes from the literal `"openrouter"` to
  `config.LLM_PROVIDER`. `get_costs_by_service` groups by that column, so old and new
  rows split into separate buckets.
- `stock_agent.py:9` — the import sits below the logger rather than in the import block.
- `tests/test_stock_agent.py:334` — `monkeypatch.setattr("stock_agent._BASE_DELAY", 0)`
  in `test_detect_signal_no_retry_on_400` is dead; no sleep is reachable on that path.
  The three new tests also use `client.post = AsyncMock(...)` while the rest of the file
  uses `client.post.return_value`.
- Stale docs: `README.md:50,70,135,155`, `CLAUDE.md:3,57`, `docs/ARCHITECTURE.md:19,105`,
  `setup_guide.md:38` still reference `_call_openrouter` and OpenRouter-only config. The
  `# ---- R2-M9: HTTP error from openrouter ----` comment at `test_integration.py:432`
  too.

## Rejected from the first pass

Two points from the initial review don't hold up.

`stock_agent.py:101` cost tracking. The claim was that switching to Gemini breaks it.
It was already broken: `usage.get("cost", 0) or float(resp.headers.get("x-cost", 0))`
returns 0 for OpenRouter too, because OpenRouter only includes `usage.cost` when the
request asks for it and this one doesn't. So `cost_usd` has always been 0 and this branch
changes nothing. Still worth fixing, but it's not a regression here.

The `.gitignore` addition is fine. `docs/SECURITY_AUDIT_*.md` was never committed, so
ignoring it is enough and no history rewrite is needed.
