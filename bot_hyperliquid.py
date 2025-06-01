import logging
import aiohttp
import os
from aiohttp import web
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Para el flujo en /add: user_states[chat_id] = {"stage": "awaiting_address"/"awaiting_name", "address": "..."}
user_states = {}

# Para evitar alertas duplicadas: { "address-time": True }
latest_fills = {}

# -----------------------
# Funciones auxiliares
# -----------------------

async def fetch_fills(address: str, timeframe_minutes: int):
    """
    Llama al endpoint userFills de Hyperliquid y filtra operaciones
    en los últimos timeframe_minutes.
    """
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "userFills", "user": address}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            fills = data.get("userFills", {}).get("fills", [])
            now = datetime.utcnow()
            resultado = [
                fill
                for fill in fills
                if now - datetime.utcfromtimestamp(fill["time"] / 1000)
                <= timedelta(minutes=timeframe_minutes)
            ]
            return resultado

# -----------------------
# Handlers de Telegram
# -----------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /start: muestra menú con opciones.
    """
    keyboard = [
        [InlineKeyboardButton("➕ Add", callback_data="menu_add")],
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

    if data == "menu_add":
        chat_id = query.from_user.id
        user_states[chat_id] = {"stage": "awaiting_address"}
        await query.edit_message_text("✍️ Please send the address (0x...):")
    elif data == "menu_list":
        await list_command(update, context, from_button=True)
    elif data == "menu_positions":
        await positions_command(update, context, from_button=True)
    elif data == "menu_summary":
        await summary_command(update, context, from_button=True)

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /add: inicia flujo para añadir direccion.
    """
    chat_id = update.effective_user.id
    user_states[chat_id] = {"stage": "awaiting_address"}
    await update.message.reply_text("✍️ Please send the address (0x...):")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja los mensajes de texto para el flujo de /add:
    - stage == "awaiting_address": validar y pedir nombre
    - stage == "awaiting_name": guardar y confirmar
    """
    chat_id = update.effective_user.id
    text = update.message.text.strip()

    if chat_id not in user_states:
        return  # No estamos en un flujo de /add

    state = user_states[chat_id]

    # 1) Esperando dirección
    if state["stage"] == "awaiting_address":
        if not (text.startswith("0x") and len(text) == 42):
            await update.message.reply_text("⚠️ Invalid address format (must start with 0x and be 42 chars).")
            return
        state["address"] = text
        state["stage"] = "awaiting_name"
        await update.message.reply_text("🏷️ Now send a name for this wallet:")
        return

    # 2) Esperando nombre
    if state["stage"] == "awaiting_name":
        name = text
        address = state["address"]
        # Añadimos a user_data
        user_data.setdefault(chat_id, [])
        # Verificar duplicado
        exists = any(w["address"] == address for w in user_data[chat_id])
        if exists:
            await update.message.reply_text("⚠️ Address already added.")
        else:
            user_data[chat_id].append({"address": address, "name": name})
            await update.message.reply_text("✅ Address added!")
        # Limpiamos estado
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

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /remove <address>: elimina una dirección de la lista.
    """
    chat_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("⚠️ Use: /remove <address>")
        return
    address = context.args[0]
    addresses = user_data.get(chat_id, [])
    new_list = [w for w in addresses if w["address"] != address]
    if len(new_list) < len(addresses):
        user_data[chat_id] = new_list
        await update.message.reply_text(f"🗑️ Address removed: {address}")
    else:
        await update.message.reply_text("⚠️ Address not found.")

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
    Callback de botones de /positions: llama a userState y muestra posiciones.
    """
    query = update.callback_query
    await query.answer()
    address = query.data.split("_", 1)[1]

    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "userState", "user": address}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            positions = data.get("userState", {}).get("assetPositions", [])

    if not positions:
        await query.edit_message_text("No open positions.")
        return

    lines = ["📈 <b>Open Positions</b>"]
    for p in positions:
        pos_size = p.get("position", {}).get("szi", 0)
        if pos_size != 0:
            coin = p["position"]["coin"]
            size = float(pos_size)
            side = "LONG" if size > 0 else "SHORT"
            lines.append(f"{coin}: <b>{side}</b> {abs(size)}")

    await query.edit_message_text("\n".join(lines), parse_mode="HTML")

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
    """
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

async def on_startup(app):
    """
    Registrado en post_init: arranca monitor_wallets como tarea en background.
    """
    app.create_task(monitor_wallets(app))

# -----------------------
# Inicializar bot
# -----------------------

app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

# Registro de handlers
app.add_handler(CommandHandler("start", start_command))
app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))
app.add_handler(CommandHandler("add", add_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CommandHandler("list", list_command))
app.add_handler(CommandHandler("remove", remove_command))
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
