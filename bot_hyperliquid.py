import os
import logging
import json
import aiohttp
import asyncio
import nest_asyncio
from datetime import datetime
from collections import defaultdict, Counter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

# Aplica nest_asyncio para entornos como Render
nest_asyncio.apply()

# Token del bot (usa variable de entorno en Render)
TOKEN = os.getenv("TOKEN")

# Diccionario para guardar direcciones por usuario
user_addresses = {}

# Configura el logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /add, /list, /remove, /positions, or /summary.")

# Comando /add <address>
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text("⚠️ Invalid address format.")
        return
    user_addresses.setdefault(user_id, []).append(address)
    await update.message.reply_text(f"✅ Address added: {address}")

# Comando /list
async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
    else:
        await update.message.reply_text("📋 Your addresses:\n" + "\n".join(addresses))

# Comando /remove <address>
async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if address in user_addresses.get(user_id, []):
        user_addresses[user_id].remove(address)
        await update.message.reply_text(f"🗑️ Address removed: {address}")
    else:
        await update.message.reply_text("⚠️ Address not found.")

# Comando /positions
async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    keyboard = [[InlineKeyboardButton(address, callback_data=address)] for address in addresses]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)

# Maneja el botón con dirección seleccionada
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data
    fills = await fetch_fills(address)
    if fills:
        await query.edit_message_text(f"📈 Recent fills for {address}:\n\n" + fills)
    else:
        await query.edit_message_text(f"⚠️ No recent fills or error for {address}.")

# Función para obtener fills desde la API (formateados para mostrar)
async def fetch_fills(address: str) -> str:
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
                    return f"❌ Error getting fills: {resp.status}"
                data = await resp.json()
    except Exception as e:
        return f"❌ Exception: {e}"

    messages = []
    for fill in data[:10]:  # Limita a los 10 más recientes
        try:
            timestamp = datetime.utcfromtimestamp(fill["time"] / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
            direction = fill["dir"]
            size = fill["sz"]
            coin = fill["coin"]
            price = fill["px"]
            start_pos = fill.get("startPosition", "N/A")
            messages.append(
                f"🕒 {timestamp}\n"
                f"📈 {direction} {size} {coin} at an average price of ${price}\n"
                f"💰 ${start_pos}"
            )
        except Exception:
            continue

    return "\n\n".join(messages)

# Función para obtener fills raw sin formatear (para resumen)
async def fetch_fills_raw(address: str):
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
                    return None
                data = await resp.json()
                return data
    except Exception:
        return None

# Comando /summary - muestra resumen de monedas con mayor volumen y dirección mayoritaria
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    coin_volume = defaultdict(float)       # volumen total por moneda
    coin_addresses = defaultdict(set)      # direcciones distintas por moneda
    coin_directions = defaultdict(Counter) # contador de direcciones (buy/sell) por moneda

    for address in addresses:
        fills = await fetch_fills_raw(address)
        if not fills:
            continue
        for fill in fills:
            try:
                coin = fill["coin"]
                size = float(fill["sz"])
                direction = fill.get("dir", "unknown")  # buy, sell, etc.

                coin_volume[coin] += size
                coin_addresses[coin].add(address)
                coin_directions[coin][direction] += 1
            except Exception:
                continue

    if not coin_volume:
        await update.message.reply_text("⚠️ No fills data available for your addresses.")
        return

    sorted_coins = sorted(coin_volume.items(), key=lambda x: x[1], reverse=True)

    message_lines = ["📊 Summary of your tracked addresses:\n"]
    for coin, volume in sorted_coins:
        count_addresses = len(coin_addresses[coin])
        direction_counts = coin_directions[coin]
        most_common_dir, count_dir = direction_counts.most_common(1)[0] if direction_counts else ("unknown", 0)
        message_lines.append(
            f"• {coin}: {volume:.2f} volume from {count_addresses} addresses. Majority direction: {most_common_dir} ({count_dir} fills)"
        )

    await update.message.reply_text("\n".join(message_lines))

# Servidor HTTP básico para que Render no haga timeout
async def handle_root(request):
    return web.Response(text="Bot is running!")

async def run_web_server():
    app = web.Application()
    app.add_routes([web.get("/", handle_root)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# Main
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_addresses))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Ejecuta bot y servidor web en paralelo
    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())
    app.run_polling()

if __name__ == "__main__":
    main()
