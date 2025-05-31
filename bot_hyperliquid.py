import os
import logging
import aiohttp
import asyncio
import nest_asyncio
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
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

# Comando /start con menú inline
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Add", callback_data="menu_add")],
        [InlineKeyboardButton("📋 List", callback_data="menu_list")],
        [InlineKeyboardButton("📌 Positions", callback_data="menu_positions")],
        [InlineKeyboardButton("📊 Summary", callback_data="menu_summary")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            "Welcome! Please choose an option:", reply_markup=reply_markup
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "Welcome! Please choose an option:", reply_markup=reply_markup
        )

# ALERTAS
previous_fills = {}  # user_id -> address -> last_fill_time

async def monitor_fills(application: Application):
    while True:
        for user_id, addr_map in user_addresses.items():
            for address, name in addr_map.items():
                fills = await fetch_fills(address)
                if not fills:
                    continue

                latest_fill = fills[0]
                last_seen_time = previous_fills.get(user_id, {}).get(address, 0)

                if latest_fill["time"] > last_seen_time:
                    previous_fills.setdefault(user_id, {})[address] = latest_fill["time"]

                    # Extraer info
                    coin = latest_fill["coin"]
                    sz = float(latest_fill["sz"])
                    px = float(latest_fill["px"])
                    side = latest_fill["dir"]  # 'L' o 'S'
                    is_close = latest_fill.get("closed", False)  # depende de la API
                    lev = latest_fill.get("leverage", "x")  # si no viene, puedes omitir

                    side_txt = "LONG" if side == "L" else "SHORT"
                    action = "Close" if is_close else "Open"
                    usd = sz * px
                    madrid_time = datetime.now(tz=timezone.utc).astimezone().astimezone().strftime("%Y-%m-%d %H:%M")

                    text = (
                        f"💼 {name}\n"
                        f"🔔 {action} {side_txt} {lev}x\n"
                        f"💰 {sz:,.2f} {coin} (${usd:,.2f})\n"
                        f"🕒 {madrid_time} (Madrid)"
                    )

                    await application.bot.send_message(chat_id=user_id, text=text)

        await asyncio.sleep(30)  # chequeo cada 30 segundos

# Botón "Add" desde menú
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu_add":
        user_id = query.from_user.id
        user_states[user_id] = {"stage": "awaiting_address"}
        await query.edit_message_text("✍️ Write the address")
    elif query.data == "menu_list":
        await list_addresses(update, context, from_button=True)
    elif query.data == "menu_positions":
        await positions(update, context, from_button=True)
    elif query.data == "menu_summary":
        await summary(update, context, from_button=True)

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
async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    user_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        msg = "📭 No addresses added."
    else:
        lines = [f"{name}: {addr}" for addr, name in addresses.items()]
        msg = "📋 Your addresses:\n" + "\n".join(lines)

    if from_button:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)

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
async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    user_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        msg = "📭 No addresses added."
        if from_button:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    keyboard = [[InlineKeyboardButton(f"{name}", callback_data=addr)] for addr, name in addresses.items()]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if from_button:
        await update.callback_query.edit_message_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)


# Maneja el botón con dirección seleccionada
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data
    fills = await fetch_fills(address)

    if not fills:
        await query.edit_message_text(f"⚠️ No recent fills or error for {address}.")
        return

    # Agrupar por activo y dirección
    coin_summary = defaultdict(lambda: {"long": 0.0, "short": 0.0, "usd": 0.0})

    for f in fills:
        try:
            coin = f.get("coin")
            sz = float(f.get("sz", 0))
            px = float(f.get("px", 0))
            dir = f.get("dir")
            if not coin or not dir:
                continue

            usd = sz * px
            if dir == "L":
                coin_summary[coin]["long"] += sz
            elif dir == "S":
                coin_summary[coin]["short"] += sz

            coin_summary[coin]["usd"] += usd
        except Exception as e:
            logging.error(f"Error parsing fill for {address}: {e}")

    if not coin_summary:
        await query.edit_message_text(f"⚠️ No valid fills for {address}.")
        return

    lines = []
    for coin, data in coin_summary.items():
        long_vol = data["long"]
        short_vol = data["short"]
        usd_total = data["usd"]

        lines.append(
            f"🪙 {coin}\n"
            f"🟢 LONG: {long_vol:.2f}\n"
            f"🔴 SHORT: {short_vol:.2f}\n"
            f"💰 USD total: ${usd_total:,.2f}\n"
        )

    text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode="Markdown")



# Función que obtiene el resumen según periodo (en horas)
async def generate_summary(addresses, period_hours):
    now_ts = int(datetime.utcnow().timestamp())
    start_ts = now_ts - period_hours * 3600

    summary_data = defaultdict(lambda: {
        "long_volume": 0.0,
        "short_volume": 0.0,
        "long_usd": 0.0,
        "short_usd": 0.0,
    })

    any_data = False

    for addr in addresses.keys():
        fills = await fetch_fills(addr)
        if not fills:
            continue

        for f in fills:
            try:
                fill_ts = int(f.get("time", 0)) // 1000
                if fill_ts < start_ts:
                    continue

                coin = f.get("coin", "?")
                size = float(f.get("sz", 0))
                price = float(f.get("px", 0))
                direction = f.get("dir", "").lower()

                usd = size * price

                if direction == "long" or direction == "l":
                    summary_data[coin]["long_volume"] += size
                    summary_data[coin]["long_usd"] += usd
                elif direction == "short" or direction == "s":
                    summary_data[coin]["short_volume"] += size
                    summary_data[coin]["short_usd"] += usd

                any_data = True
            except Exception as e:
                logging.error(f"Error processing fill in summary: {e}")

    if not any_data:
        return "⚠️ No operations in timeframe."

    lines = []
    for coin, data in summary_data.items():
        long_vol = data["long_volume"]
        short_vol = data["short_volume"]
        long_usd = data["long_usd"]
        short_usd = data["short_usd"]
        total_usd = long_usd + short_usd

        lines.append(
            f"🔹 {coin}\n"
            f"🟢 LONG: {long_vol:.2f} (USD: ${long_usd:,.2f})\n"
            f"🔴 SHORT: {short_vol:.2f} (USD: ${short_usd:,.2f})\n"
            f"💰 USD total: ${total_usd:,.2f}\n"
        )

    return "\n".join(lines)


# Handler /summary - muestra botones para seleccionar periodo
async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await update.message.reply_text("📭 No addresses added.")
        return

    keyboard = [
        [
            InlineKeyboardButton("1h", callback_data="summary_1h"),
            InlineKeyboardButton("6h", callback_data="summary_6h"),
            InlineKeyboardButton("12h", callback_data="summary_12h"),
            InlineKeyboardButton("24h", callback_data="summary_24h"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📊 Select timeframe for summary:", reply_markup=reply_markup)


# Callback para botones de resumen y refresco
async def summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    addresses = user_addresses.get(user_id, {})

    if not addresses:
        await query.edit_message_text("📭 No addresses added.")
        return

    data = query.data

    if data.startswith("summary_"):
        try:
            period_hours = int(data.split("_")[1].replace("h", ""))
        except:
            period_hours = 24

        text = await generate_summary(addresses, period_hours)
        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=data),
                InlineKeyboardButton("⬅️ Back", callback_data="summary_back"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    elif data == "summary_back":
        keyboard = [
            [
                InlineKeyboardButton("1h", callback_data="summary_1h"),
                InlineKeyboardButton("6h", callback_data="summary_6h"),
                InlineKeyboardButton("12h", callback_data="summary_12h"),
                InlineKeyboardButton("24h", callback_data="summary_24h"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📊 Select timeframe for summary:", reply_markup=reply_markup)


# Servidor HTTP básico para evitar timeout en Render
async def handle_root(request):
    return web.Response(text="✅ Bot is alive")

# Función principal
async def main():
    app = Application.builder().token(TOKEN).build()

    # Handlers del bot
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_addresses))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("summary", summary_command))  # ✅ Comando /summary

    # CallbackQueryHandlers: orden IMPORTANTE
    app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_"))      # ✅ Summary period buttons
    app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_back$")) # ✅ Back button
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))             # ✅ Menú general
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^0x"))              # ✅ Direcciones 0x
    app.add_handler(CallbackQueryHandler(button_handler))                             # ✅ Otros botones sin patrón

    # Mensajes normales
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))



    # Inicia bot manualmente
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Servidor aiohttp para mantener Render vivo
    runner = web.AppRunner(web.Application())
    app.web_app = runner.app
    app.web_app.add_routes([web.get("/", handle_root)])
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("✅ Bot is running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
