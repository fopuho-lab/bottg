import os
import json
import telebot
from telebot import types
from flask import Flask, request

# 1. Токен бота і ID чату адмінів — беремо з змінних оточення (Render Environment Variables)
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))

# Публічна адреса твого сервісу на Render, наприклад: https://my-bot.onrender.com
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# === ФАЙЛИ ЗБЕРІГАННЯ ДАНИХ ===
# УВАГА: на безкоштовному Render диск не постійний — дані переживають звичайні
# перезапуски/засинання, але скидаються при новому деплої (оновленні коду).
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
BANNED_FILE = os.path.join(DATA_DIR, 'banned_users.json')
REQUESTS_FILE = os.path.join(DATA_DIR, 'requests.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
COUNTER_FILE = os.path.join(DATA_DIR, 'counter.json')


def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Помилка збереження {path}: {e}")


# Забанені користувачі (set із ID)
banned_users = set(load_json(BANNED_FILE, []))

# Усі користувачі, які писали боту: {user_id: {"first_name":..,"username":..}}
users_db = load_json(USERS_FILE, {})

# Заявки: {request_id: {...}}
requests_db = load_json(REQUESTS_FILE, {})

# Лічильник заявок
_counter_data = load_json(COUNTER_FILE, {"value": 0})
request_counter = _counter_data.get("value", 0)


def save_banned():
    save_json(BANNED_FILE, list(banned_users))


def save_requests():
    save_json(REQUESTS_FILE, requests_db)


def save_users():
    save_json(USERS_FILE, users_db)


def save_counter():
    save_json(COUNTER_FILE, {"value": request_counter})


def next_request_id():
    global request_counter
    request_counter += 1
    save_counter()
    return str(request_counter)


STATUS_LABELS = {
    'new': '🆕 Нова',
    'progress': '⏳ В роботі',
    'done': '✅ Відповідано',
}


def build_keyboard(req_id):
    req = requests_db.get(req_id, {})
    status = req.get('status', 'new')
    assigned_name = req.get('assigned_name')

    markup = types.InlineKeyboardMarkup(row_width=3)
    status_buttons = []
    for key, label in STATUS_LABELS.items():
        text = f"• {label}" if key == status else label
        status_buttons.append(
            types.InlineKeyboardButton(text, callback_data=f"st:{req_id}:{key}")
        )
    markup.add(*status_buttons)

    take_text = f"🙋 Відповідальний: {assigned_name}" if assigned_name else "🙋 Взяти в роботу"
    markup.add(types.InlineKeyboardButton(take_text, callback_data=f"as:{req_id}"))
    return markup


def status_footer(req_id):
    req = requests_db.get(req_id, {})
    status = STATUS_LABELS.get(req.get('status', 'new'))
    assigned = req.get('assigned_name')
    footer = f"\n\n📌 Статус: {status}"
    if assigned:
        footer += f"\n🙋 Відповідальний: {assigned}"
    footer += f"\n🔖 Заявка #{req_id}"
    return footer


def refresh_message(req_id):
    """Перемальовує повідомлення заявки в чаті адмінів після зміни статусу."""
    req = requests_db.get(req_id)
    if not req:
        return
    chat_id = req['admin_chat_id']
    message_id = req['admin_message_id']
    kind = req['kind']  # 'text' | 'caption'

    try:
        if kind == 'text':
            new_text = req['base_content'] + status_footer(req_id)
            bot.edit_message_text(
                new_text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='HTML',
                reply_markup=build_keyboard(req_id),
            )
        else:  # caption (для фото/відео/документів/аудіо-хедера)
            new_caption = req['base_content'] + status_footer(req_id)
            if len(new_caption) > 1024:
                new_caption = new_caption[:1000] + "..."
            if req.get('is_caption'):
                bot.edit_message_caption(
                    caption=new_caption,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='HTML',
                    reply_markup=build_keyboard(req_id),
                )
            else:
                bot.edit_message_text(
                    new_caption,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='HTML',
                    reply_markup=build_keyboard(req_id),
                )
    except Exception as e:
        print(f"Помилка оновлення повідомлення заявки {req_id}: {e}")


# Допоміжна функція для пошуку User ID (для /ban, /unban, ручного reply)
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


def find_request_by_admin_message(message_id):
    for req_id, req in requests_db.items():
        if req.get('admin_message_id') == message_id:
            return req_id
    return None


def notify_user(user_id, text):
    """Надсилає користувачу службове повідомлення про статус заявки.
    Без імені адміна, просто факт зміни статусу."""
    try:
        bot.send_message(user_id, text, parse_mode='HTML')
    except Exception as e:
        print(f"Не вдалося сповістити користувача {user_id}: {e}")


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
        save_banned()
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
            banned_users.discard(user_id)
            save_banned()
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


# === 4. КОМАНДА /banlist ===
@bot.message_handler(commands=['banlist'], chat_types=['group', 'supergroup'])
def banlist(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not banned_users:
        bot.reply_to(message, "✅ Список забанених порожній.")
        return
    text = "🚫 <b>Забанені користувачі:</b>\n" + "\n".join(
        f"— <code>{uid}</code>" for uid in banned_users
    )
    bot.reply_to(message, text, parse_mode='HTML')


# === 5. КОМАНДА /broadcast У ЧАТІ АДМІНІВ ===
@bot.message_handler(commands=['broadcast'], chat_types=['group', 'supergroup'])
def broadcast(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return

    # Текст можна передати або аргументом команди, або через Reply на повідомлення
    text_to_send = None
    if message.reply_to_message and message.reply_to_message.text:
        text_to_send = message.reply_to_message.text
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            text_to_send = parts[1]

    if not text_to_send:
        bot.reply_to(
            message,
            "⚠️ Вкажи текст: <code>/broadcast Текст розсилки</code>\n"
            "Або зроби Reply командою /broadcast на повідомлення, яке треба розіслати.",
            parse_mode='HTML',
        )
        return

    recipients = [
        int(uid) for uid in users_db.keys() if int(uid) not in banned_users
    ]

    status_msg = bot.reply_to(
        message, f"⏳ Розсилка запущена... Отримувачів: {len(recipients)}"
    )

    sent = 0
    failed = 0
    for uid in recipients:
        try:
            bot.send_message(uid, text_to_send, parse_mode='HTML')
            sent += 1
        except Exception:
            failed += 1

    bot.edit_message_text(
        f"✅ Розсилку завершено.\nНадіслано: {sent}\nНе вдалося: {failed}",
        chat_id=status_msg.chat.id,
        message_id=status_msg.message_id,
    )


# === 6. ПРИЙОМ ПОВІДОМЛЕНЬ ВІД ЮЗЕРІВ (ТЕКСТ, АУДІО, ФАЙЛИ, ФОТО) ===
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

    # Ігноруємо команди тут — вони обробляються окремими хендлерами
    if message.text and message.text.startswith('/'):
        return

    user = message.from_user

    # Запам'ятовуємо користувача для майбутніх розсилок
    users_db[str(user.id)] = {
        "first_name": user.first_name,
        "username": user.username,
    }
    save_users()

    try:
        username_str = f"@{user.username}" if user.username else "Немає"

        user_header = (
            f"👤 <b>Від:</b> {user.first_name} {user.last_name or ''}\n"
            f"🔗 <b>Username:</b> {username_str}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"-------------------------------\n"
        )

        req_id = next_request_id()

        if message.content_type == 'text':
            base_content = user_header + "\n" + message.text
            sent_msg = bot.send_message(
                ADMIN_CHAT_ID,
                base_content + status_footer(req_id),
                parse_mode='HTML',
            )
            requests_db[req_id] = {
                "user_id": user_id,
                "status": "new",
                "assigned_name": None,
                "admin_chat_id": sent_msg.chat.id,
                "admin_message_id": sent_msg.message_id,
                "kind": "text",
                "is_caption": False,
                "base_content": base_content,
            }
            bot.edit_message_reply_markup(
                chat_id=sent_msg.chat.id,
                message_id=sent_msg.message_id,
                reply_markup=build_keyboard(req_id),
            )

        elif message.content_type in ['audio', 'voice', 'sticker']:
            base_content = user_header + "🎵 <i>Надіслано аудіо/голосове:</i>"
            header_msg = bot.send_message(
                ADMIN_CHAT_ID,
                base_content + status_footer(req_id),
                parse_mode='HTML',
            )
            bot.copy_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_to_message_id=header_msg.message_id,
            )
            requests_db[req_id] = {
                "user_id": user_id,
                "status": "new",
                "assigned_name": None,
                "admin_chat_id": header_msg.chat.id,
                "admin_message_id": header_msg.message_id,
                "kind": "caption",
                "is_caption": False,
                "base_content": base_content,
            }
            bot.edit_message_reply_markup(
                chat_id=header_msg.chat.id,
                message_id=header_msg.message_id,
                reply_markup=build_keyboard(req_id),
            )

        else:
            original_caption = message.caption or ""
            base_content = user_header + "\n" + original_caption

            sent_msg = bot.copy_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=(base_content + status_footer(req_id))[:1024],
                parse_mode='HTML',
            )
            requests_db[req_id] = {
                "user_id": user_id,
                "status": "new",
                "assigned_name": None,
                "admin_chat_id": ADMIN_CHAT_ID,
                "admin_message_id": sent_msg.message_id,
                "kind": "caption",
                "is_caption": True,
                "base_content": base_content,
            }
            bot.edit_message_reply_markup(
                chat_id=ADMIN_CHAT_ID,
                message_id=sent_msg.message_id,
                reply_markup=build_keyboard(req_id),
            )

        save_requests()

    except Exception as e:
        print(f"Помилка пересилання: {e}")
        bot.reply_to(
            message, "❌ Сталася помилка при відправці. Спробуйте пізніше."
        )


# === 7. ОБРОБКА НАТИСКАНЬ НА КНОПКИ СТАТУСУ/ВІДПОВІДАЛЬНОГО ===
@bot.callback_query_handler(func=lambda call: call.data.startswith(('st:', 'as:')))
def handle_callback(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id)
        return

    parts = call.data.split(':')
    action = parts[0]
    req_id = parts[1]

    if req_id not in requests_db:
        bot.answer_callback_query(call.id, "Заявку не знайдено (можливо, дані скинулись при деплої).")
        return

    admin = call.from_user
    admin_name = f"@{admin.username}" if admin.username else admin.first_name

    if action == 'st':
        new_status = parts[2]
        was_done = requests_db[req_id]['status'] == 'done'
        requests_db[req_id]['status'] = new_status
        bot.answer_callback_query(call.id, f"Статус: {STATUS_LABELS[new_status]}")
        if new_status == 'done' and not was_done:
            notify_user(
                requests_db[req_id]['user_id'],
                "✅ Ваше звернення розглянуто та закрито. Дякуємо!",
            )
    elif action == 'as':
        was_assigned = requests_db[req_id]['assigned_name'] is not None
        requests_db[req_id]['assigned_name'] = admin_name
        if requests_db[req_id]['status'] == 'new':
            requests_db[req_id]['status'] = 'progress'
        bot.answer_callback_query(call.id, f"Ти відповідальний за заявку #{req_id}")
        if not was_assigned:
            notify_user(
                requests_db[req_id]['user_id'],
                "🔔 Ваше звернення взято в роботу. Незабаром отримаєте відповідь.",
            )

    save_requests()
    refresh_message(req_id)


# === 8. ВІДПОВІДЬ АДМІНА КОРИСТУВАЧУ (ЧЕРЕЗ REPLY) ===
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

    if message.text and message.text.startswith('/'):
        return

    user_id = extract_user_id(message)

    if user_id:
        try:
            bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            # Автоматично позначаємо заявку як "Відповідано"
            req_id = find_request_by_admin_message(message.reply_to_message.message_id)
            if req_id:
                admin = message.from_user
                admin_name = f"@{admin.username}" if admin.username else admin.first_name
                was_done = requests_db[req_id]['status'] == 'done'
                requests_db[req_id]['status'] = 'done'
                if not requests_db[req_id].get('assigned_name'):
                    requests_db[req_id]['assigned_name'] = admin_name
                save_requests()
                refresh_message(req_id)
                if not was_done:
                    notify_user(
                        user_id,
                        "✅ Ваше звернення розглянуто та закрито. Дякуємо!",
                    )

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
