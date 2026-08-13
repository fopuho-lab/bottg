import os
import telebot
from flask import Flask, request

# 1. Токен бота і ID чату адмінів — беремо з змінних оточення (Render Environment Variables)
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))

# Публічна адреса твого сервісу на Render, наприклад: https://my-bot.onrender.com
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Список забанених ID у пам'яті
banned_users = set()


# Допоміжна функція для пошуку User ID
def extract_user_id(message):
    if message.text:
        args = message.text.split()
        if len(args) > 1 and args[1].isdigit():
            return int(args[1])

    if message.reply_to_message:
        reply = message.reply_to_message

        if reply.forward_from:
            return reply.forward_from.id

        text = reply.caption or reply.text
        if text:
            for line in text.split('\n'):
                if 'User ID:' in line:
                    try:
                        return int(
                            line.split('User ID:')[1]
                            .strip()
                            .replace('<code>', '')
                            .replace('</code>', '')
                        )
                    except Exception:
                        pass
    return None


# === 1. КОМАНДА /start ===
@bot.message_handler(commands=['start'], chat_types=['private'])
def send_welcome(message):
    if message.from_user.id in banned_users:
        return

    bot.send_message(
        message.chat.id,
        "<b>⚔️ Steppenwolfskreuz Rekorde — Manager Bot</b>\n\n"
        "Надішліть сюди своє повідомлення, запитання чи демо-запис. "
        "Адміністрація розглядає всі звернення.",
        parse_mode='HTML',
    )


# === 2. КОМАНДА /ban У ЧАТІ АДМІНІВ ===
@bot.message_handler(commands=['ban'], chat_types=['group', 'supergroup'])
def ban_user(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return

    user_id = extract_user_id(message)

    if user_id:
        banned_users.add(user_id)
        bot.reply_to(
            message,
            f"🚫 <b>Користувача забанено!</b>\n🆔 ID: <code>{user_id}</code>\nЙого повідомлення більше не надходитимуть.",
            parse_mode='HTML',
        )
    else:
        bot.reply_to(
            message,
            "⚠️ Дайте відповідь (Reply) на повідомлення або вкажіть ID: <code>/ban 123456789</code>",
            parse_mode='HTML',
        )


# === 3. КОМАНДА /unban У ЧАТІ АДМІНІВ ===
@bot.message_handler(commands=['unban'], chat_types=['group', 'supergroup'])
def unban_user(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return

    user_id = extract_user_id(message)

    if user_id:
        if user_id in banned_users:
            banned_users.remove(user_id)
            bot.reply_to(
                message,
                f"✅ <b>Користувача розбанено!</b>\n🆔 ID: <code>{user_id}</code>\nТепер він знову може писати боту.",
                parse_mode='HTML',
            )
        else:
            bot.reply_to(
                message,
                f"ℹ️ Користувач з ID <code>{user_id}</code> не перебуває у списку забанених.",
                parse_mode='HTML',
            )
    else:
        bot.reply_to(
            message,
            "⚠️ Дайте відповідь (Reply) на повідомлення або вкажіть ID: <code>/unban 123456789</code>",
            parse_mode='HTML',
        )


# === 4. ПРИЙОМ ПОВІДОМЛЕНЬ ВІД ЮЗЕРІВ (ТЕКСТ, АУДІО, ФАЙЛИ, ФОТО) ===
@bot.message_handler(
    content_types=[
        'text',
        'audio',
        'voice',
        'document',
        'photo',
        'video',
        'sticker',
    ],
    chat_types=['private'],
)
def forward_to_admins(message):
    user_id = message.from_user.id

    if user_id in banned_users:
        return

    try:
        user = message.from_user
        username_str = f"@{user.username}" if user.username else "Немає"

        user_header = (
            f"👤 <b>Від:</b> {user.first_name} {user.last_name or ''}\n"
            f"🔗 <b>Username:</b> {username_str}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"-------------------------------\n"
        )

        if message.content_type == 'text':
            full_text = user_header + "\n" + message.text
            bot.send_message(ADMIN_CHAT_ID, full_text, parse_mode='HTML')

        elif message.content_type in ['audio', 'voice', 'sticker']:
            header_msg = bot.send_message(
                ADMIN_CHAT_ID,
                user_header + "🎵 <i>Надіслано аудіо/голосове:</i>",
                parse_mode='HTML',
            )
            bot.copy_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_to_message_id=header_msg.message_id,
            )

        else:
            original_caption = message.caption or ""
            full_caption = user_header + "\n" + original_caption
            if len(full_caption) > 1000:
                full_caption = full_caption[:990] + "..."

            bot.copy_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=full_caption,
                parse_mode='HTML',
            )

        bot.reply_to(message, "✅ Повідомлення надіслано адмінам.")

    except Exception as e:
        print(f"Помилка пересилання: {e}")
        bot.reply_to(
            message, "❌ Сталася помилка при відправці. Спробуйте пізніше."
        )


# === 5. ВІДПОВІДЬ АДМІНА КОРИСТУВАЧУ (ЧЕРЕЗ REPLY) ===
@bot.message_handler(
    content_types=[
        'text',
        'audio',
        'voice',
        'document',
        'photo',
        'video',
        'sticker',
    ],
    chat_types=['group', 'supergroup'],
)
def reply_to_user(message):
    if message.chat.id != ADMIN_CHAT_ID or not message.reply_to_message:
        return

    if message.text and (
        message.text.startswith('/ban') or message.text.startswith('/unban')
    ):
        return

    user_id = extract_user_id(message)

    if user_id:
        try:
            bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            bot.reply_to(message, "✅ Відповідь доставлено.")
        except Exception as e:
            bot.reply_to(message, f"❌ Не вдалося відправити юзеру: {e}")


# === WEBHOOK ROUTES ===

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return 'OK', 200


@app.route('/')
def index():
    return 'Bot is alive', 200


if __name__ == '__main__':
    bot.remove_webhook()
    if RENDER_EXTERNAL_URL:
        bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/{TOKEN}")
        print(f"Webhook встановлено: {RENDER_EXTERNAL_URL}/{TOKEN}")
    else:
        print("УВАГА: RENDER_EXTERNAL_URL не задано, webhook не встановлено!")

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
