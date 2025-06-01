import logging
import aiohttp
import os
from aiohttp import web
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    MenuButtonCommands
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    Application
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
    """
    Llama al endpoint userFills de Hyperliquid y filtra operaciones
    realizadas en los últimos timeframe_minutes.
    Se adapta si la respuesta viene como lista o como dict.
    """
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
    """
    Comando /start: muestra menú con opciones.
    """
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
    """
    Maneja los botones del menú (/start).
    """
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
    """
    Comando /add: inicia flujo para añadir dirección.
    """
    chat_id = update.effective_user.id
    user_states[chat_id] = {"stage": "awaiting_address_add"}
    await update.message.reply_text("✍️ Please send the address (0x...):")

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /remove <address> o flujo para eliminar dirección desde menú.
    """
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
    """
    Comando /edit <address> <new_name> o flujo para renombrar wallet.
    """
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
    """
    Maneja los mensajes de texto para los flujos de /add, /remove, /edit.
    """
    chat_id = update.effective_user.id
    text = update.message.text.strip()

    if chat_id not in user_states:
        return

    state = user_states[chat_id]
    stage = state["stage"]

    # Flujo /add
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

    # Flujo /remove desde menú
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

    # Flujo /edit desde menú
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
    """
    Comando /list: muestra direcciones guardadas.
    """
    chat_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    addresses = user_data.get(chat_id, [])
    if not addresses:
        msg = "📭 No addresses added."
    else:
        lines = [f"{w['name']}: {w['address']}" for w in addresses]
        msg = "📋 Your addresses:\n" + "\n".join(lines)

    if from_button:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    """
    Comando /positions: muestra botones con cada wallet para ver posiciones abiertas.
    """
    chat_id = update.effective_user.id if not from_button else update.callback_query.from_user.id
    addresses = user_data.get(chat_id, [])
    if not addresses:
        msg = "📭 No addresses added."
        if from_button:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    keyboard = [
        [InlineKeyboardButton(w["name"], callback_data=f"positions_{w['address']}")]
        for w in addresses
    ]
    if from_button:
        await update.callback_query.edit_message_text("📌 Select a wallet:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("📌 Select a wallet:", reply_markup=InlineKeyboardMarkup(keyboard))

async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback de botones de /positions: usa clearinghouseState para mostrar posiciones.
    Incluye botón de refresh.
    """
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    address = query.data.split("_", 1)[1]

    logging.info(f"positions_callback triggered for chat_id={chat_id}, address={address}")

    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "clearinghouseState", "user": address}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    await query.message.reply_text(f"Error {resp.status} retrieving positions.")
                    return
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    text = await resp.text()
                    logging.error(f"positions_callback: respuesta no JSON ({content_type}): {text}")
                    await query.message.reply_text("Error retrieving positions (invalid response).")
                    return
                data = await resp.json()
        except Exception as e:
            logging.error(f"positions_callback: excepción al llamar a la API: {e}")
            await query.message.reply_text("Error retrieving positions (exception).")
            return

    positions = data.get("assetPositions", [])
    if not positions:
        await query.message.reply_text("No open positions.")
        return

    lines = ["📈 <b>Open Positions</b>"]
    for p in positions:
        pos = p.get("position", {})
        coin = pos.get("coin")
        size = float(pos.get("szi", 0))
        entry_px = float(pos.get("entryPx", 0))
        side_txt = "LONG" if size > 0 else "SHORT"
        usd_value = abs(size) * entry_px
        status_symbol = "🟢"
        lines.append(f"{status_symbol} Open {side_txt}")
        lines.append(f"{abs(size)} {coin} (${usd_value:,.2f})")

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"positions_{address}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_positions")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=reply_markup)

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_button=False):
    """
    Comando /summary: muestra botones para seleccionar rango de tiempo.
    """
    keyboard = [
        [InlineKeyboardButton("1h", callback_data="summary_60")],
        [InlineKeyboardButton("6h", callback_data="summary_360")],
        [InlineKeyboardButton("12h", callback_data="summary_720")],
        [InlineKeyboardButton("24h", callback_data="summary_1440")],
    ]
    if from_button:
        await update.callback_query.edit_message_text(
            "Select a time range:", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "Select a time range:", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback de botones de /summary: muestra resumen de cada wallet en ese periodo.
    Incluye botón de refresh.
    """
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    period = int(query.data.split("_")[1])

    logging.info(f"summary_callback triggered for chat_id={chat_id}, period={period}")

    addresses = user_data.get(chat_id, [])
    if not addresses:
        await query.message.reply_text("You haven’t added any addresses yet.")
        return

    summary_data = {}
    wallets_per_coin = {}

    for addr in addresses:
        fills = await fetch_fills(addr["address"], period)
        for f in fills:
            coin = f.get("coin", "?")
            size = float(f.get("sz", 0))
            price = float(f.get("px", 0))
            direction = f.get("dir", "").upper()
            usd = size * price

            if coin not in summary_data:
                summary_data[coin] = {"long_usd": 0.0, "short_usd": 0.0, "total_amount": 0.0}
                wallets_per_coin[coin] = set()

            if direction == "L":
                summary_data[coin]["long_usd"] += usd
            else:
                summary_data[coin]["short_usd"] += usd

            summary_data[coin]["total_amount"] += size
            wallets_per_coin[coin].add(addr["address"])

    if not summary_data:
        await query.message.reply_text("⚠️ No operations in timeframe.")
        return

    lines = []
    idx = 1
    for coin, data in sorted(summary_data.items(), key=lambda x: -x[1]["total_amount"]):
        total_amount = data["total_amount"]
        total_usd = data["long_usd"] + data["short_usd"]
        long_pct = (data["long_usd"] / total_usd * 100) if total_usd > 0 else 0
        short_pct = (data["short_usd"] / total_usd * 100) if total_usd > 0 else 0
        wallet_count = len(wallets_per_coin[coin])

        lines.append(f"{idx}.- {total_amount:,.2f} {coin} (${total_usd:,.2f})")
        lines.append(f"Long {long_pct:.0f}% vs Short {short_pct:.0f}% (Wallets: {wallet_count})")
        idx += 1

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"summary_{period}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_summary")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=reply_markup)

# -----------------------
# Monitoreo y alertas
# -----------------------

async def monitor_wallets(app):
    """
    Revisa cada 20s las wallets de user_data y envía alertas si hay fills nuevos en últimos 10m.
    """
    while True:
        for chat_id, wallets in user_data.items():
            for wallet in wallets:
                address = wallet["address"]
                name = wallet["name"]
                fills = await fetch_fills(address, 10)
                for fill in fills:
                    key = f"{address}-{fill['time']}"
                    if key not in latest_fills:
                        latest_fills[key] = True
                        coin = fill["coin"]
                        size = float(fill["size"])
                        side = "LONG" if fill["isTaker"] else "SHORT"
                        price = float(fill["px"])
                        total = size * price
                        dt = datetime.utcfromtimestamp(fill["time"] / 1000) + timedelta(hours=2)
                        dt_str = dt.strftime("%d/%m/%Y %H:%M")
                        text_alert = (
                            f"📡 <b>{name}</b>\n"
                            f"🟢 <b>Open {side}</b> {size} {coin} (${total:,.2f})\n"
                            f"🕒 {dt_str} UTC+2"
                        )
                        try:
                            await app.bot.send_message(chat_id=chat_id, text=text_alert, parse_mode="HTML")
                        except Exception as e:
                            logging.error(f"Error sending alert: {e}")
        await asyncio.sleep(20)

async def set_bot_commands(app):
    """
    Define la lista de comandos que aparecerán en el botón fijo.
    """
    await app.bot.set_my_commands([
        BotCommand("start",    "🏠 Show main menu"),
        BotCommand("add",      "➕ Add a new wallet"),
        BotCommand("edit",     "✏️ Edit a wallet’s name"),
        BotCommand("remove",   "🗑️ Remove a wallet"),
        BotCommand("list",     "📋 List your wallets"),
        BotCommand("positions","📌 Show open positions"),
        BotCommand("summary",  "📊 Show summary of recent ops"),
    ])

async def on_startup(app):
    """
    Registrado en post_init: arranca monitor_wallets como tarea en background
    y registra los comandos globales.
    """
    app.create_task(monitor_wallets(app))
    await set_bot_commands(app)

#BOTÓN FIJO
async def setup_bot(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("add", "Add a wallet"),
        BotCommand("add_bulk", "Add multiple wallets"),
        BotCommand("list", "List followed wallets"),
        BotCommand("remove", "Remove a wallet"),
        BotCommand("positions", "Show open positions"),
        BotCommand("summary", "Summary of recent trades"),
    ])
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def main():
    application = Application.builder().token(TOKEN).build()
    application.post_init(setup_bot)
    application.run_polling()

# -----------------------
# Inicializar bot
# -----------------------

app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
app.add_handler(CommandHandler("start", start_command))
app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))
app.add_handler(CommandHandler("add", add_command))
app.add_handler(CommandHandler("remove", remove_command))
app.add_handler(CommandHandler("edit", edit_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CommandHandler("list", list_command))
app.add_handler(CommandHandler("positions", positions_command))
app.add_handler(CallbackQueryHandler(positions_callback, pattern="^positions_"))
app.add_handler(CommandHandler("summary", summary_command))
app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_"))

# -----------------------
# Servidor aiohttp (puerto 10000)
# -----------------------

async def handle(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    """
    Inicia un servidor web en / para mantener Render contento.
    """
    app_web = web.Application()
    app_web.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()

# -----------------------
# Función principal
# -----------------------

async def main():
    # 1) Arrancar servidor web en background
    asyncio.create_task(start_web_server())

    # 2) Inicializar y arrancar bot sin conflictos de event loop
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # 3) Mantener el loop vivo
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())










