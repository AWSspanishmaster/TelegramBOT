import os
import logging
import json
import aiohttp
import asyncio
import nest_asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# Aplica nest_asyncio para entornos como Render
nest_asyncio.apply()

TOKEN = os.getenv("TOKEN")

user_addresses = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /add, /list, /remove, /positions, or /summary.")

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
    await update.message.reply_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data
    fills = await fetch_fills(address)
    if fills:
        await query.edit_message_text(f"📈 Recent fills for {address}:\n\n" + fills)
    else:
        await query.edit_message_text(f"⚠️ No recent fills or error for {address}.")

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
                    return []
                data = await resp.json()
                return data
    except Exception as e:
        logging.error(f"Exception fetching fills for {address}: {e}")
        return []

# NUEVO: Comando /summary
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    # Tiempo ahora en UTC
    now = datetime.now(timezone.utc)

    # Parsear argumento periodo
    arg = context.args[0].lower() if context.args else "24h"
    if arg == "1h":
        delta = timedelta(hours=1)
    elif arg == "8h":
        delta = timedelta(hours=8)
    elif arg == "24h":
        delta = timedelta(hours=24)
    else:
        await update.message.reply_text("⚠️ Invalid period. Use 1h, 8h or 24h.")
        return

    cutoff_timestamp = int((now - delta).timestamp() * 1000)  # en ms para comparar con fills

    # Diccionario para agrupar datos: coin -> {volume, usd_volume, wallets_set, long_volume, short_volume}
    summary_data = {}

    # Obtener fills de todas las wallets y agregarlos
    for address in addresses:
        fills = await fetch_fills(address)
        if not fills:
            continue

        for fill in fills:
            try:
                fill_time = fill.get("time", 0)
                if fill_time < cutoff_timestamp:
                    continue  # ignorar fills antiguos

                coin = fill.get("coin")
                if not coin:
                    continue

                size = fill.get("sz", 0)
                direction = fill.get("dir", "").lower()
                price = fill.get("px", 0)

                # Inicializar diccionario para moneda si no existe
                if coin not in summary_data:
                    summary_data[coin] = {
                        "volume": 0,
                        "usd_volume": 0,
                        "wallets": set(),
                        "long_volume": 0,
                        "short_volume": 0,
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
        await update.message.reply_text(f"⚠️ No fills found in the last {arg}.")
        return

    # Ordenar monedas por volumen descendente
    sorted_coins = sorted(summary_data.items(), key=lambda x: x[1]["volume"], reverse=True)[:10]

    lines = [f"Most traded coins in the last {arg} are:"]
    for i, (coin, data) in enumerate(sorted_coins, start=1):
        total_volume = data["volume"]
        usd_volume = data["usd_volume"]
        wallet_count = len(data["wallets"])
        long_vol = data["long_volume"]
        short_vol = data["short_volume"]

        long_pct = (long_vol / total_volume) * 100 if total_volume else 0
        short_pct = (short_vol / total_volume) * 100 if total_volume else 0

        lines.append(
            f"{i}. {coin} - {total_volume:.2f} ({usd_volume:.2f} USD) LONG {long_pct:.0f}% vs SHORT {short_pct:.0f}%"
            f" across {wallet_count} wallet(s)"
        )

    await update.message.reply_text("\n".join(lines))


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_addresses))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("summary", summary))

    app.run_polling()

if __name__ == "__main__":
    main()

