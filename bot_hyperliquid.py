import os
import json
import asyncio
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiohttp
import websockets
import nest_asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, ConversationHandler, CallbackQueryHandler, filters
)

nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

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
user_addresses = {}

ADD_ADDRESS, ADD_NAME = range(2)
REMOVE_SELECT = 3
SELECT_POSITION_ADDRESS = 4

# --- WebSocket: notificaciones de operaciones ---
async def listen_to_ws():
    uri = "wss://api.hyperliquid.xyz/ws"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
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
                        await ws.send(json.dumps(msg))

                while True:
                    response = await ws.recv()
                    data = json.loads(response)

                    if data.get("channel") == "userFills":
                        fills = data.get("data", {}).get("fills", [])
                        if fills:
                            fill = fills[-1]
                            username = fill.get("username")
                            text = (
                                f"📢 New operation detected\n"
                                f"👤 Trader: {username}\n"
                                f"🪙 Coin: {fill.get('coin')}\n"
                                f"📈 Type: {fill.get('side')}\n"
                                f"💰 Price: {fill.get('px')}\n"
                                f"📦 Size: {fill.get('sz')}"
                            )
                            for uid, addresses in user_addresses.items():
                                for a in addresses:
                                    if a["address"] == username:
                                        await app.bot.send_message(chat_id=uid, text=text)
        except Exception as e:
            print(f"WebSocket error: {e}")
            await asyncio.sleep(5)

# --- Funciones Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome!\nUse:\n/add\n/remove\n/list\n/positions")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📨 Send the address:")
    return ADD_ADDRESS

async def add_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_address"] = update.message.text.strip()
    await update.message.reply_text("📝 Now send a name:")
    return ADD_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    address = context.user_data["new_address"]
    user_id = update.message.from_user.id
    user_addresses.setdefault(user_id, []).append({"name": name, "address": address})
    await update.message.reply_text("✅ Address added!")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 Empty list")
        return
    msg = "📋 Tracked addresses:\n"
    for a in addresses:
        msg += f"- {a['name']}: {a['address']}\n"
    await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("📭 Empty list")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(f"{a['name']}: {a['address']}", callback_data=f"toggle_{a['name']}")]
                for a in addresses]
    keyboard.append([InlineKeyboardButton("🗑️ DELETE", callback_data="delete")])

    context.user_data['to_delete'] = set()
    await update.message.reply_text("☑️ Select to remove:", reply_markup=InlineKeyboardMarkup(keyboard))
    return REMOVE_SELECT

async def remove_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "delete":
        to_delete = context.user_data.get('to_delete', set())
        addresses = user_addresses.get(user_id, [])
        user_addresses[user_id] = [a for a in addresses if a["name"] not in to_delete]
        await query.edit_message_text("✅ Removed!")
        return ConversationHandler.END
    else:
        _, name = data.split("_", 1)
        selected = context.user_data.setdefault('to_delete', set())
        selected ^= {name}
        addresses = user_addresses.get(user_id, [])
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅ ' if a['name'] in selected else ''}{a['name']}: {a['address']}",
                callback_data=f"toggle_{a['name']}"
            )] for a in addresses
        ]
        keyboard.append([InlineKeyboardButton("🗑️ DELETE", callback_data="delete")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return REMOVE_SELECT

# --- POSITIONS ---
async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        await update.message.reply_text("❌ No addresses to check.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(f"{a['name']}", callback_data=a["address"])] for a in addresses]
    await update.message.reply_text("📍 Choose address to check positions:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_POSITION_ADDRESS

async def show_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    address = query.data

    async with aiohttp.ClientSession() as session:
        try:
            payload = {"type": "allUserData", "user": address}
            async with session.post("https://api.hyperliquid.xyz/info", json=payload) as resp:
                if resp.status != 200:
                    await query.edit_message_text(f"❌ Error getting positions: {resp.status}")
                    return ConversationHandler.END

                data = await resp.json()
                positions = data.get("assetPositions", [])
                if not positions:
                    await query.edit_message_text(f"📭 No open positions for `{address}`", parse_mode="Markdown")
                    return ConversationHandler.END

                msg = f"📊 Open positions for `{address}`:\n\n"
                for p in positions:
                    if float(p.get("size", 0)) != 0:
                        msg += (
                            f"• Coin: {p['coin']}\n"
                            f"  - Size: {p['size']}\n"
                            f"  - Entry: {p['entryPx']}\n"
                            f"  - PnL: {p['unrealizedPnl']}\n\n"
                        )
                await query.edit_message_text(msg, parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {e}")
    return ConversationHandler.END

# --- Main ---
async def main():
    global app
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_addresses))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add)],
        states={
            ADD_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_address)],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("remove", remove)],
        states={REMOVE_SELECT: [CallbackQueryHandler(remove_select)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("positions", positions)],
        states={SELECT_POSITION_ADDRESS: [CallbackQueryHandler(show_positions)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    asyncio.create_task(listen_to_ws())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
