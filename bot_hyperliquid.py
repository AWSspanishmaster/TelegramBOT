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
    # Default lang to English if not set
    if user_id not in user_languages:
        user_languages[user_id] = "en"
    await update.message.reply_text(get_text(user_id, "start"), reply_markup=language_keyboard(user_id))

def language_keyboard(user_id):
    lang = user_languages.get(user_id, "en")
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
    lang = user_languages.get(user_id, "en")
    if not addresses:
        await update.message.reply_text(get_text(user_id, "no_addresses"))
    else:
        # Mejor formato visual con emojis y alineación
        lines = [f"• {name}: `{addr}`" for addr, name in addresses.items()]
        text = f"{get_text(user_id, 'your_addresses')}\n\n" + "\n".join(lines)
        await update.message.reply_text(text, parse_mode="Markdown")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = " ".join(context.args)
    lang = user_languages.get(user_id, "en")
    if address in user_addresses.get(user_id, {}):
        del user_addresses[user_id][address]
        await update.message.reply_text(get_text(user_id, "address_removed").format(address))
    else:
        await update.message.reply_text(get_text(user_id, "address_not_found"))

async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    addresses = user_addresses.get(user_id, {})
    lang = user_languages.get(user_id, "en")
    if not addresses:
        await update.message.reply_text(get_text(user_id, "no_addresses"))
        return

    # Botones para elegir dirección + botón editar alias + botón cambio idioma
    keyboard = [
        [InlineKeyboardButton(f"{name}", callback_data=f"pos_{addr}")]
        for addr, name in addresses.items()
    ]
    keyboard.append([
        InlineKeyboardButton("✏️ Edit Nicknames", callback_data="edit_nicknames"),
        InlineKeyboardButton("🌐 Language", callback_data="change_language"),
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(get_text(user_id, "select_address_fills"), reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    lang = user_languages.get(user_id, "en")

    # Manejo de selección de posiciones de wallets
    if data.startswith("pos_"):
        address = data[4:]
        name = user_addresses.get(user_id, {}).get(address, address)
        # Aquí iría la lógica para traer fills o posiciones
        # Solo mockup visual con emojis y mejor formato
        text = f"📊 {get_text(user_id, 'recent_fills_for').format(name)}\n\n" \
               f"🔹 Example position 1\n🔹 Example position 2"
        await query.edit_message_text(text)

    elif data == "edit_nicknames":
        addresses = user_addresses.get(user_id, {})
        if not addresses:
            await query.edit_message_text(get_text(user_id, "no_addresses_edit"))
            return
        keyboard = [
            [InlineKeyboardButton(f"{name}", callback_data=f"editnick_{addr}")]
            for addr, name in addresses.items()
        ]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_positions")])
        await query.edit_message_text("✏️ Select nickname to edit:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("editnick_"):
        address = data[len("editnick_") :]
        user_states[user_id] = {"stage": "editing_nickname", "address": address}
        await query.edit_message_text(get_text(user_id, "edit_nickname_prompt").format(address))

    elif data == "back_positions":
        # Volver al listado de posiciones
        await positions(update, context)

    elif data == "change_language":
        await query.edit_message_text(get_text(user_id, "choose_language"), reply_markup=language_keyboard(user_id))

    elif data == "refresh_summary":
        # Recalcular resumen con parámetros previos guardados (mock)
        # Suponemos que tenemos user_states[user_id]["last_summary_period"] guardado
        period = user_states.get(user_id, {}).get("last_summary_period", "24h")
        await send_summary(update, context, period, refresh=True)

async def edit_nickname_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = user_states.get(user_id)
    if not state or state.get("stage") != "editing_nickname":
        return
    address = state["address"]
    user_addresses[user_id][address] = text
    await update.message.reply_text(get_text(user_id, "nickname_updated"))
    del user_states[user_id]

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = user_languages.get(user_id, "en")

    buttons = [
        [
            InlineKeyboardButton("1h", callback_data="summary_1h"),
            InlineKeyboardButton("6h", callback_data="summary_6h"),
            InlineKeyboardButton("12h", callback_data="summary_12h"),
            InlineKeyboardButton("24h", callback_data="summary_24h"),
        ],
        [
            InlineKeyboardButton(get_text(user_id, "refresh_button"), callback_data="refresh_summary"),
            InlineKeyboardButton("🌐", callback_data="change_language"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(get_text(user_id, "select_timeframe"), reply_markup=reply_markup)

async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str, refresh=False):
    # Actualizar user_states para guardar último periodo
    user_id = update.effective_user.id
    user_states.setdefault(user_id, {})["last_summary_period"] = period
    lang = user_languages.get(user_id, "en")

    # Simulación de datos de resumen con emojis y formato bonito
    summary_text = f"📊 {get_text(user_id, 'most_traded').format(period)}\n\n"
    summary_text += "🔹 BTC: 150 trades\n🔹 ETH: 120 trades\n🔹 USDT: 90 trades\n\n"
    summary_text += f"⚖️ {get_text(user_id, 'long_vs_short').format(60, 40, 5)}"

    if refresh:
        # Si es refresh, editamos el mensaje actual
        await update.callback_query.edit_message_text(summary_text, reply_markup=update.callback_query.message.reply_markup)
    else:
        await update.message.reply_text(summary_text, reply_markup=language_keyboard(user_id))

async def summary_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("summary_"):
        period = data.split("_")[1]
        await send_summary(update, context, period)

async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_addresses))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("summary", summary))

    app.add_handler(CallbackQueryHandler(language_button_handler, pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(button_handler, pattern=r"^(pos_|edit_nick|back_positions|change_language|refresh_summary)"))
    app.add_handler(CallbackQueryHandler(summary_button_handler, pattern=r"^summary_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, edit_nickname_receive))

    # aiohttp web server para mantener vivo el bot en Render
    async def handle(request):
        return web.Response(text="Bot is running")

    runner = web.AppRunner(web.Application())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())








