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

# Telegram credentials
import os
TOKEN = os.getenv("TOKEN")
USER_ID = 980727505

# Diccionario para almacenar usuarios y direcciones
user_addresses = {}

# Estados de conversación
ADD_ADDRESS, ADD_NAME = range(2)
REMOVE_SELECT = 3

# Logger para debug
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# WebSocket handler
async def listen_to_ws():
    uri = "wss://api.hyperliquid.xyz/ws"
    while True:
        try:
            async with websockets.connect(uri) as websocket:
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
                                f"📢 Nuevo fill detectado\n"
                                f"👤 Trader: {username}\n"
                                f"🪙 Coin: {coin}\n"
                                f"📈 Opèration: {side}\n"
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

# Start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Welcomwe! Check the available commands in the list below:\n"
        "/add - Add new addresses\n"
        "/remove - Remove tracked addresses\n"
        "/list - Check all the followed addresses"
    )

# ADD flow
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

# LIST
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

# REMOVE flow con selección múltiple con ticks y botón DELETE
async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    addresses = user_addresses.get(user_id, [])

    if not addresses:
        await update.message.reply_text("Empty list.")
        return ConversationHandler.END

    keyboard = []
    for addr in addresses:
        keyboard.append([InlineKeyboardButton(f"{addr['name']}: {addr['address']}", callback_data=f"toggle_{addr['name']}")])
    keyboard.append([InlineKeyboardButton("DELETE", callback_data="delete")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data['to_delete'] = set()

    await update.message.reply_text("Please select the addresses you need to be removed:", reply_markup=reply_markup)
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

        addresses = user_addresses.get(user_id, [])
        addresses = [a for a in addresses if a["name"] not in to_delete]
        user_addresses[user_id] = addresses

        await query.edit_message_text("Erased!")
        return ConversationHandler.END
    else:
        _, name = data.split("_", 1)
        selected = context.user_data.setdefault('to_delete', set())

        if name in selected:
            selected.remove(name)
        else:
            selected.add(name)

        # Reconstruir teclado con marcas de selección
        addresses = user_addresses.get(user_id, [])
        keyboard = []
        for addr in addresses:
            prefix = "✅ " if addr['name'] in selected else ""
            keyboard.append([InlineKeyboardButton(f"{prefix}{addr['name']}: {addr['address']}", callback_data=f"toggle_{addr['name']}")])
        keyboard.append([InlineKeyboardButton("DELETE", callback_data="delete")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        return REMOVE_SELECT

# Main
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

    asyncio.create_task(listen_to_ws())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
