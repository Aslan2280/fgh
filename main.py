import asyncio
import logging
from datetime import datetime, timedelta, timezone
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "ВАШ_ТОКЕН_ОТ_BOTFATHER"  # <-- ЗАМЕНИТЕ
# ================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# HTTP сессия для запросов к API
http_session = None

# Храним connection_id для каждого пользователя
user_connections = {}  # user_id: connection_id


def get_msk_time():
    utc_now = datetime.now(timezone.utc)
    return utc_now + timedelta(hours=3)


def is_school_time():
    msk_time = get_msk_time()
    hours = msk_time.hour
    minutes = msk_time.minute
    return (7 <= hours < 13) or (hours == 13 and minutes <= 30)


async def send_business_message(chat_id: int, text: str, connection_id: str):
    """
    Отправка сообщения через бизнес-подключение с использованием aiohttp
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'business_connection_id': connection_id
    }
    
    try:
        async with http_session.post(url, json=payload) as response:
            result = await response.json()
            if result.get('ok'):
                logger.info(f"✅ Сообщение отправлено в чат {chat_id}")
                return result
            else:
                logger.error(f"❌ Ошибка API: {result}")
                return None
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return None


# ========== BUSINESS CONNECTION (подключение) ==========

@dp.business_connection()
async def handle_business_connection(connection: types.BusinessConnection):
    """
    Обработчик подключения Chat Automation
    """
    logger.info(f"✅ Chat Automation подключен!")
    logger.info(f"   Connection ID: {connection.id}")
    logger.info(f"   User ID: {connection.user.id}")
    logger.info(f"   Username: @{connection.user.username}")
    
    # Сохраняем connection_id
    user_connections[connection.user.id] = connection.id


# ========== BUSINESS MESSAGE (сообщения из чатов) ==========

@dp.business_message()
async def handle_business_message(message: Message):
    """
    Обработчик бизнес-сообщений
    """
    try:
        logger.info(f"📩 Business message от {message.from_user.id}")
        logger.info(f"   Текст: {message.text}")

        # Проверяем, что есть business_connection_id
        conn_id = message.business_connection_id
        if not conn_id:
            logger.warning("❌ Нет business_connection_id")
            return

        # Проверяем, что это текстовое сообщение
        if not message.text:
            await send_business_message(
                chat_id=message.chat.id,
                text="Я понимаю только текст 📝",
                connection_id=conn_id
            )
            return

        text = message.text.strip()
        chat_id = message.chat.id

        # ===== ОБРАБОТКА КОМАНД =====
        if text.lower() in ['/start', '.start']:
            response = (
                "👋 Привет! Я твой личный автоответчик.\n\n"
                "📌 Что я умею:\n"
                "• Отвечаю в зависимости от времени\n"
                "• Показываю время (/time или .time)\n"
                "• Показываю информацию (/info или .info)\n"
                "• Проверяю задержку (/ping или .ping)\n\n"
                "💡 Просто напиши мне что-нибудь!"
            )
            await send_business_message(chat_id, response, conn_id)
            return

        elif text.lower() in ['/help', '.help']:
            response = (
                "📚 Доступные команды:\n\n"
                "🕐 /time или .time - Текущее время\n"
                "👤 /info или .info - Информация\n"
                "🏓 /ping или .ping - Задержка\n"
                "❓ /help или .help - Справка\n\n"
                "💡 Просто напиши что-нибудь!"
            )
            await send_business_message(chat_id, response, conn_id)
            return

        elif text.lower() in ['/time', '.time']:
            msk_time = get_msk_time()
            response = f"🕐 {msk_time.strftime('%H:%M:%S')} МСК"
            await send_business_message(chat_id, response, conn_id)
            return

        elif text.lower() in ['/info', '.info']:
            user = message.from_user
            response = f"👤 ID: {user.id}\nИмя: {user.first_name}"
            if user.username:
                response += f"\n@ {user.username}"
            await send_business_message(chat_id, response, conn_id)
            return

        elif text.lower() in ['/ping', '.ping']:
            # Отправляем начальное сообщение
            sent = await send_business_message(chat_id, "🏓 Понг...", conn_id)
            
            if sent and sent.get('ok'):
                message_id = sent['result']['message_id']
                end_time = datetime.now(timezone.utc)
                latency = (end_time - message.date).total_seconds() * 1000
                
                # Редактируем сообщение
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
                payload = {
                    'chat_id': chat_id,
                    'message_id': message_id,
                    'text': f"🏓 Понг!\n⏱️ {latency:.0f}мс",
                    'business_connection_id': conn_id
                }
                try:
                    async with http_session.post(url, json=payload) as response:
                        await response.json()
                except Exception as e:
                    logger.error(f"❌ Ошибка редактирования: {e}")
            return

        # ===== ОБЫЧНЫЙ ТЕКСТ =====
        msk_time = get_msk_time()
        time_string = msk_time.strftime('%H:%M')

        if is_school_time():
            response = f"Я щас занят, я в школе.\nМоё время: {time_string} МСК"
        else:
            response = f"Привет! Я сейчас свободен ({time_string} МСК). Напиши своё сообщение, и я отвечу, когда смогу."

        await send_business_message(chat_id, response, conn_id)

    except Exception as e:
        logger.error(f"❌ Ошибка в business_message: {e}")


# ========== ОБЫЧНЫЕ ОБРАБОТЧИКИ (для тестов) ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обычный /start для тестирования"""
    await message.answer(
        "👋 Привет!\n\n"
        "📌 Чтобы использовать меня как автоответчика:\n"
        "1. Включи Business Mode у @BotFather\n"
        "2. Зайди в Настройки → Chat Automation\n"
        "3. Подключи меня\n\n"
        "После этого я буду отвечать на сообщения за тебя! 🤖"
    )


@dp.message()
async def handle_regular_message(message: Message):
    """Обычные сообщения (для тестов в ЛС с ботом)"""
    if message.text and message.text.startswith('/'):
        return
    await message.answer(f"Эхо: {message.text}")


# ========== ЗАПУСК ==========

async def main():
    global http_session
    
    logger.info("🚀 Бот запускается...")
    
    # Создаем HTTP сессию
    http_session = aiohttp.ClientSession()
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        # Закрываем сессию при завершении
        await http_session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
