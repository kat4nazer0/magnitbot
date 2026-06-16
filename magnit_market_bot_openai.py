"""
Telegram-бот для продавцов Магнит Маркет 3P — OpenAI GPT
Установка: pip3 install openai python-telegram-bot
Рядом со скриптом должен лежать файл faq.txt (база знаний)
"""

import os
import logging
from openai import OpenAI
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ─────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────────────────
# Локально: впишите токены прямо в кавычки ниже.
# На Railway: оставьте кавычки пустыми — токены подставятся
# автоматически из переменных окружения (Variables).

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "ВАШ_ТОКЕН"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or "ВАШ_ТОКЕН"

GPT_MODEL   = "gpt-4o-mini"   # хорошее качество, низкая цена
MAX_HISTORY = 10              # сколько сообщений помнит бот в сессии
CHUNK_SIZE  = 9000             # размер одного блока базы знаний в символах

# ─────────────────────────────────────────────────────────
# БАЗОВЫЙ СИСТЕМНЫЙ ПРОМПТ (роль, поведение, формат, эскалация)
# ─────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """СИСТЕМНЫЙ ПРОМПТ — AI-АССИСТЕНТ МАГНИТ МАРКЕТ 3P

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
🔗 Подробнее: [название раздела] — [ссылка]
Заканчивай ответ фразой поддержки, например:
«Если остались вопросы — смело спрашивайте!»

────────────────────────────────────────────────────────
ТЕМАТИКА
────────────────────────────────────────────────────────
Отвечай ТОЛЬКО на вопросы, связанные с:
• Регистрацией и верификацией продавца
• Загрузкой и управлением документами (сертификаты, декларации и т.д.)
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
Если у тебя спрашивают размер комиссии — посмотри максимально релевантную
ставку в базе знаний и укажи её отдельно для FBO и для FBS. А также
предложи скачать и посмотреть файл целиком по ссылке, если она есть в базе.

────────────────────────────────────────────────────────
ИНСТРУКЦИЯ ПРОДАВЦА (доп. источник)
────────────────────────────────────────────────────────
Как источник можно также использовать Инструкцию продавца по ссылке:
https://seller-manual.mm.ru/
Если используешь ответ оттуда как источник — указывай, какой пункт инструкции.

────────────────────────────────────────────────────────
ПОДДЕРЖКА
────────────────────────────────────────────────────────
Если не знаешь как ответить, можно предложить обратиться в поддержку
в Личном кабинете продавца или в Telegram-бот:
https://t.me/MagnitMarketBusiness_Bot

────────────────────────────────────────────────────────
ЭСКАЛАЦИЯ
────────────────────────────────────────────────────────
Если ответа нет в базе знаний или ситуация нестандартная, отвечай так:
«К сожалению, у меня нет точного ответа на этот вопрос.
Рекомендую обратиться напрямую в поддержку Магнит Маркет:
🔗 seller.magnit.ru/support
или написать вашему менеджеру по работе с продавцами.»

────────────────────────────────────────────────────────
ПРИМЕР ХОРОШЕГО ОТВЕТА
────────────────────────────────────────────────────────
Вопрос продавца: «Как мне зарегистрироваться?»
Хороший ответ:
«Для регистрации на Магнит Маркет выполните следующие шаги:
1. Перейдите на seller.magnit.ru
2. Нажмите кнопку «Стать продавцом»
3. Заполните форму: укажите ИНН, email и номер телефона
4. Подтвердите email по ссылке из письма
5. Дождитесь проверки аккаунта — обычно это занимает 1–2 рабочих дня
🔗 Подробнее: Регистрация продавца — seller.magnit.ru/register
Если остались вопросы — смело спрашивайте!»
"""

# ─────────────────────────────────────────────────────────
# ЗАГРУЗКА БАЗЫ ЗНАНИЙ (faq.txt) И РАЗБИВКА НА ЧАНКИ
# ─────────────────────────────────────────────────────────

def load_faq(path="faq.txt", chunk_size=CHUNK_SIZE):
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
    """Находит top_n наиболее релевантных блока FAQ по словам из вопроса."""
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
        top_chunks = [FAQ_CHUNKS[0]]

    return "\n\n".join(top_chunks)


# ─────────────────────────────────────────────────────────
# ИНИЦИАЛИЗАЦИЯ
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY)

# История диалогов: {user_id: [{"role": ..., "content": ...}, ...]}
histories: dict[int, list] = {}


def get_response(user_id: int, user_message: str) -> str:
    if user_id not in histories:
        histories[user_id] = []

    relevant_faq = find_relevant_chunks(user_message, top_n=2)
    system_prompt = f"{BASE_SYSTEM_PROMPT}\n\nБАЗА ЗНАНИЙ (релевантные разделы):\n{relevant_faq}"

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

        histories[user_id].append({"role": "assistant", "content": answer})
        return answer

    except Exception as e:
        logger.error(f"Ошибка OpenAI для user {user_id}: {e}")
        return (
            f"Произошла техническая ошибка: {str(e)}\n\n"
            "Обратитесь в поддержку: @MagnitMarketBusiness_Bot"
        )


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
        "комиссиями, накладными и логистикой.\n\n"
        "Задайте любой вопрос! 🛍️\n\n"
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
        "💰 Комиссии и финансы — выплаты, ставки, документы\n"
        "🚚 Отправка и возврат товара со склада\n\n"
        "Напишите вопрос обычным текстом!"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    histories.pop(update.effective_user.id, None)
    await update.message.reply_text("История очищена. Начните с нового вопроса! ✅")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_message = update.message.text
    logger.info(f"[{user.id}] {user.first_name}: {user_message[:80]}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    response = get_response(user.id, user_message)
    await update.message.reply_text(response)


# ─────────────────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("reset",  reset_command))
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
