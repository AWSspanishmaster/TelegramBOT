# 📡 Hyperliquid Telegram Bot

This Telegram bot sends real-time notifications for trades (fills) made by specific users on the Hyperliquid platform. You can add or remove users to track directly via Telegram commands.

---

## 🚀 Features

- `/start`: Shows the list of available commands.
- `/add`: Add a new address (username) to track.
- `/remove`: See and delete currently tracked addresses.
- `/list`: Lists all addresses you are currently following.
- Real-time trade alerts using Hyperliquid's WebSocket API.

---

## 🛠 Requirements

- Python 3.10 or higher
- A [Telegram bot token](https://t.me/BotFather)
- A free account on [Render.com](https://render.com)

---

## 🧪 Local Installation (Optional)

```bash
git clone https://github.com/YOUR_USERNAME/TelegramBOT.git
cd TelegramBOT
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
