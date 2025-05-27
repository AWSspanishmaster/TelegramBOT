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

# Datos de usuarios: {user_id: {address: name}}
user_addresses = {}
# Estado para flujos por usuario {user_id: {"stage": ..., ...}}
user_states = {}
# Idioma por usuario: "en" o "es"
user_languages = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Textos traducidos
TEXTS = {
    "start": {
        "en": "Welcome! Use /add, /list, /remove, /positions, or /summary.",
        "es": "¡Bienvenido! Usa /add, /list, /remove, /positions o /summary.",
    },
    "write_address": {"en": "✍️ Write the address", "es": "✍️ Escribe la dirección"},
    "invalid_address": {
        "en": "⚠️ Invalid address format.",
        "es": "⚠️ Formato de dirección inválido.",
    },
    "name_it": {"en": "🏷️ Name it", "es": "🏷️ Ponle un nombre"},
    "address_exists": {
        "en": "⚠️ Address already added.",
        "es": "⚠️ La dirección ya está añadida.",
    },
    "done": {"en": "✅ Done!", "es": "✅ ¡Listo!"},
    "no_addresses": {"en": "📭 No addresses added.", "es": "📭 No hay direcciones añadidas."},
    "your_addresses": {"en": "📋 Your addresses:", "es": "📋 Tus direcciones:"},
    "address_removed": {
        "en": "🗑️ Address removed: {}",
        "es": "🗑️ Dirección eliminada: {}",
    },
    "address_not_found": {
        "en": "⚠️ Address not found.",
        "es": "⚠️ Dirección no encontrada.",
    },
    "select_address_fills": {
        "en": "📌 Select an address to view recent fills:",
        "es": "📌 Selecciona una dirección para ver operaciones recientes:",
    },
    "no_fills": {
        "en": "⚠️ No recent fills or error for {}.",
        "es": "⚠️ No hay operaciones recientes o error para {}.",
    },
    "recent_fills_for": {"en": "📈 Recent fills for {}:", "es": "📈 Operaciones recientes para {}:"},
    "select_timeframe": {
        "en": "⏱️ Select timeframe:",
        "es": "⏱️ Selecciona intervalo:",
    },
    "fetching_fills": {
        "en": "⏳ Fetching fills for last {}... Please wait.",
        "es": "⏳ Buscando operaciones de las últimas {}... Por favor espera.",
    },
    "no_fills_found": {
        "en": "⚠️ No fills found in the given timeframe.",
        "es": "⚠️ No se encontraron operaciones en el intervalo dado.",
    },
    "most_traded": {
        "en": "Most traded coins in the last {}:",
        "es": "Monedas más negociadas en las últimas {}:",
    },
    "long_vs_short": {
        "en": "Long {}% vs Short {}% (Wallets: {})",
        "es": "Long {}% vs Short {}% (Carteras: {})",
    },
    "choose_language": {"en": "🌐 Choose language / Elige idioma", "es": "🌐 Elige idioma / Choose language"},
    "language_changed": {
        "en": "Language changed to English 🇬🇧",
        "es": "Idioma cambiado a Español 🇪🇸",
    },
    "edit_nickname_prompt": {
        "en": "✏️ Send the new nickname for address:\n{}",
        "es": "✏️ Envía el nuevo apodo para la dirección:\n{}",
    },
    "nickname_updated": {
        "en": "✅ Nickname updated!",
        "es": "✅ ¡Apodo actualizado!",
    },
    "no_addresses_edit": {
        "en": "📭 No addresses to edit.",
        "es": "📭 No hay direcciones para editar.",
    },
    "refresh_button": {
        "en": "🔄 Refresh",
        "es": "🔄 Actualizar",
    }
}

def get_text(user_id, key):
    lang = user_languages.get(user_id, "en")
    return TEXTS.get(key, {}).get(lang, "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_languages:
        user_languages[user_id] = "en"
    await update.message.reply_text(get_text(user_id, "start"), reply_markup=language_keyboard(user_id))

def language_keyboard(user_id):
    buttons = [
        InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        InlineKeyboardButton("🇪🇸 Español", callback_data="lang_es"),
    ]
    return InlineKeyboardMarkup([buttons])

async def language_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data == "lang_en":
        user_languages[user_id] = "en"
        await query.edit_message_text(TEXTS["language_changed"]["en"], reply_markup=language_keyboard(user_id))
    elif data == "lang_es":
        user_languages[user_id] = "es"
        await query.edit_message_text(TEXTS["language_changed"]["es"], reply_markup=language_keyboard(user_id))

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"stage": "awaiting_address"}
    await update.message.reply_text(get_text(user_id, "write_address"))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in user_states:
        return

    state = user_states[user_id]
    lang = user_languages.get(user_id, "en")

    if state["stage"] == "awaiting_address":
        if not text.startswith("0x") or len(text) != 42:
            await update.message.reply_text(get_text(user_id, "invalid_address"))
            return
        state["address"] = text
        state["stage"] = "awaiting_name"
        await update.message.reply_text(get_text(user_id, "name_it"))

    elif state["stage"] == "awaiting_name":
        name = text
        address = state["address"]
        user_addresses.setdefault(user_id, {})
        if address in user_addresses[user_id]:
            await update.message.reply_text(get_text(user_id, "address_exists"))
        else:
            user_addresses[user_id][address] = name
            await update.message.reply_text(get_text(user_id, "done"))
        del user_states[user_id]

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await update.message.reply_text(get_text(user_id, "no_addresses"))
    else:
        lines = [f"• {name}: `{addr}`" for addr, name in addresses.items()]
        text = f"{get_text(user_id, 'your_addresses')}\n\n" + "\n".join(lines)
        await update.message.reply_text(text, parse_mode="Markdown")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    if address in user_addresses.get(user_id, {}):
        del user_addresses[user_id][address]
        await update.message.reply_text(get_text(user_id, "address_removed").format(address))
    else:
        await update.message.reply_text(get_text(user_id, "address_not_found"))

async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await update.message.reply_text(get_text(user_id, "no_addresses"))
        return

    keyboard = [
        [InlineKeyboardButton(f"{name}", callback_data=f"pos_{addr}")]
        for addr, name in addresses.items()
    ]
    keyboard.append([InlineKeyboardButton(get_text(user_id, "choose_language"), callback_data="change_lang")])
    await update.message.reply_text(get_text(user_id, "select_address_fills"),
                                    reply_markup=InlineKeyboardMarkup(keyboard))

async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("pos_"):
        address = data[4:]
        # Aquí simular una consulta de fills recientes (deberías sustituir por llamada real)
        fills = [
            {"time": "2025-05-27 12:00", "coin": "BTC", "side": "Long", "qty": 0.1, "price": 30000},
            {"time": "2025-05-27 13:00", "coin": "ETH", "side": "Short", "qty": 2, "price": 2000},
        ]
        if not fills:
            await query.edit_message_text(get_text(user_id, "no_fills").format(address))
            return
        text = f"{get_text(user_id, 'recent_fills_for').format(address)}\n\n"
        for f in fills:
            line = f"{f['time']} - {f['coin']} - {f['side']} - Qty: {f['qty']} at ${f['price']}"
            text += line + "\n"
        # Añadimos botón para cambiar idioma y volver
        buttons = [
            [
                InlineKeyboardButton(get_text(user_id, "choose_language"), callback_data="change_lang"),
                InlineKeyboardButton("⬅️ Back", callback_data="back_positions"),
            ]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "back_positions":
        await positions(update, context)

    elif data == "change_lang":
        await language_button_handler(update, context)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Mostrar botones para intervalos
    buttons = [
        [InlineKeyboardButton("1h", callback_data="sum_1h"),
         InlineKeyboardButton("6h", callback_data="sum_6h")],
        [InlineKeyboardButton("12h", callback_data="sum_12h"),
         InlineKeyboardButton("24h", callback_data="sum_24h")],
        [InlineKeyboardButton(get_text(user_id, "choose_language"), callback_data="change_lang")],
    ]
    await update.message.reply_text(get_text(user_id, "select_timeframe"),
                                    reply_markup=InlineKeyboardMarkup(buttons))

async def summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("sum_"):
        period = data[4:]
        # Aquí simular resumen, reemplazar con consulta real
        text = f"{get_text(user_id, 'most_traded').format(period)}\n"
        # Datos simulados
        coins = [("BTC", 10), ("ETH", 7), ("SOL", 5)]
        for coin, vol in coins:
            text += f"• {coin}: {vol} trades\n"

        # Botón refrescar y cambio idioma + volver
        buttons = [
            [
                InlineKeyboardButton(get_text(user_id, "refresh_button"), callback_data=data),
                InlineKeyboardButton("⬅️ Back", callback_data="summary_back"),
            ],
            [InlineKeyboardButton(get_text(user_id, "choose_language"), callback_data="change_lang")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "summary_back":
        # Volver a menú resumen
        await summary(update, context)

    elif data == "change_lang":
        await language_button_handler(update, context)

async def edit_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    if not addresses:
        await update.message.reply_text(get_text(user_id, "no_addresses_edit"))
        return

    buttons = [[InlineKeyboardButton(name, callback_data=f"editnick_{addr}")] for addr, name in addresses.items()]
    buttons.append([InlineKeyboardButton(get_text(user_id, "choose_language"), callback_data="change_lang")])
    await update.message.reply_text("✏️ Select address to edit nickname:", reply_markup=InlineKeyboardMarkup(buttons))

async def edit_nickname_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("editnick_"):
        address = data[9:]
        user_states[user_id] = {"stage": "awaiting_new_nickname", "address": address}
        await query.edit_message_text(get_text(user_id, "edit_nickname_prompt").format(address))

async def handle_new_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id].get("stage") != "awaiting_new_nickname":
        return
    new_nick = update.message.text.strip()
    address = user_states[user_id]["address"]
    if user_id in user_addresses and address in user_addresses[user_id]:
        user_addresses[user_id][address] = new_nick
        await update.message.reply_text(get_text(user_id, "nickname_updated"))
    else:
        await update.message.reply_text(get_text(user_id, "address_not_found"))
    del user_states[user_id]

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ Unknown command")

async def run_webhook_server():
    async def handle(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

async def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("list", list_addresses))
    application.add_handler(CommandHandler("remove", remove))
    application.add_handler(CommandHandler("positions", positions))
    application.add_handler(CommandHandler("summary", summary))
    application.add_handler(CommandHandler("editnickname", edit_nickname))

    application.add_handler(CallbackQueryHandler(language_button_handler, pattern="^lang_"))
    application.add_handler(CallbackQueryHandler(positions_callback, pattern="^(pos_|back_positions|change_lang)$"))
    application.add_handler(CallbackQueryHandler(summary_callback, pattern="^(sum_|summary_back|change_lang)$"))
    application.add_handler(CallbackQueryHandler(edit_nickname_callback, pattern="^editnick_"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_nickname))

    # Run web server alongside bot polling
    runner = asyncio.create_task(run_webhook_server())
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())









