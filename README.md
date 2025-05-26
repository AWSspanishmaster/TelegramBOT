# Telegram Bot for Monitoring Hyperliquid Wallets

This Telegram bot allows users to track Ethereum addresses and monitor their trading activity (fills) and open positions on the Hyperliquid platform.

## 🚀 Features

- Add and manage tracked Ethereum addresses.
- View open positions for any tracked wallet.
- Get a summary of the top trades per wallet over specific time periods (1h, 6h, 12h, 24h).
- Fully interactive with Telegram inline buttons.
- Deployable on platforms like **Render** with built-in HTTP keep-alive server.

## 📦 Commands

### `/start`
Sends a welcome message and basic instructions.

### `/add`
Guides the user step-by-step to:
1. Enter an Ethereum address.
2. Assign a custom name for easier identification.
3. Confirm successful addition with a ✅.

### `/add_bulk`
Adds multiple addresses at once. The format should be:
```
0x1234abcd... Name1  
0x5678efgh... Name2
```

### `/list`
Shows all addresses the user is currently tracking.

### `/remove <address>`
Removes the specified address from the user’s list.

### `/positions`
Displays a menu of tracked addresses. When the user selects one, the bot returns its current open positions on Hyperliquid.

### `/summary`
Sends four inline buttons to choose a time range (1h, 6h, 12h, 24h). After selection, the bot fetches all trades from tracked wallets within that period and displays the ones with the highest volume per wallet.

---

## 🌐 How It Works

- Uses the [Hyperliquid public API](https://hyperliquid.xyz) to fetch fills and positions.
- Each user manages their own list of addresses (per Telegram user ID).
- The bot uses `python-telegram-bot` for interaction and `aiohttp` to run a background HTTP server (used for keep-alive on Render).

---

## ⚙️ Technical Details

- Python 3.13+
- `python-telegram-bot >= 20`
- `aiohttp` for HTTP server
- `nest_asyncio` for compatibility with `asyncio.run` inside some platforms
- Render deployment ready with dynamic port binding

### HTTP Server on Render

Render requires the app to listen on an HTTP port to stay alive (unless you use a paid **Background Worker**). This bot runs a lightweight HTTP server on `/` to avoid idle shutdown.

```python
# Example
from aiohttp import web

async def handler(request):
    return web.Response(text="Bot is running")

app = web.Application()
app.add_routes([web.get('/', handler)])
web.run_app(app, port=int(os.environ.get("PORT", 8080)))
```

---

## 📄 Environment Variables

| Variable | Description |
|----------|-------------|
| `TOKEN`  | Telegram bot token from BotFather |

---

## 🛠️ Running Locally

1. Clone this repository:
```bash
git clone https://github.com/yourusername/hyperliquid-telegram-bot.git
cd hyperliquid-telegram-bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set your environment variable:
```bash
export TOKEN=your_telegram_bot_token
```

4. Run the bot:
```bash
python main.py
```

---

## 📤 Deploying on Render

- Choose **Web Service**, not **Background Worker**.
- Set environment variable `TOKEN` in the dashboard.
- Make sure `main.py` runs both the bot and the HTTP server.

---

## ✅ VERSION OK

This README matches the latest stable bot version:
- Guided `/add` flow
- `/summary` with period buttons
- Persistent HTTP server for Render
- All commands functional and verified

---
