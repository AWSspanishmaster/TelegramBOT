import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
import json
import websockets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, ConversationHandler, filters, CallbackQueryHandler
)
import nest_asyncio
import logging

nest_asyncio.apply()

# --- Servidor HTTP para mantener vivo en Render ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running')

def run_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

Thread(target=run_server, daemon=True).start()

# --- Configuración del bot ---
TOKEN = os.getenv("TOKEN")
USER_ID = 980727505  # Puedes eliminarlo si usas múltiples usuarios

user_addresses = {}
ADD_ADDRESS, ADD_NAME = range(2)
REMOVE_SELECT = 3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- WebSocket Listener ---
async def listen_to_ws():
    uri = "wss://api.hyperliquid.xyz/ws"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("Conectado al WebSocket de Hyperliquid")

                for user_id, addresses in user_addresses.items():
                    for addr in addresses:
                        for channel in ["userFills", "userState"]:
                            msg = {
                                "method": "subscribe",
                                "subscription": {
                                    "type": channel,
                                    "user": addr["address"]
                                }
                            }
                            await websocket.send(json.dumps(msg))

                while True:
                    response = await websocket.recv()
                    data = json.loads(response)
                    print("🔵 WebSocket response:", json.dumps(data, indent=2))  # Debug

                    # Fills (operaciones)
                    if data.get("channel") == "userFills":
                        fills = data.get("data", {}).get("fills", [])
                        if fills:
                            fill_info = fills[-1]
                            username = fill_info.get("username")
                            coin = fill_info.get("coin")
                            side = fill_info.get("side")
                            px = fill_info.get("px")
                            sz = fill_info.get("sz")

                            text = (
                                f"📢 New operation detected\n"
                                f"👤 Trader: {username}\n"
                                f"🪙 Coin: {coin}\n"
                                f"📈 Type: {side}\n"
                                f"💰 Price: {px}\n"
                                f"📦 Size: {sz}"
                            )

                            for user_id, addresses in user_addresses.items():
                                for addr in addresses:
                                    if addr["address"] == username:
                                        await app.bot.send_message(chat_id=user_id, text=text)

                    # Positions (posiciones abiertas)
                    elif data.get("channel") == "userState":
                        username = data.get("user")
                        user_data_state = data.get("data", {})

                        positions = user_data_state.get("assetPositions", [])

                        open_positions = [
                            pos for pos in positions
                            if float(pos.get("position", 0)) != 0
                        ]

                        if open_positions:
                            msg = f"📊 Open positions for {username}:\n"
                            for pos in open_positions:
                                msg += (
                                    f"\n🪙 Coin: {pos.get('coin')}\n"
                                    f"📦 Size: {pos.get('position')}\n"
                                    f"🎯 Entry: {pos.get('entryPx')}\n"
                                    f"📉 Liquidation: {pos.get('liqPx')}\n"
                                    f"💸 Unrealized PnL: {pos.get('unrealizedPnl')}\n"
                                )

                            for user_id, addresses in user_addresses.items():
                                for addr in addresses:
                                    if addr["address"] == username:
                                        await app.bot.send_message(chat_id=user_id, text=msg)

        except Exception as e:
            print(f"WebSocket error: {e}")
            print("Reconectando en 5 segundos...")
            await asyncio.sleep(5)

# --- Handlers de Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Welcome! Check the available commands:\n"
        "/add - Add new addresses\n"
        "/remove - Remove tracked addresses\n"
        "/list - Check all followed addresses"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please introduce the address")
    return ADD_ADDRESS

async def add_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_address"] = update.message.text
    await update.message.reply_text("Now name it")
    return ADD_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    address = context.user_data["new_address"]
    user_id = update.message.from_user.id

    if user_id not in user_addresses:
        user_addresses[user_id] = []

    user_addresses[user_id].append({"name": name, "address": address})
    await update.message.reply_text("✅ Done")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("Empty list")
        return

    msg = "📋 Addresses already tracked:\n"
    for addr in addresses:
        msg += f"- {addr['name']}: {addr['address']}\n"

    await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("Empty list.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(f"{addr['name']}: {addr['address']}", callback_data=f"toggle_{addr['name']}")]
        for addr in addresses
    ]
    keyboard.append([InlineKeyboardButton("DELETE", callback_data="delete")])
    context.user_data['to_delete'] = set()

    await update.message.reply_text(
        "Please select the addresses you want to remove:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return REMOVE_SELECT

async def remove_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "delete":
        to_delete = context.user_data.get('to_delete', set())
        if not to_delete:
            await query.edit_message_text("You didn't select anything yet!")
            return ConversationHandler.END

        user_addresses[user_id] = [
            a for a in user_addresses.get(user_id, [])
            if a["name"] not in to_delete
        ]
        await query.edit_message_text("Erased!")
        return ConversationHandler.END
    else:
        _, name = data.split("_", 1)
        selected = context.user_data.setdefault('to_delete', set())

        if name in selected:
            selected.remove(name)
        else:
            selected.add(name)

        addresses = user_addresses.get(user_id, [])
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅ ' if addr['name'] in selected else ''}{addr['name']}: {addr['address']}",
                callback_data=f"toggle_{addr['name']}"
            )] for addr in addresses
        ]
        keyboard.append([InlineKeyboardButton("DELETE", callback_data="delete")])

        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return REMOVE_SELECT

# --- Main ---
async def main():
    global app
    app = ApplicationBuilder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add)],
        states={
            ADD_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_address)],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    remove_conv = ConversationHandler(
        entry_points=[CommandHandler("remove", remove)],
        states={
            REMOVE_SELECT: [CallbackQueryHandler(remove_select)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(remove_conv)
    app.add_handler(CommandHandler("list", list_addresses))

    # Inicia el WebSocket listener en segundo plano
    asyncio.create_task(listen_to_ws())

    await app.run_polling()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
