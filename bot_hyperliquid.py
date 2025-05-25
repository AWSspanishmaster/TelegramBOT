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
import aiohttp
import nest_asyncio
import logging

nest_asyncio.apply()

# --- Servidor HTTP simple para Render ---
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

# --- Configuración bot Telegram ---
TOKEN = os.getenv("TOKEN")
user_addresses = {}

ADD_ADDRESS, ADD_NAME = range(2)
REMOVE_SELECT = 3
POSITIONS_SELECT = 4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- WebSocket handler ---
async def listen_to_ws():
    uri = "wss://api.hyperliquid.xyz/ws"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("Conectado al WebSocket de Hyperliquid")
                for user_id, addresses in user_addresses.items():
                    for addr in addresses:
                        msg = {
                            "method": "subscribe",
                            "subscription": {
                                "type": "userFills",
                                "user": addr["address"]
                            }
                        }
                        await websocket.send(json.dumps(msg))

                while True:
                    response = await websocket.recv()
                    data = json.loads(response)

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
        except Exception as e:
            print(f"WebSocket error: {e}")
            print("Reconectando en 5 segundos...")
            await asyncio.sleep(5)

# --- Función para obtener posiciones ---
async def get_positions(address):
    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "user",
        "user": address
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    print(f"Error {resp.status}: {text}")
                    return {"error": f"{resp.status} - {text}"}
    except Exception as e:
        print(f"Exception getting positions: {e}")
        return {"error": str(e)}

# --- Handlers Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Commands:\n"
        "/add - Add new addresses\n"
        "/remove - Remove addresses\n"
        "/list - Show followed addresses\n"
        "/positions - Show current open positions"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter the wallet address:")
    return ADD_ADDRESS

async def add_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_address"] = update.message.text
    await update.message.reply_text("Now assign a name to this address:")
    return ADD_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    address = context.user_data["new_address"]
    user_id = update.message.from_user.id

    if user_id not in user_addresses:
        user_addresses[user_id] = []

    user_addresses[user_id].append({"name": name, "address": address})
    await update.message.reply_text("✅ Address added!")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("📭 No addresses followed.")
        return

    msg = "📋 Followed addresses:\n"
    for addr in addresses:
        msg += f"- {addr['name']}: {addr['address']}\n"

    await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("📭 Nothing to remove.")
        return ConversationHandler.END

    keyboard = []
    for addr in addresses:
        keyboard.append([InlineKeyboardButton(f"{addr['name']}: {addr['address']}", callback_data=f"toggle_{addr['name']}")])
    keyboard.append([InlineKeyboardButton("DELETE", callback_data="delete")])

    context.user_data['to_delete'] = set()
    await update.message.reply_text("Select addresses to remove:", reply_markup=InlineKeyboardMarkup(keyboard))
    return REMOVE_SELECT

async def remove_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "delete":
        to_delete = context.user_data.get('to_delete', set())
        addresses = user_addresses.get(user_id, [])
        addresses = [a for a in addresses if a["name"] not in to_delete]
        user_addresses[user_id] = addresses
        await query.edit_message_text("🗑️ Deleted!")
        return ConversationHandler.END
    else:
        _, name = data.split("_", 1)
        selected = context.user_data.setdefault('to_delete', set())
        if name in selected:
            selected.remove(name)
        else:
            selected.add(name)

        addresses = user_addresses.get(user_id, [])
        keyboard = []
        for addr in addresses:
            prefix = "✅ " if addr['name'] in selected else ""
            keyboard.append([InlineKeyboardButton(f"{prefix}{addr['name']}: {addr['address']}", callback_data=f"toggle_{addr['name']}")])
        keyboard.append([InlineKeyboardButton("DELETE", callback_data="delete")])

        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return REMOVE_SELECT

# --- /positions handlers ---
async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("📭 No addresses to check.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(f"{addr['name']}", callback_data=f"pos_{addr['address']}")]
        for addr in addresses
    ]

    await update.message.reply_text("Select a wallet to view open positions:", reply_markup=InlineKeyboardMarkup(keyboard))
    return POSITIONS_SELECT

async def show_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, address = query.data.split("_", 1)

    data = await get_positions(address)
    if "error" in data:
        await query.edit_message_text(f"❌ Error getting positions: {data['error']}")
        return ConversationHandler.END

    positions = data.get("assetPositions", [])
    if not positions:
        await query.edit_message_text("📭 No open positions.")
        return ConversationHandler.END

    msg = "📊 Open positions:\n"
    for p in positions:
        if p.get("position", {}).get("sz", 0) != 0:
            coin = p["position"]["coin"]
            sz = p["position"]["sz"]
            entry = p["position"]["entryPx"]
            msg += f"- {coin}: {sz} @ {entry}\n"

    await query.edit_message_text(msg or "📭 No open positions.")
    return ConversationHandler.END

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

    positions_conv = ConversationHandler(
        entry_points=[CommandHandler("positions", positions)],
        states={
            POSITIONS_SELECT: [CallbackQueryHandler(show_positions)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(remove_conv)
    app.add_handler(positions_conv)
    app.add_handler(CommandHandler("list", list_addresses))

    asyncio.create_task(listen_to_ws())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())



