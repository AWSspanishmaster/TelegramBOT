import os
import logging
import aiohttp
import asyncio
import nest_asyncio
from datetime import datetime, timezone
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

nest_asyncio.apply()
TOKEN = os.getenv("TOKEN")

user_addresses = {}
user_states = {}

# Para control de fills ya notificados: {user_id: {address: set(fills_ids)}}
notified_fills = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Add", callback_data="menu_add")],
        [InlineKeyboardButton("📋 List", callback_data="menu_list")],
        [InlineKeyboardButton("📌 Positions", callback_data="menu_positions")],
        [InlineKeyboardButton("📊 Summary", callback_data="menu_summary")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Añadimos botón fijo para volver a /start en mensajes de menú y resumen
    fixed_button = InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")

    # Si es callback_query respondemos con menú, sino mensaje normal
    if update.message:
        await update.message.reply_text(
            "Welcome! Please choose an option:", reply_markup=reply_markup
        )
    elif update.callback_query:
        # Añadimos botón fijo abajo
        buttons_with_fixed = [*keyboard, [fixed_button]]
        reply_markup_fixed = InlineKeyboardMarkup(buttons_with_fixed)
        await update.callback_query.edit_message_text(
            "Welcome! Please choose an option:", reply_markup=reply_markup_fixed
        )


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
    elif query.data == "menu_start":
        # Maneja el botón fijo para volver al menú start
        await start(update, context)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"stage": "awaiting_address"}
    await update.message.reply_text("✍️ Write the address")

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

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    user_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        msg = "📭 No addresses added."
    else:
        lines = [f"{name}: {addr}" for addr, name in addresses.items()]
        msg = "📋 Your addresses:\n" + "\n".join(lines)

    # Añadimos botón fijo para volver al menú
    fixed_button = InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")

    if from_button:
        await update.callback_query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup([[fixed_button]])
        )
    else:
        await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if address in user_addresses.get(user_id, {}):
        del user_addresses[user_id][address]
        await update.message.reply_text(f"🗑️ Address removed: {address}")
    else:
        await update.message.reply_text("⚠️ Address not found.")

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
    # Botón fijo para volver al menú
    fixed_button = InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")
    keyboard.append([fixed_button])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if from_button:
        await update.callback_query.edit_message_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("📌 Select an address to view recent fills:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data
    fills = await fetch_fills(address)

    if fills:
        await query.edit_message_text(f"📈 Recent fills for {address}:")
        for f in fills[:5]:
            try:
                coin = f.get("coin", "?")
                size = f.get("sz", "?")
                price = f.get("px", "?")
                direction = f.get("dir", "?")
                timestamp = int(f.get("time", 0)) // 1000
                time_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                msg = f"📊 {coin} | {direction}\nSize: {size} @ ${price}\nTime: {time_str}"
                await query.message.reply_text(msg)
            except Exception as e:
                logging.error(f"Error formatting fill: {e}")
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
                    logging.error(f"Error getting fills for {address}: HTTP {resp.status}")
                    return None
                return await resp.json()
    except Exception as e:
        logging.error(f"Exception fetching fills for {address}: {e}")
        return None

# --- CAMBIO: summary con botón refresh y botón fijo ---

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    keyboard = [
        [InlineKeyboardButton("1h", callback_data="summary_1h"),
         InlineKeyboardButton("6h", callback_data="summary_6h")],
        [InlineKeyboardButton("12h", callback_data="summary_12h"),
         InlineKeyboardButton("24h", callback_data="summary_24h")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="summary_refresh")]
    ]

    # Botón fijo para volver al menú
    fixed_button = InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")
    keyboard.append([fixed_button])

    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_text = "📊 Summary - Choose period:"

    if from_button:
        await update.callback_query.edit_message_text(msg_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg_text, reply_markup=reply_markup)


async def summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Parse data
    data = query.data
    if data == "summary_refresh":
        # Recarga summary (usando mensaje actual)
        await summary(update, context, from_button=True)
        return

    # Simulación de resumen: aquí deberías añadir la lógica real
    period = data.split("_")[1] if "_" in data else "unknown"
    await query.edit_message_text(f"📊 Summary for last {period} hours\n\n[Datos reales aquí]")

    # Añadimos el botón Refresh y fijo para volver al menú
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="summary_refresh")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_reply_markup(reply_markup)

# --- Tarea asíncrona para notificaciones automáticas ---

async def notify_new_fills(application):
    while True:
        for user_id, addresses in user_addresses.items():
            for address in addresses.keys():
                fills = await fetch_fills(address)
                if not fills:
                    continue
                # Usa notified_fills para no enviar repetidos
                known_fills = notified_fills.setdefault(user_id, {}).setdefault(address, set())
                new_fills = []
                for fill in fills:
                    fill_id = fill.get("id")
                    if fill_id and fill_id not in known_fills:
                        new_fills.append(fill)
                        known_fills.add(fill_id)

                if new_fills:
                    for fill in new_fills:
                        coin = fill.get("coin", "?")
                        size = fill.get("sz", "?")
                        price = fill.get("px", "?")
                        direction = fill.get("dir", "?")
                        timestamp = int(fill.get("time", 0)) // 1000
                        time_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                        msg = f"⚡ New fill detected for {address}:\n📊 {coin} | {direction}\nSize: {size} @ ${price}\nTime: {time_str}"
                        try:
                            await application.bot.send_message(chat_id=user_id, text=msg)
                        except Exception as e:
                            logging.error(f"Failed to send notification to {user_id}: {e}")
        await asyncio.sleep(60)  # Esperar 60s antes de siguiente chequeo

# --- HTTP server para Render (sin cambios) ---

async def handle(request):
    return web.Response(text="Bot is running.")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_addresses))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("summary", summary))

    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_"))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^0x"))  # para fills por dirección

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Lanzar tarea asíncrona para notificaciones
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(notify_new_fills(app)), interval=60, first=10)

    # Start aiohttp server en background
    runner = web.AppRunner(web.Application())
    asyncio.get_event_loop().run_until_complete(runner.setup())
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    asyncio.get_event_loop().run_until_complete(site.start())

    logging.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()


