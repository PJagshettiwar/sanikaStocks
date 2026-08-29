# Setup Guide

## Prerequisites

1. Python 3.12+
2. Docker (optional, for containerized deployment)

## Step 1: Telegram User Account API

1. Go to https://my.telegram.org
2. Log in with your phone number
3. Go to "API development tools"
4. Create an application, note API_ID and API_HASH

## Step 2: Create Approval Bot

1. Open Telegram, search for @BotFather
2. Send /newbot, follow prompts
3. Note the bot token
4. Start a chat with your new bot (send /start)
5. Get your chat ID: send a message to the bot, then visit
   https://api.telegram.org/bot<TOKEN>/getUpdates
   and find chat.id in the response

## Step 3: Get Channel IDs

For each of your 4 private channels:
1. Open Telegram Web (web.telegram.org)
2. Navigate to the channel
3. The URL will show the channel ID (e.g., -1001234567890)

## Step 4: INDstocks API Token

1. Log in to indstocks.com
2. Navigate to API section
3. Copy your access token (expires every 24h)

## Step 5: OpenRouter API Key

1. Go to https://openrouter.ai
2. Create an account
3. Purchase $10 credit (unlocks 1000 req/day on free models)
4. Copy your API key

## Step 6: Configure .env

Copy .env.example to .env and fill in all values.

## Step 7: First Run (Local)

pip install -r requirements.txt
python main.py

On first run, Telethon will ask for your phone number and
verification code to create the session file.

## Step 8: Docker Deployment

After the session file is created locally:

docker-compose up -d --build
docker-compose logs -f stock-agent
