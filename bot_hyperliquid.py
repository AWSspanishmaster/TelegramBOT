import os
import logging
import aiohttp
import asyncio
import nest_asyncio
from datetime import datetime, timezone
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
    await update.message.reply_text(
        "Welcome! Use /add, /list, /remove, /positions, or /summary <1h|8h|24h>."
    )

# Comando /add <address>
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text("⚠️ Invalid address format.")
        return
    user_addresses.setdefault(user_id, [])
    if address in user_addresses[user_id]:
        await update.message.reply_text("⚠️ Address already added.")
        return
    user_addresses[user_id].append(address)
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
                data = await resp.json()
                return data
    except Exception as e:
        logging.error(f"Exception fetching fills for {address}: {e}")
        return None

# Comando /summary <timeframe>
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    # Tiempo configurado
    valid_times = {"1h": 3600, "8h": 28800, "24h": 86400}
    arg = context.args[0].lower() if context.args else "24h"
    if arg not in valid_times:
        await update.message.reply_text("⚠️ Use /summary <1h|8h|24h>")
        return

    timeframe_seconds = valid_times[arg]
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    cutoff_ts = now_ts - timeframe_seconds * 1000  # API usa ms

    summary_data = {}

    await update.message.reply_text(f"⏳ Fetching fills for last {arg}... Please wait.")

    # Obtén fills para todas las direcciones y procesa
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
                if not coin:
                    continue

                size_raw = fill.get("sz", 0)
                price_raw = fill.get("px", 0)
                direction = fill.get("dir", "").lower()

                try:
                    size = float(size_raw)
                    price = float(price_raw)
                except Exception:
                    size = 0.0
                    price = 0.0

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
                continue

    if not summary_data:
        await update.message.reply_text("⚠️ No fills found in the given timeframe.")
        return

    # Ordenar monedas por volumen descendente y limitar a 10
    sorted_coins = sorted(summary_data.items(), key=lambda x: x[1]["volume"], reverse=True)[:10]

    # Construir mensaje
    lines = ["Most traded coins in the last " + arg + ":"]
    for i, (coin, data) in enumerate(sorted_coins, 1):
        total_vol = data["volume"]
        usd_vol = data["usd_volume"]
        wallets_count = len(data["wallets"])
        long_vol = data["long_volume"]
        short_vol = data["short_volume"]

        if total_vol == 0:
            long_pct = short_pct = 0
        else:
            long_pct = round(100 * long_vol / total_vol)
            short_pct = 100 - long_pct

        lines.append(
            f"{i}. {coin} - {total_vol:.2f} ({usd_vol:,.2f} USD) LONG {long_pct}% vs SHORT {short_pct}% (Wallets: {wallets_count})"
        )

    message = "\n".join(lines)
    await update.message.reply_text(message)

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

    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())
    app.run_polling()

if __name__ == "__main__":
    main()


