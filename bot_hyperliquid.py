import os
import logging
import json
import aiohttp
import asyncio
import nest_asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

# Aplica nest_asyncio para entornos como Render
nest_asyncio.apply()

# Token del bot desde variable de entorno
TOKEN = os.getenv("TOKEN")

# Diccionario para guardar direcciones por usuario
user_addresses = {}

# Configura el logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- COMANDOS DE TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /add, /list, /remove, /positions or /summary <1h|8h|24h>.")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text("⚠️ Invalid address format.")
        return
    user_addresses.setdefault(user_id, []).append(address)
    await update.message.reply_text(f"✅ Address added: {address}")

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
    else:
        await update.message.reply_text("📋 Your addresses:\n" + "\n".join(addresses))

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if address in user_addresses.get(user_id, []):
        user_addresses[user_id].remove(address)
        await update.message.reply_text(f"🗑️ Address removed: {address}")
    else:
        await update.message.reply_text("⚠️ Address not found.")

async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    keyboard = [[InlineKeyboardButton(address, callback_data=address)] for address in addresses]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📌 Select an address to view recent fills summary:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data

    fills = await fetch_fills(address)
    if not fills:
        await query.edit_message_text(f"⚠️ No recent fills or error for {address}.")
        return

    summary = {}
    for fill in fills:
        coin = fill.get("coin")
        direction = fill.get("dir", "").upper()
        size_raw = fill.get("sz", 0)
        price_raw = fill.get("px", 0)

        try:
            size = float(size_raw)
            price = float(price_raw)
        except Exception:
            size = 0.0
            price = 0.0

        if not coin or direction not in ["LONG", "SHORT"]:
            continue

        key = (direction, coin)
        if key not in summary:
            summary[key] = {"volume": 0.0, "usd_value": 0.0}

        summary[key]["volume"] += size
        summary[key]["usd_value"] += size * price

    if not summary:
        await query.edit_message_text(f"⚠️ No fills to summarize for {address}.")
        return

    sorted_summary = sorted(summary.items(), key=lambda x: x[1]["usd_value"], reverse=True)[:5]
    lines = [f"Top fills summary for {address}:"]
    for i, ((direction, coin), data) in enumerate(sorted_summary, 1):
        volume = data["volume"]
        usd_val = data["usd_value"]
        lines.append(f'{i}. {direction} {coin} - {volume:.2f} ($ {usd_val:,.2f})')

    message = "\n".join(lines)
    await query.edit_message_text(message)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    if context.args and context.args[0] in ["1h", "8h", "24h"]:
        hours = int(context.args[0].replace("h", ""))
    else:
        hours = 24

    since_timestamp = datetime.utcnow() - timedelta(hours=hours)
    since_ms = int(since_timestamp.timestamp() * 1000)

    coin_summary = {}

    for address in addresses:
        fills = await fetch_fills(address)
        if not fills:
            continue

        for fill in fills:
            fill_time = fill.get("time", 0)
            if fill_time < since_ms:
                continue

            coin = fill.get("coin")
            direction = fill.get("dir", "").upper()
            size_raw = fill.get("sz", 0)
            price_raw = fill.get("px", 0)

            try:
                size = float(size_raw)
                price = float(price_raw)
            except Exception:
                size = 0.0
                price = 0.0

            if not coin or direction not in ["LONG", "SHORT"]:
                continue

            if coin not in coin_summary:
                coin_summary[coin] = {
                    "total_volume": 0.0,
                    "total_usd": 0.0,
                    "long_volume": 0.0,
                    "short_volume": 0.0,
                    "wallets": set()
                }

            coin_summary[coin]["total_volume"] += size
            coin_summary[coin]["total_usd"] += size * price
            if direction == "LONG":
                coin_summary[coin]["long_volume"] += size
            else:
                coin_summary[coin]["short_volume"] += size
            coin_summary[coin]["wallets"].add(address)

    if not coin_summary:
        await update.message.reply_text(f"⚠️ No fills found in the last {hours} hours.")
        return

    sorted_coins = sorted(coin_summary.items(), key=lambda x: x[1]["total_usd"], reverse=True)[:10]
    lines = [f"Most traded coins in the last {hours}h:"]
    for i, (coin, data) in enumerate(sorted_coins, 1):
        total_vol = data["total_volume"]
        total_usd = data["total_usd"]
        long_pct = (data["long_volume"] / total_vol * 100) if total_vol > 0 else 0
        short_pct = (data["short_volume"] / total_vol * 100) if total_vol > 0 else 0
        wallets_count = len(data["wallets"])

        lines.append(
            f"{i}. {coin} - {total_vol:.2f} ($ {total_usd:,.2f}) LONG {long_pct:.0f}% vs SHORT {short_pct:.0f}% (Wallets: {wallets_count})"
        )

    await update.message.reply_text("\n".join(lines))

# --- API DE HYPERLIQUID ---

async def fetch_fills(address: str) -> list:
    url = "https://api.hyperliquid.xyz/info"
    headers = {"Content-Type": "application/json"}
    payload = {
        "type": "userFills",
        "user": address,
        "aggregateByTime": False
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    logging.error(f"Error fetching fills for {address}: HTTP {resp.status}")
                    return []
                data = await resp.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "fills" in data:
                    return data["fills"]
                else:
                    return []
    except Exception as e:
        logging.error(f"Exception fetching fills for {address}: {e}")
        return []

# --- SERVIDOR HTTP PARA RENDER ---

async def handle_root(request):
    return web.Response(text="Bot is running!")

async def run_web_server():
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.add_routes([web.get("/", handle_root)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Web server running on port {port}")

# --- INICIALIZACIÓN ---

async def init_bot_and_server():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("list", list_addresses))
    application.add_handler(CommandHandler("remove", remove))
    application.add_handler(CommandHandler("positions", positions))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("summary", summary))

    await asyncio.gather(
        application.run_polling(),
        run_web_server()
    )

# Entry point compatible con Render
if __name__ == "__main__":
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass

    loop = asyncio.get_event_loop()
    loop.create_task(init_bot_and_server())
    loop.run_forever()
