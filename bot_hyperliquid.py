import logging
import aiohttp
import os
from aiohttp import web
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import asyncio

# -----------------------
# Configuración inicial
# -----------------------

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("Debes definir la variable de entorno TOKEN con tu token de Telegram")

logging.basicConfig(level=logging.INFO)

# user_data: { chat_id: [ {"address": "...", "name": "..."} , ... ] }
user_data = {}

# user_states para los flujos de /add, /remove, /edit:
# { chat_id: {"stage": "...", "address": "..."} }
user_states = {}

# latest_fills para evitar alertas duplicadas: { "address-time": True }
latest_fills = {}

# -----------------------
# Funciones auxiliares
# -----------------------

async def fetch_fills(address: str, timeframe_minutes: int):
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "userFills", "user": address}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logging.error(f"fetch_fills: HTTP {resp.status} para dirección {address}")
                    return []
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    text = await resp.text()
                    logging.error(f"fetch_fills: respuesta no JSON ({content_type}): {text}")
                    return []

                data = await resp.json()
                if isinstance(data, list):
                    fills = data
                else:
                    fills = data.get("userFills", {}).get("fills", [])

                now = datetime.utcnow()
                resultado = [
                    fill
                    for fill in fills
                    if now - datetime.utcfromtimestamp(fill.get("time", 0) / 1000)
                    <= timedelta(minutes=timeframe_minutes)
                ]
                return resultado
        except Exception as e:
            logging.error(f"fetch_fills: excepción al llamar a la API: {e}")
            return []

# -----------------------
# Handlers de Telegram
# -----------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Add", callback_data="menu_add")],
        [InlineKeyboardButton("✏️ Edit", callback_data="menu_edit")],
        [InlineKeyboardButton("🗑️ Remove", callback_data="menu_remove")],
        [InlineKeyboardButton("📋 List", callback_data="menu_list")],
        [InlineKeyboardButton("📌 Positions", callback_data="menu_positions")],
        [InlineKeyboardButton("📊 Summary", callback_data="menu_summary")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("Welcome! Please choose an option:", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text("Welcome! Please choose an option:", reply_markup=reply_markup)

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id

    if data == "menu_add":
        user_states[chat_id] = {"stage": "awaiting_address_add"}
        await query.edit_message_text("✍️ Please send the address (0x...):")
    elif data == "menu_edit":
        user_states[chat_id] = {"stage": "awaiting_address_edit"}
        await query.edit_message_text("✍️ Please send the address you want to edit (0x...):")
    elif data == "menu_remove":
        user_states[chat_id] = {"stage": "awaiting_address_remove"}
        await query.edit_message_text("✍️ Please send the address you want to remove (0x...):")
    elif data == "menu_list":
        await list_command(update, context, from_button=True)
    elif data == "menu_positions":
        await positions_command(update, context, from_button=True)
    elif data == "menu_summary":
        await summary_command(update, context, from_button=True)

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    user_states[chat_id] = {"stage": "awaiting_address_add"}
    await update.message.reply_text("✍️ Please send the address (0x...):")

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    if context.args:
        address = context.args[0]
        addresses = user_data.get(chat_id, [])
        new_list = [w for w in addresses if w["address"] != address]
        if len(new_list) < len(addresses):
            user_data[chat_id] = new_list
            await update.message.reply_text(f"🗑️ Address removed: {address}")
        else:
            await update.message.reply_text("⚠️ Address not found.")
    else:
        user_states[chat_id] = {"stage": "awaiting_address_remove"}
        await update.message.reply_text("✍️ Please send the address you want to remove (0x...):")

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    if len(context.args) >= 2:
        address = context.args[0]
        new_name = " ".join(context.args[1:])
        addresses = user_data.get(chat_id, [])
        found = False
        for w in addresses:
            if w["address"] == address:
                w["name"] = new_name
                found = True
                break
        if found:
            await update.message.reply_text(f"✏️ Wallet {address} renamed to '{new_name}'.")
        else:
            await update.message.reply_text("⚠️ Address not found.")
    else:
        user_states[chat_id] = {"stage": "awaiting_address_edit"}
        await update.message.reply_text("✍️ Please send the address you want to edit (0x...):")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    text = update.message.text.strip()

    if chat_id not in user_states:
        return

    state = user_states[chat_id]
    stage = state["stage"]

    if stage == "awaiting_address_add":
        if not (text.startswith("0x") and len(text) == 42):
            await update.message.reply_text("⚠️ Invalid address format (must start with 0x and be 42 chars).")
            return
        state["address"] = text
        state["stage"] = "awaiting_name_add"
        await update.message.reply_text("🏷️ Now send a name for this wallet:")
        return
    if stage == "awaiting_name_add":
        name = text
        address = state["address"]
        user_data.setdefault(chat_id, [])
        exists = any(w["address"] == address for w in user_data[chat_id])
        if exists:
            await update.message.reply_text("⚠️ Address already added.")
        else:
            user_data[chat_id].append({"address": address, "name": name})
            await update.message.reply_text("✅ Address added!")
        user_states.pop(chat_id, None)
        return

    if stage == "awaiting_address_remove":
        address = text
        if not (address.startswith("0x") and len(address) == 42):
            await update.message.reply_text("⚠️ Invalid address format.")
            return
        addresses = user_data.get(chat_id, [])
        new_list = [w for w in addresses if w["address"] != address]
        if len(new_list) < len(addresses):
            user_data[chat_id] = new_list
            await update.message.reply_text(f"🗑️ Address removed: {address}")
        else:
            await update.message.reply_text("⚠️ Address not found.")
        user_states.pop(chat_id, None)
        return

    if stage == "awaiting_address_edit":
        address = text
        if not (address.startswith("0x") and len(address) == 42):
            await update.message.reply_text("⚠️ Invalid address format.")
            return
        state["address"] = address
        state["stage"] = "awaiting_name_edit"
        await update.message.reply_text("🏷️ Send the new name for this wallet:")
        return
    if stage == "awaiting_name_edit":
        new_name = text
        address = state["address"]
        addresses = user_data.get(chat_id, [])
        found = False
        for w in addresses:
            if w["address"] == address:
                w["name"] = new_name
                found = True
                break
        if found:
            await update.message.reply_text(f"✏️ Wallet {address} renamed to '{new_name}'.")
        else:
            await update.message.reply_text("⚠️ Address not found.")
        user_states.pop(chat_id, None)
        return

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    chat_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    wallets = user_data.get(chat_id, [])
    if not wallets:
        text = "📋 Your wallet list is empty."
    else:
        text = "📋 Your wallets:\n\n"
        for w in wallets:
            text += f"• {w['name']} — `{w['address']}`\n"
    if from_button:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    chat_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    wallets = user_data.get(chat_id, [])
    if not wallets:
        text = "📌 Your wallet list is empty."
        if from_button:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    # Construir listado con botones para seleccionar wallet
    keyboard = []
    for w in wallets:
        keyboard.append([InlineKeyboardButton(w["name"], callback_data=f"pos_{w['address']}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if from_button:
        await update.callback_query.edit_message_text("Select a wallet to view positions:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Select a wallet to view positions:", reply_markup=reply_markup)

async def position_detail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    data = query.data
    if not data.startswith("pos_"):
        return
    address = data[4:]

    # Aquí haríamos la consulta real a la API para obtener posiciones abiertas de la wallet
    # Como ejemplo, pondremos texto ficticio:
    # Idealmente, crear función async fetch_positions(address) para obtener datos reales

    # Simulación respuesta:
    text = (
        f"📌 Open positions for wallet:\n\n"
        f"`{address}`\n\n"
        f"(Aquí irían las posiciones abiertas reales obtenidas de la API)"
    )

    await query.edit_message_text(text, parse_mode="Markdown")

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    chat_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    wallets = user_data.get(chat_id, [])
    if not wallets:
        text = "📊 Your wallet list is empty."
        if from_button:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    keyboard = [
        [
            InlineKeyboardButton("1h", callback_data="summary_60"),
            InlineKeyboardButton("6h", callback_data="summary_360"),
            InlineKeyboardButton("12h", callback_data="summary_720"),
            InlineKeyboardButton("24h", callback_data="summary_1440"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if from_button:
        await update.callback_query.edit_message_text("Select timeframe for summary:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Select timeframe for summary:", reply_markup=reply_markup)

async def summary_timeframe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    data = query.data
    if not data.startswith("summary_"):
        return
    minutes = int(data.split("_")[1])
    wallets = user_data.get(chat_id, [])
    if not wallets:
        await query.edit_message_text("📊 Your wallet list is empty.")
        return

    # Obtener fills de todas las wallets en el timeframe y ordenar por volumen
    all_fills = []
    for w in wallets:
        fills = await fetch_fills(w["address"], minutes)
        for fill in fills:
            all_fills.append({"wallet": w["name"], "address": w["address"], "fill": fill})

    if not all_fills:
        await query.edit_message_text("No fills found in the selected timeframe.")
        return

    # Ordenar por volumen (ejemplo: cantidad absoluta del fill)
    all_fills.sort(key=lambda x: abs(x["fill"].get("size", 0)), reverse=True)

    # Mostrar top 5 fills
    lines = []
    for item in all_fills[:5]:
        fill = item["fill"]
        side = fill.get("side", "N/A")
        size = fill.get("size", 0)
        price = fill.get("price", 0)
        time_ts = fill.get("time", 0) / 1000
        time_str = datetime.utcfromtimestamp(time_ts).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(
            f"Wallet: {item['wallet']}\n"
            f"Side: {side}\n"
            f"Size: {size}\n"
            f"Price: {price}\n"
            f"Time: {time_str}\n"
            f"---"
        )

    text = "📊 Top fills:\n\n" + "\n".join(lines)
    await query.edit_message_text(text)

# -----------------------
# Ruta web para keep-alive
# -----------------------

async def web_handler(request):
    return web.Response(text="Bot is running")

# -----------------------
# Main
# -----------------------

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # Handlers comandos y mensajes
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("edit", edit_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("positions", positions_command))
    application.add_handler(CommandHandler("summary", summary_command))

    application.add_handler(CallbackQueryHandler(menu_handler, pattern=r"^menu_"))
    application.add_handler(CallbackQueryHandler(position_detail_handler, pattern=r"^pos_"))
    application.add_handler(CallbackQueryHandler(summary_timeframe_handler, pattern=r"^summary_"))

    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    # Correr bot en modo polling y también el servidor web con aiohttp para keep-alive
    runner = web.AppRunner(web.Application())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

    # Añadir ruta para web
    app = runner.app
    app.router.add_get("/", web_handler)

    logging.info("Bot is running...")

    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
