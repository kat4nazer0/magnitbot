"""
Telegram-бот для продавцов Магнит Маркет 3P — OpenAI GPT
Установка: pip3 install openai python-telegram-bot
Рядом со скриптом должен лежать файл faq.txt (база знаний)

Фича обратной связи:
- под каждым ответом бота появляются кнопки "✅ Правда" / "❌ Ложь"
- "Правда" — бот благодарит
- "Ложь" — бот просит написать правильный ответ, и сохраняет
  пару (вопрос, неверный ответ бота, правильный ответ менеджера,
  кто оценил) в файл feedback_log.csv рядом со скриптом
"""

import os
import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from telegram import (
    Update, BotCommand,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ─────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────────────────
# Локально: впишите токены прямо в кавычки ниже.
# На Railway: оставьте кавычки пустыми — токены подставятся
# автоматически из переменных окружения (Variables).

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "ВАШ_ТОКЕН_ОТ_BOTFATHER"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or "ВАШ_КЛЮЧ_ОТ_OPENAI"

GPT_MODEL       = "gpt-4o-mini"
MAX_HISTORY     = 10

# ID вашей Google таблицы (из ссылки)
SPREADSHEET_ID  = "12t38O0ul0hWgg2p1XJzsgMatj5oP6kVQyWm5sIw2htU"

# Ключ сервисного аккаунта — читается из переменной окружения на Render
# или из файла локально
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")

# ─────────────────────────────────────────────────────────
# СИСТЕМНЫЙ ПРОМПТ
# ─────────────────────────────────────────────────────────

SYSTEM_PROMPT_BASE = """СИСТЕМНЫЙ ПРОМПТ — AI-АССИСТЕНТ МАГНИТ МАРКЕТ 3P

Ты — AI-помощник команды массового привлечения маркетплейса Магнит Маркет.
Твоя задача — помогать продавцам (селлерам) работать с платформой: отвечать
на вопросы о регистрации, загрузке документов, создании магазина, управлении
карточками товаров, финансах и логистике.

────────────────────────────────────────────────────────
РОЛЬ И ПОВЕДЕНИЕ
────────────────────────────────────────────────────────
Ты вежливый, дружелюбный и профессиональный помощник.
Общаешься на русском языке.
Отвечаешь чётко и по делу — без воды и лишних слов.
Если вопрос понятен — сразу даёшь ответ, не переспрашивая лишний раз.
Если вопрос неоднозначный — уточняешь одним конкретным вопросом.
НЕ придумываешь информацию. Если ответа нет в базе знаний — честно говоришь
об этом и предлагаешь обратиться в поддержку.

────────────────────────────────────────────────────────
ФОРМАТ ОТВЕТОВ
────────────────────────────────────────────────────────
Для пошаговых инструкций используй нумерованный список:
1. Первый шаг
2. Второй шаг
3. Третий шаг
Для коротких ответов — один-два абзаца без списков.
Если есть ссылка — обязательно укажи её в конце ответа вот так:
🔗 Подробнее: Название раздела — https://example.com

ВАЖНО: Ссылки пиши только в виде голого URL (https://...), без markdown-оформления вида [текст](url) — Telegram не поддерживает такой формат и покупатель увидит нечитаемый текст вместо ссылки.
Заканчивай ответ фразой поддержки, например:
«Если остались вопросы — смело спрашивайте!»

────────────────────────────────────────────────────────
ТЕМАТИКА
────────────────────────────────────────────────────────
Отвечай ТОЛЬКО на вопросы, связанные с:
• Регистрацией и верификацией продавца
• Загрузкой и управлением документами
• Созданием магазина и карточек товаров
• Массовой загрузкой товаров через Excel
• Финансами: выплаты, комиссии, акты
• Логистикой: FBS, FBO, упаковка, возвраты
• Рекламой и продвижением товаров на площадке
• Техническими вопросами по личному кабинету

Если вопрос не связан с работой на Магнит Маркет — вежливо сообщи,
что ты специализируешься только на вопросах платформы.

────────────────────────────────────────────────────────
КОМИССИИ
────────────────────────────────────────────────────────
Если у тебя спрашивают размер комиссии, посмотри максимально релевантную
запись в базе знаний и укажи комиссию отдельно для FBO и FBS. Дополнительно
предложи скачать и посмотреть файл целиком с полной таблицей по всем
подкатегориям.

────────────────────────────────────────────────────────
ИНСТРУКЦИЯ ПРОДАВЦА
────────────────────────────────────────────────────────
Как дополнительный источник можешь использовать Инструкцию продавца:
https://seller-manual.mm.ru/
Если берёшь ответ оттуда — указывай, какой раздел/пункт инструкции.

────────────────────────────────────────────────────────
ПОДДЕРЖКА
────────────────────────────────────────────────────────
Если не знаешь точного ответа, предложи обратиться в поддержку
в Личном кабинете продавца или в Telegram-бот:
https://t.me/MagnitMarketBusiness_Bot

────────────────────────────────────────────────────────
ЭСКАЛАЦИЯ
────────────────────────────────────────────────────────
Если ответа нет в базе знаний или ситуация нестандартная, отвечай так:
«К сожалению, у меня нет точного ответа на этот вопрос.
Рекомендую обратиться напрямую в поддержку Магнит Маркет:
🔗 https://t.me/MagnitMarketBusiness_Bot
или написать вашему менеджеру по работе с продавцами.»
"""

# ─────────────────────────────────────────────────────────
# ЗАГРУЗКА FAQ
# ─────────────────────────────────────────────────────────

def load_faq(path="faq.txt", chunk_size=8000):
    try:
        text = open(path, encoding="utf-8").read()
        blocks = text.split("---")
        chunks, current = [], ""
        for block in blocks:
            if len(current) + len(block) < chunk_size:
                current += "---" + block
            else:
                if current:
                    chunks.append(current.strip())
                current = "---" + block
        if current:
            chunks.append(current.strip())
        print(f"✅ FAQ загружен: {len(text)} символов → {len(chunks)} частей")
        return chunks
    except FileNotFoundError:
        print("⚠️  faq.txt не найден! Положите файл рядом со скриптом.")
        return ["База знаний не загружена."]

FAQ_CHUNKS = load_faq()


def find_relevant_chunks(question: str, top_n: int = 2) -> str:
    question_lower = question.lower()
    words = [w for w in question_lower.split() if len(w) > 3]
    scored = []
    for chunk in FAQ_CHUNKS:
        chunk_lower = chunk.lower()
        score = sum(1 for w in words if w in chunk_lower)
        scored.append((score, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    top_chunks = [c for s, c in scored[:top_n] if s > 0]
    if not top_chunks:
        top_chunks = [scored[0][1]]
    return "\n\n".join(top_chunks)


# ─────────────────────────────────────────────────────────
# ФАЙЛ ОБРАТНОЙ СВЯЗИ
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# GOOGLE SHEETS — инициализация и запись фидбэка
# ─────────────────────────────────────────────────────────

def get_sheet():
    """Подключается к Google Sheets и возвращает первый лист."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    if GOOGLE_CREDS_JSON:
        # На Render: читаем из переменной окружения
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
    else:
        # Локально: читаем из файла рядом со скриптом
        with open("google_creds.json", encoding="utf-8") as f:
            creds_dict = json.load(f)

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1


def init_sheet():
    """Добавляет шапку таблицы если она ещё пустая."""
    try:
        sheet = get_sheet()
        if not sheet.row_values(1):
            sheet.append_row([
                "дата_время", "пользователь_id", "имя_пользователя",
                "вопрос", "ответ_бота", "оценка", "правильный_ответ"
            ])
            logger.info("✅ Google Sheets: шапка добавлена")
        else:
            logger.info("✅ Google Sheets: подключение успешно")
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")


def save_feedback(user_id, username, question, bot_answer, verdict, correction=""):
    """Записывает строку фидбэка в Google Sheets."""
    try:
        sheet = get_sheet()
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            str(user_id), username, question, bot_answer, verdict, correction
        ])
        logger.info(f"✅ Фидбэк записан в таблицу: {verdict}")
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {e}")


# ─────────────────────────────────────────────────────────
# ИНИЦИАЛИЗАЦИЯ
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY)

histories: dict[int, list] = {}

# Временное хранилище последнего вопроса/ответа для каждого сообщения бота
# {message_id: {"user_id":, "username":, "question":, "answer":}}
pending_feedback: dict[int, dict] = {}

# Кто сейчас в режиме "пишет правильный ответ": {user_id: message_id}
awaiting_correction: dict[int, int] = {}


def get_response(user_id: int, user_message: str) -> str:
    if user_id not in histories:
        histories[user_id] = []

    relevant_faq = find_relevant_chunks(user_message)
    system_prompt = f"{SYSTEM_PROMPT_BASE}\n\n────────────────────────────────────────────────────────\nБАЗА ЗНАНИЙ (релевантные разделы)\n────────────────────────────────────────────────────────\n{relevant_faq}"

    histories[user_id].append({"role": "user", "content": user_message})
    history = histories[user_id][-MAX_HISTORY:]

    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                *history
            ],
            max_tokens=1024,
            temperature=0.3
        )
        answer = response.choices[0].message.content

        # Разворачиваем markdown-ссылки вида [текст](url) → url
        # Telegram их не рендерит, показывает как есть — некрасиво
        import re
        answer = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'\2', answer)

        histories[user_id].append({"role": "assistant", "content": answer})
        return answer

    except Exception as e:
        logger.error(f"Ошибка OpenAI для user {user_id}: {e}")
        return f"Техническая ошибка: {str(e)}\n\nОбратитесь в поддержку: @MagnitMarketBusiness_Bot"


def feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Правда", callback_data="fb_true"),
        InlineKeyboardButton("❌ Ложь", callback_data="fb_false"),
    ]])


# ─────────────────────────────────────────────────────────
# ОБРАБОТЧИКИ КОМАНД
# ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    histories.pop(user.id, None)
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — AI-помощник Магнит Маркет для продавцов.\n"
        "Помогу с регистрацией, документами, карточками товаров, "
        "накладными, складами и комиссиями.\n\n"
        "Под каждым моим ответом есть кнопки ✅/❌ — оцените, "
        "пожалуйста, насколько ответ был верным, это помогает "
        "сделать бота лучше!\n\n"
        "/help — список тем\n"
        "/reset — очистить историю"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 Темы, по которым я помогу:\n\n"
        "📝 Регистрация и активация — документы, верификация, ЛК\n"
        "🏪 Создание магазина — название, оформление, настройки\n"
        "🗂 Создание карточки товара — поля, фото, шаблон Excel\n"
        "📦 Накладные FBO — создание, правила, упаковка, УПД\n"
        "🏭 Склад FBS — создание, остатки, заборная логистика\n"
        "💰 Финансы и комиссии — выплаты, ставки по категориям\n"
        "📢 Условия работы — договор, ограничения, тарифы\n\n"
        "Напишите вопрос обычным текстом!"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    histories.pop(update.effective_user.id, None)
    await update.message.reply_text("История очищена. Начните с нового вопроса! ✅")


# ─────────────────────────────────────────────────────────
# ОБРАБОТКА ОБЫЧНЫХ СООБЩЕНИЙ
# ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_message = update.message.text

    # Если пользователь сейчас должен написать правильный ответ —
    # это сообщение не вопрос боту, а коррекция предыдущего ответа
    if user.id in awaiting_correction:
        msg_id = awaiting_correction.pop(user.id)
        record = pending_feedback.get(msg_id)
        if record:
            save_feedback(
                user_id=user.id,
                username=user.username or user.first_name,
                question=record["question"],
                bot_answer=record["answer"],
                verdict="Ложь",
                correction=user_message
            )
            logger.info(f"Получена коррекция от {user.id}: {user_message[:80]}")
        await update.message.reply_text(
            "Спасибо, записал правильный вариант! Передам команде "
            "для обновления базы знаний. 🙏"
        )
        return

    logger.info(f"[{user.id}] {user.first_name}: {user_message[:80]}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    answer = get_response(user.id, user_message)
    sent_message = await update.message.reply_text(
        answer, reply_markup=feedback_keyboard()
    )

    # Запоминаем вопрос/ответ, привязанные к id отправленного сообщения
    pending_feedback[sent_message.message_id] = {
        "user_id": user.id,
        "username": user.username or user.first_name,
        "question": user_message,
        "answer": answer,
    }


# ─────────────────────────────────────────────────────────
# ОБРАБОТКА НАЖАТИЙ НА КНОПКИ
# ─────────────────────────────────────────────────────────

async def handle_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # убирает "часики" на кнопке

    msg_id = query.message.message_id
    record = pending_feedback.get(msg_id)

    if not record:
        # Сообщение слишком старое или бот перезапускался
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if query.data == "fb_true":
        save_feedback(
            user_id=record["user_id"],
            username=record["username"],
            question=record["question"],
            bot_answer=record["answer"],
            verdict="Правда"
        )
        # Убираем кнопки, показываем благодарность
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Спасибо за оценку! 🙌")
        pending_feedback.pop(msg_id, None)

    elif query.data == "fb_false":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "Спасибо, что заметили! Напишите, пожалуйста, как должен "
            "звучать правильный ответ на этот вопрос — я передам его "
            "команде для обновления базы знаний."
        )
        # Переводим пользователя в режим ожидания коррекции
        awaiting_correction[query.from_user.id] = msg_id


# ─────────────────────────────────────────────────────────
# КРОШЕЧНЫЙ ВЕБ-СЕРВЕР — нужен только для Render.com
# Render требует, чтобы Web Service слушал какой-то порт.
# Сам сервер ничего не делает, кроме ответа "ok" на пинг.
# На Railway/локально он не мешает — просто не используется Render'ом.
# ─────────────────────────────────────────────────────────

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/feedback":
            # Перенаправляем на Google таблицу с фидбэком
            url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Magnit Market bot is running")

    def log_message(self, format, *args):
        pass  # отключаем лишние логи от веб-сервера


def start_ping_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    logger.info(f"🌐 Ping-сервер запущен на порту {port}")
    server.serve_forever()


# ─────────────────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────────────────

def main() -> None:
    init_sheet()

    # Запускаем веб-сервер в отдельном потоке, чтобы Render видел
    # открытый порт и не считал сервис мёртвым
    threading.Thread(target=start_ping_server, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("reset",  reset_command))
    app.add_handler(CallbackQueryHandler(handle_feedback_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def set_commands(app):
        await app.bot.set_my_commands([
            BotCommand("start",  "Начать заново"),
            BotCommand("help",   "Список тем"),
            BotCommand("reset",  "Очистить историю"),
        ])
    app.post_init = set_commands

    logger.info("✅ Бот запущен! Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
