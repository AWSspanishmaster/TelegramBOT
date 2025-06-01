import logging
import aiohttp
import os
from aiohttp import web
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)
import asyncio

# -----------------------
# Configuración inicial
# -----------------------

TOKEN = os.getenv("TOKEN")

# Diccionarios globales
user_data = {}     # { chat_id: [ {"address": "...", "name": "..."} , ... ] }
latest_fills = {}  # Para evitar alertas duplicadas: { "address-time": True }

# -----------------------
# Funciones auxiliares
# -----------------------

async def fetch_fills(address: str, timeframe_minutes: int):
    """Llama al endpoint userFills de Hyperliquid y filtra las operaciones en los últimos timeframe_minutes."""
    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "userFills",
        "user": address
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            fills = data.get("userFills", {}).get("fills", [])
            now = datetime.utcnow()
            resultado = [
                fill for fill in fills
                if now - datetime.utcfromtimestamp(fill["time"] / 1000) <= timedelta(minutes=timeframe_minutes)
            ]
            return resultado

# -----------------------
# Handlers de Telegram
# -----------------------

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /summary: muestra botones para elegir rango de tiempo."""
    keyboard = [
        [InlineKeyboardButton("1h", callback_data="summary_60")],
        [InlineKeyboardButton("6h", callback_data="summary_360")],
        [InlineKeyboardButton("12h", callback_data="summary_720")],
        [InlineKeyboardButton("24h", callback_data="summary_1440")],
    ]
    await update.message.reply_text(
        "Select a time range:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback cuando el usuario pulsa uno de los botones de /summary."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    period = int(query.data.split("_")[1])      # Ej. "summary_60" → period = 60
    addresses = user_data.get(chat_id, [])

    if not addresses:
        await query.edit_message_text("You haven’t added any addresses yet.")
        return

    msg_lines = [f"📊 <b>Summary ({period//60}h)</b>"]

    for addr in addresses:
        fills = await fetch_fills(addr["address"], period)
        if fills:
            # Calcula volumen total en USD (asumiendo coin * size como aproximación)
            total_volume = sum(abs(float(f["coin"]) * float(f["size"])) for f in fills)
            msg_lines.append(f"\n<b>{addr['name']}</b>\n💰 ${total_volume:,.2f}")
        else:
            msg_lines.append(f"\n<b>{addr['name']}</b>\nNo activity.")

    await query.edit_message_text("\n".join(msg_lines), parse_mode="HTML")

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /positions: muestra botones con cada wallet para ver posiciones abiertas."""
    chat_id = update.message.chat.id
    addresses = user_data.get(chat_id, [])

    if not addresses:
        await update.message.reply_text("You haven’t added any addresses yet.")
        return

    keyboard = [
        [InlineKeyboardButton(addr["name"], callback_data=f"positions_{addr['address']}")]
        for addr in addresses
    ]
    await update.message.reply_text(
        "Select a wallet to view positions:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback cuando el usuario pulsa un botón de /positions para ver posiciones abiertas."""
    query = update.callback_query
    await query.answer()

    # Extrae la dirección: "positions_0x123..." → "0x123..."
    address = query.data.split("_", 1)[1]

    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "userState",
        "user": address
    }
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

# -----------------------
# Monitoreo y alertas
# -----------------------

async def monitor_wallets(app):
    """
    Tarea asíncrona que repite cada 20 segundos:
    - Revisa las wallet de cada chat en user_data.
    - Llama a fetch_fills() para ver operaciones en últimos 10 minutos.
    - Si hay un fill nuevo (según timestamp), envía alerta al chat correspondiente.
    """
    while True:
        for chat_id, wallets in user_data.items():
            for wallet in wallets:
                address = wallet["address"]
                name = wallet["name"]
                fills = await fetch_fills(address, 10)  # Últimos 10 min

                for fill in fills:
                    key = f"{address}-{fill['time']}"
                    if key not in latest_fills:
                        latest_fills[key] = True  # Marcar como ya notificado
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

async def on_startup(app):
    """Esta función se registra en post_init: arranca la tarea de monitoreo."""
    app.create_task(monitor_wallets(app))

# -----------------------
# Inicializar bot
# -----------------------

# Construye la aplicación y registra on_startup en el builder
app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

# Registra comandos y callbacks
app.add_handler(CommandHandler("summary", summary_command))
app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_"))
app.add_handler(CommandHandler("positions", positions_command))
app.add_handler(CallbackQueryHandler(positions_callback, pattern="^positions_"))

# -----------------------
# Servidor aiohttp (puerto 10000)
# -----------------------

async def handle(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    """Inicia un servidor aiohttp simple en /
    que responde "Bot is running" para mantener Render contento."""
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
    # 1) Arrancar servidor web en background (no bloqueante)
    asyncio.create_task(start_web_server())

    # 2) Iniciar el bot con run_polling (inicializa, arranca y hace polling)
    await app.run_polling()

if __name__ == "__main__":
    # Sólo ejecutamos run_polling() desde aquí; NO usamos asyncio.run(app.run_polling())
    asyncio.run(main())




