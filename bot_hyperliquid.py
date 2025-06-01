import logging
import json
import aiohttp
import os
from aiohttp import web
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
)
from aiohttp import web

#TOKEN
TOKEN = os.getenv("TOKEN")

user_data = {}
latest_fills = {} 

async def fetch_fills(address, timeframe_minutes):
    url = f"https://api.hyperliquid.xyz/info"
    body = {
        "type": "userFills",
        "user": address
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body) as resp:
            data = await resp.json()
            fills = data.get("userFills", {}).get("fills", [])
            now = datetime.utcnow()
            filtered = [
                fill for fill in fills
                if now - datetime.utcfromtimestamp(fill["time"] / 1000) <= timedelta(minutes=timeframe_minutes)
            ]
            return filtered

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("1h", callback_data="summary_60")],
        [InlineKeyboardButton("6h", callback_data="summary_360")],
        [InlineKeyboardButton("12h", callback_data="summary_720")],
        [InlineKeyboardButton("24h", callback_data="summary_1440")],
    ]
    await update.message.reply_text("Select a time range:", reply_markup=InlineKeyboardMarkup(keyboard))

async def summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    period = int(query.data.split("_")[1])
    addresses = user_data.get(chat_id, [])

    if not addresses:
        await query.edit_message_text("You haven’t added any addresses yet.")
        return

    msg_lines = [f"📊 <b>Summary ({period//60}h)</b>"]

    for addr in addresses:
        fills = await fetch_fills(addr["address"], period)
        if fills:
            total_volume = sum(abs(float(f["coin"]) * float(f["size"])) for f in fills)
            msg_lines.append(f"\n<b>{addr['name']}</b>\n💰 ${total_volume:,.2f}")
        else:
            msg_lines.append(f"\n<b>{addr['name']}</b>\nNo activity.")

    await query.edit_message_text("\n".join(msg_lines), parse_mode="HTML")

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    addresses = user_data.get(chat_id, [])

    if not addresses:
        await update.message.reply_text("You haven’t added any addresses yet.")
        return

    keyboard = [
        [InlineKeyboardButton(addr["name"], callback_data=f"positions_{addr['address']}")]
        for addr in addresses
    ]
    await update.message.reply_text("Select a wallet to view positions:", reply_markup=InlineKeyboardMarkup(keyboard))

async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    address = query.data.split("_")[1]

    url = f"https://api.hyperliquid.xyz/info"
    body = {
        "type": "userState",
        "user": address
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body) as resp:
            data = await resp.json()
            positions = data.get("userState", {}).get("assetPositions", [])

    if not positions:
        await query.edit_message_text("No open positions.")
        return

    lines = ["📈 <b>Open Positions</b>"]
    for p in positions:
        if p.get("position", {}).get("szi", 0) != 0:
            coin = p["position"]["coin"]
            size = float(p["position"]["szi"])
            side = "LONG" if size > 0 else "SHORT"
            lines.append(f"{coin}: <b>{side}</b> {abs(size)}")

    await query.edit_message_text("\n".join(lines), parse_mode="HTML")

async def monitor_wallets(app):
    while True:
        for chat_id, wallets in user_data.items():
            for wallet in wallets:
                address = wallet["address"]
                name = wallet["name"]
                fills = await fetch_fills(address, 10)  # Últimos 10 min

                for fill in fills:
                    key = f"{address}-{fill['time']}"
                    if key not in latest_fills:
                        latest_fills[key] = True  # Evitar repetir
                        coin = fill["coin"]
                        size = float(fill["size"])
                        side = "LONG" if fill["isTaker"] else "SHORT"
                        price = float(fill["px"])
                        total = size * price
                        dt = datetime.utcfromtimestamp(fill["time"] / 1000) + timedelta(hours=2)
                        dt_str = dt.strftime("%d/%m/%Y %H:%M")
                        msg = (
                            f"📡 <b>{name}</b>\n"
                            f"🟢 <b>Open {side}</b> {size} {coin} (${total:,.2f})\n"
                            f"🕒 {dt_str} UTC+2"
                        )
                        try:
                            await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                        except Exception as e:
                            logging.error(f"Error sending alert: {e}")
        await asyncio.sleep(20)

# Registrar handlers
app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
app.add_handler(CommandHandler("summary", summary_command))
app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_"))
app.add_handler(CommandHandler("positions", positions_command))
app.add_handler(CallbackQueryHandler(positions_callback, pattern="^positions_"))

# Inicia el monitor de fills al arrancar el bot
async def on_startup(app):
    app.create_task(monitor_wallets(app))

# Mantener el servidor activo en Render
async def handle(request):
    return web.Response(text="Bot is running")

async def start_bot():
    # Crear la app aiohttp y agregar ruta
    app_web = web.Application()
    app_web.add_routes([web.get("/", handle)])

    # Crear runner y site con puerto 10000
    app_runner = web.AppRunner(app_web)
    await app_runner.setup()
    site = web.TCPSite(app_runner, "0.0.0.0", 10000)
    await site.start()

    # Ejecutar polling del bot Telegram (suponiendo que 'app' es tu ApplicationBuilder)
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(start_bot())
