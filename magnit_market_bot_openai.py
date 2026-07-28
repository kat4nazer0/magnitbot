"""
Telegram-бот для продавцов Магнит Маркет 3P — OpenAI GPT
Установка: pip3 install openai python-telegram-bot
Рядом со скриптом должен лежать файл faq.txt (база знаний)

Система обратной связи:
- под каждым ответом бота появляются кнопки оценки от 1 до 5 звёзд
- оценки 4-5: бот благодарит, записывает оценку в таблицу
- оценки 1-2-3: бот предлагает написать комментарий что не так,
  комментарий и оценка записываются в Google Sheets
"""

import os
import re
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
ВАЖНО: Никогда не используй markdown-форматирование: никаких **жирных** слов, *курсива*, заголовков ## и других спецсимволов — Telegram не рендерит markdown и пользователь увидит звёздочки как есть. Пиши обычным текстом.
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
ВАЖНОЕ ОБНОВЛЕНИЕ — FBO
────────────────────────────────────────────────────────
С 20 июля 2026 года приём новых поставок на склад FBO приостановлен.
Создать накладную технически можно, но выбрать таймслот для новых
поставок нельзя. Поставки созданные до 20 июля можно отгрузить
в течение 7 календарных дней.
Товары уже размещённые на складе FBO продолжают продаваться.
Площадка переходит на модель FBS, в перспективе — DBS.

Если продавец спрашивает про FBO или отгрузку на склад — ВСЕГДА
сообщай об этом ограничении и рекомендуй работать по FBS.
Следить за обновлениями: https://t.me/mmpartnersnews


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
    """Записывает строку фидбэка в Google Sheets в отдельном потоке
    чтобы не блокировать основной async-поток бота."""
    def _write():
        try:
            sheet = get_sheet()
            sheet.append_row([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(user_id), username, question, bot_answer, verdict, correction
            ])
            logger.info(f"✅ Фидбэк записан в таблицу: {verdict}")
        except Exception as e:
            logger.error(f"Ошибка записи в Google Sheets: {e}")
    threading.Thread(target=_write, daemon=True).start()


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


def needs_feedback(user_message: str, answer: str) -> bool:
    """Определяет нужна ли кнопка оценки после этого ответа.
    Оценка нужна только когда бот отвечает на содержательный вопрос по теме.
    Не нужна для: приветствий, вопросов о возможностях бота, коротких
    уточнений, сообщений не по теме, технических ошибок."""

    # Не показываем если это техническая ошибка
    if "Техническая ошибка" in answer:
        return False

    # Короткие сообщения пользователя — скорее всего не вопрос по теме
    if len(user_message.strip()) < 10:
        return False

    # Служебные паттерны — вопросы о боте, приветствия, благодарности
    service_patterns = [
        "что ты умеешь", "что умеешь", "что можешь", "чем можешь помочь",
        "привет", "здравствуй", "добрый", "как дела", "кто ты", "что ты",
        "помоги мне", "расскажи о себе", "что такое этот бот",
        "спасибо", "благодарю", "понял", "окей", "ок", "хорошо",
        "ясно", "понятно", "отлично", "супер", "класс", "👍", "👌",
    ]
    msg_lower = user_message.lower().strip()
    if any(p in msg_lower for p in service_patterns):
        return False

    # Если бот ответил что не знает ответа — тоже не просим оценку
    no_answer_patterns = [
        "нет точного ответа", "не могу помочь с этим вопросом",
        "специализируюсь только на вопросах", "не связан с работой"
    ]
    if any(p in answer.lower() for p in no_answer_patterns):
        return False

    return True


def get_response(user_id: int, user_message: str) -> tuple[str, bool]:
    """Возвращает (ответ бота, нужна_ли_оценка)."""
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

        # Разворачиваем markdown-ссылки [текст](url) → url
        answer = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'\2', answer)
        # Убираем markdown-жирный **текст** и *курсив*
        answer = re.sub(r'\*\*([^*]+)\*\*', r'\1', answer)
        answer = re.sub(r'\*([^*]+)\*', r'\1', answer)

        histories[user_id].append({"role": "assistant", "content": answer})
        return answer, needs_feedback(user_message, answer)

    except Exception as e:
        logger.error(f"Ошибка OpenAI для user {user_id}: {e}")
        return f"Техническая ошибка: {str(e)}\n\nОбратитесь в поддержку: @MagnitMarketBusiness_Bot", False


def feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 Помогло", callback_data="fb_5"),
        InlineKeyboardButton("😐 Частично", callback_data="fb_3"),
        InlineKeyboardButton("👎 Не помогло", callback_data="fb_1"),
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
        "Задайте любой вопрос! После ответа вы сможете оценить "
        "его по шкале от 1 до 5 — это помогает делать бота лучше.\n\n"
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

    # Если пользователь в режиме ввода комментария к низкой оценке
    if user.id in awaiting_correction:
        msg_id = awaiting_correction.pop(user.id)
        record = pending_feedback.get(msg_id)
        if record:
            save_feedback(
                user_id=user.id,
                username=user.username or user.first_name,
                question=record["question"],
                bot_answer=record["answer"],
                verdict=record["rating"],
                correction=user_message
            )
            logger.info(f"Получен комментарий от {user.id}: {user_message[:80]}")
        await update.message.reply_text(
            "Спасибо за обратную связь! Передам команде для улучшения бота. 🙏"
        )
        return

    logger.info(f"[{user.id}] {user.first_name}: {user_message[:80]}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    answer, show_feedback = get_response(user.id, user_message)

    if show_feedback:
        sent_message = await update.message.reply_text(
            answer + "\n\nОцените ответ:",
            reply_markup=feedback_keyboard()
        )
        pending_feedback[sent_message.message_id] = {
            "user_id": user.id,
            "username": user.username or user.first_name,
            "question": user_message,
            "answer": answer,
            "rating": None,
        }
    else:
        # Служебный ответ (приветствие, не по теме и т.д.) — без кнопок оценки
        await update.message.reply_text(answer)


# ─────────────────────────────────────────────────────────
# ОБРАБОТКА НАЖАТИЙ НА КНОПКИ ОЦЕНКИ
# ─────────────────────────────────────────────────────────

async def handle_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    msg_id = query.message.message_id
    record = pending_feedback.get(msg_id)

    if not record:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # Определяем оценку из callback_data (fb_1, fb_3, fb_5)
    rating = int(query.data.split("_")[1])

    # Метка для записи в таблицу
    rating_label = {5: "👍 Помогло", 3: "😐 Частично", 1: "👎 Не помогло"}.get(rating, str(rating))
    record["rating"] = rating_label

    await query.edit_message_reply_markup(reply_markup=None)

    if rating == 5:
        # Помогло — сразу благодарим и сохраняем
        save_feedback(
            user_id=record["user_id"],
            username=record["username"],
            question=record["question"],
            bot_answer=record["answer"],
            verdict=rating_label,
            correction=""
        )
        await query.message.reply_text("Рад помочь! 👍")
        pending_feedback.pop(msg_id, None)

    else:
        # Частично или Не помогло — просим комментарий
        if rating == 3:
            prompt = "Что именно не хватило в ответе? Напишите — постараемся улучшить."
        else:
            prompt = "Жаль, что не помогло. Напишите что было не так — это поможет нам стать лучше."
        await query.message.reply_text(prompt)
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

    def do_HEAD(self):
        # UptimeRobot иногда шлёт HEAD вместо GET из европейских узлов —
        # без этого метода сервер возвращает 501 Not Implemented
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

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
