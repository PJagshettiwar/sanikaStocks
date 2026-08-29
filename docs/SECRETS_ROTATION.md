# Secrets Rotation Guide

Step-by-step instructions to rotate every credential used by sanikaStocks.

## When to Rotate

- After any review session where `.env` contents were visible
- If you suspect credential compromise
- Periodically (every 90 days recommended)

## Rotation Steps

### 1. Telegram Bot Token

1. Open Telegram, message @BotFather
2. Send `/mybots`, select your bot
3. Select "API Token" > "Revoke current token"
4. Copy the new token
5. Update `.env`: `TELEGRAM_BOT_TOKEN=<new-token>`

The old token stops working immediately. Your bot will disconnect and reconnect with the new token on next restart.

### 2. Telegram API ID and Hash

These rarely need rotation since they identify your Telegram application, not a session.

1. Go to https://my.telegram.org/apps
2. Log in with your phone number
3. If you need to rotate: delete the app and create a new one
4. Update `.env`: `TELEGRAM_API_ID=<new-id>` and `TELEGRAM_API_HASH=<new-hash>`
5. Delete the old session file: `rm stock_agent.session stock_agent.session-journal`
6. Re-authenticate on next startup (you'll need to enter your phone number and code)

### 3. OpenRouter API Key

1. Go to https://openrouter.ai/settings/keys
2. Click "Create Key" to generate a new one
3. Delete the old key
4. Update `.env`: `OPENROUTER_API_KEY=<new-key>`

### 4. INDstocks Client ID

1. Log in to INDstocks dashboard
2. Navigate to API settings
3. Generate a new API client/key
4. Revoke the old one
5. Update `.env`: `INDSTOCKS_CLIENT_ID=<new-client-id>`

### 5. INDstocks TOTP Secret

This is the most sensitive credential. It bypasses 2FA.

1. Log in to INDstocks
2. Go to Security > Two-Factor Authentication
3. Disable 2FA, then re-enable to get a new secret
4. Scan the QR code with your authenticator app
5. Copy the secret key (the text version of the QR code)
6. Update `.env`: `INDSTOCKS_TOTP_SECRET=<new-secret>`
7. Verify: the bot should be able to authenticate on next restart

### 6. INDstocks MPIN

1. Log in to INDstocks
2. Go to Settings > Change MPIN
3. Set a new MPIN
4. Update `.env`: `INDSTOCKS_MPIN=<new-mpin>`

## After Rotation

1. Restart the bot: `docker-compose down && docker-compose up -d`
2. Send `/status` in the approval chat to verify all services connect
3. Verify the session file was recreated if you rotated Telegram credentials

## Keeping Secrets Safe

### Current protections (already in place)
- `.env` is in `.gitignore` (never committed to git)
- `.env` was never in git history (verified)
- Session files are gitignored

### Additional protections added
- `.dockerignore` prevents secrets from being baked into Docker images
- `.env.example` has all fields (so nobody shares the real `.env` for onboarding)

### Best practices
- Never share `.env` via Slack, email, or any messaging app
- Never paste credentials into AI chat sessions or logs
- Set file permissions: `chmod 600 .env stock_agent.session`
- If deploying to a server, use Docker secrets or environment injection from a vault instead of `env_file`
- If you must share credentials for debugging, use a temporary credential and rotate immediately after
