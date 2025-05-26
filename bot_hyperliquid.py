Esta versión es la mejor por el momento, reemplaza todas las anteriores por esta:

import os
import logging
import aiohttp
import asyncio
import nest_asyncio
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from aiohttp import web

# Aplica nest_asyncio para entornos como Render
nest_asyncio.apply()

# Token del bot (usa variable de entorno en Render)
TOKEN = os.getenv("TOKEN")

# Diccionarios para guardar direcciones y estados
user_addresses = {}
user_states = {}

# Configura el logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Use /add, /list, /remove, /positions, or /summary."
    )

# Comando /add inicia flujo de dirección
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"stage": "awaiting_address"}
    await update.message.reply_text("✍️ Write the address")

# Manejo de mensajes para flujo de /add
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in user_states:
        return

    state = user_states[user_id]

    if state["stage"] == "awaiting_address":
        if not text.startswith("0x") or len(text) != 42:
            await update.message.reply_text("⚠️ Invalid address format.")
            return
        state["address"] = text
        state["stage"] = "awaiting_name"
        await update.message.reply_text("🏷️ Name it")

    elif state["stage"] == "awaiting_name":
        name = text
        address = state["address"]
        user_addresses.setdefault(user_id, {})
        if address in user_addresses[user_id]:
            await update.message.reply_text("⚠️ Address already added.")
        else:
            user_addresses[user_id][address] = name
            await update.message.reply_text("✅ Done!")
        del user_states[user_id]

# Comando /list
async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
    else:
        lines = [f"{name}: {addr}" for addr, name in addresses.items()]
        await update.message.reply_text("📋 Your addresses:\n" + "\n".join(lines))

# Comando /remove <address>
async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if address in user_addresses.get(user_id, {}):
        del user_addresses[user_id][address]
        await update.message.reply_text(f"🗑️ Address removed: {address}")
    else:
        await update.message.reply_text("⚠️ Address not found.")

# Comando /positions
async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    keyboard = [[InlineKeyboardButton(f"{name}", callback_data=addr)] for addr, name in addresses.items()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)

# Maneja el botón con dirección seleccionada
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data
    fills = await fetch_fills(address)
    if fills:
        await query.edit_message_text(f"📈 Recent fills for {address}:")
        for f in fills[:5]:
            await query.message.reply_text(str(f))
    else:
        await query.edit_message_text(f"⚠️ No recent fills or error for {address}.")

# Función para obtener fills desde la API
async def fetch_fills(address: str):
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
                    logging.error(f"Error getting fills for {address}: HTTP {resp.status}")
                    return None
                return await resp.json()
    except Exception as e:
        logging.error(f"Exception fetching fills for {address}: {e}")
        return None

# Comando /summary muestra botones
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("1h", callback_data="summary_1h"),
         InlineKeyboardButton("6h", callback_data="summary_6h")],
        [InlineKeyboardButton("12h", callback_data="summary_12h"),
         InlineKeyboardButton("24h", callback_data="summary_24h")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⏱️ Select timeframe:", reply_markup=reply_markup)

# Maneja botones de resumen
async def summary_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    timeframe = query.data.split("_")[1]
    user_id = query.from_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await query.edit_message_text("📭 No addresses added.")
        return

    valid_times = {"1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400}
    timeframe_seconds = valid_times.get(timeframe)
    now_ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    cutoff_ts = now_ts - timeframe_seconds * 1000

    summary_data = {}
    await query.edit_message_text(f"⏳ Fetching fills for last {timeframe}... Please wait.")

    for address in addresses:
        fills = await fetch_fills(address)
        if not fills:
            continue
        for fill in fills:
            try:
                fill_time = fill.get("time", 0)
                if fill_time < cutoff_ts:
                    continue
                coin = fill.get("coin")
                size = float(fill.get("sz", 0))
                price = float(fill.get("px", 0))
                direction = fill.get("dir", "").lower()

                if coin not in summary_data:
                    summary_data[coin] = {
                        "volume": 0.0,
                        "usd_volume": 0.0,
                        "wallets": set(),
                        "long_volume": 0.0,
                        "short_volume": 0.0,
                    }

                summary_data[coin]["volume"] += size
                summary_data[coin]["usd_volume"] += size * price
                summary_data[coin]["wallets"].add(address)
                if direction == "long":
                    summary_data[coin]["long_volume"] += size
                elif direction == "short":
                    summary_data[coin]["short_volume"] += size

            except Exception as e:
                logging.error(f"Error procesando fill: {e}")

    if not summary_data:
        await query.message.reply_text("⚠️ No fills found in the given timeframe.")
        return

    sorted_coins = sorted(summary_data.items(), key=lambda x: x[1]["volume"], reverse=True)[:10]

    lines = [f"Most traded coins in the last {timeframe}:"]
    for i, (coin, data) in enumerate(sorted_coins, 1):
        vol = data["volume"]
        usd = data["usd_volume"]
        wallets = len(data["wallets"])
        long_pct = round(100 * data["long_volume"] / vol) if vol else 0
        short_pct = 100 - long_pct

       lines.append(f"{i}.- {vol:,.2f} {coin} (${usd:,.2f})\nLong {long_pct}% vs Short {short_pct}% (Wallets: {wallets})\n")


    await query.message.reply_text("\n".join(lines))

# Servidor HTTP básico para evitar timeout en Render
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
    app.add_handler(CallbackQueryHandler(summary_button_handler, pattern=r"^summary_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())
    app.run_polling()

if __name__ == "__main__":
    main()
