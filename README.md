# Bybit Futures Signal Bot

A Telegram bot that monitors 12 crypto futures pairs and sends **confirmed trading signals** — only when both the 5-minute and 15-minute timeframes agree on the same direction.

## Pairs
BTC, ETH, SOL, BNB, XRP, TAO, HYPE, ZEC, ARB, OP, AVAX, DOGE (all USDT perpetual)

## Indicators
- MA7, MA14, MA28 (moving averages)
- RSI(14)

## Signal Logic
- **BUY**: MA7 crosses above MA14 (or MA7>MA14>MA28 uptrend) + RSI 40–70, confirmed on both 5m and 15m
- **SELL**: MA7 crosses below MA14 (or MA7<MA14<MA28 downtrend) + RSI 30–60, confirmed on both 5m and 15m
- Conflicting timeframes are **silently skipped**

## Signal Format
Each alert includes:
- Entry price, Stop Loss, TP1 (1.5R), TP2 (3.0R)
- RSI and MA values
- ✅ 5m+15m confirmation badge

## Telegram Commands
| Command | Description |
|---------|-------------|
| `/start` | Show bot info and commands |
| `/status` | Live snapshot of all 12 pairs with current signal |
| `/scan` | Force an immediate full scan and send signals |
| `/help` | Show help message |

## Setup

### Environment Variables
Copy `.env.example` to `.env` and fill in your values:
```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_or_channel_id
BYBIT_API_KEY=your_bybit_api_key
BYBIT_API_SECRET=your_bybit_api_secret
```

> **Note:** Market data is fetched from KuCoin's public API (Bybit's CDN blocks certain datacenter IP ranges). Prices are effectively identical for these major pairs.

### Run Locally
```bash
pip install -r requirements.txt
python3 bot/signals.py
```

### Deploy to Railway
1. Push this repo to GitHub
2. Create a new project on [Railway](https://railway.app)
3. Connect your GitHub repo
4. Add environment variables in Railway's settings panel
5. Railway auto-deploys using `railway.toml`

## Disclaimer
For educational purposes only. Not financial advice.
