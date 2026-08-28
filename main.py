import asyncio
import json
import os
import logging
import re
from typing import Optional, Dict
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode

from telethon import TelegramClient, events
from telethon.tl.types import Message as TelethonMessage
from telethon.errors import SessionPasswordNeededError

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "YOUR_BOT_TOKEN"  # Замените на ваш токен
API_ID = 123456  # Ваш API ID
API_HASH = "your_api_hash"  # Ваш API HASH
ADMIN_ID = 6539341659  # ID администратора
TARGET_CHAT = "pepegamechat"  # Целевой чат

# Пути для хранения данных
DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
SESSIONS_DB = os.path.join(DATA_DIR, "sessions.json")

# Создаем папки если их нет
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========
class SessionDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.data = self._load()
    
    def _load(self) -> Dict:
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save(self):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def add_session(self, phone: str, session_name: str):
        """Добавляет сессию в БД"""
        self.data[phone] = {
            "session_name": session_name,
            "added_at": datetime.now().isoformat(),
            "status": "active"
        }
        self._save()
    
    def get_session(self, phone: str) -> Optional[str]:
        """Получает имя файла сессии по номеру телефона"""
        session_data = self.data.get(phone)
        return session_data.get("session_name") if session_data else None
    
    def get_all_sessions(self) -> Dict:
        """Возвращает все сессии"""
        return self.data
    
    def get_session_info(self, phone: str) -> Optional[Dict]:
        """Получает полную информацию о сессии"""
        return self.data.get(phone)
    
    def remove_session(self, phone: str):
        """Удаляет сессию"""
        if phone in self.data:
            # Удаляем файл сессии если существует
            session_name = self.data[phone].get("session_name")
            if session_name and os.path.exists(f"{session_name}.session"):
                try:
                    os.remove(f"{session_name}.session")
                except:
                    pass
            
            del self.data[phone]
            self._save()
    
    def update_status(self, phone: str, status: str):
        """Обновляет статус сессии"""
        if phone in self.data:
            self.data[phone]["status"] = status
            self._save()

# ========== МЕНЕДЖЕР ЮЗЕРБОТОВ (TELEGRAM) ==========
class UserBotManager:
    def __init__(self, api_id: int, api_hash: str, session_db, target_chat: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_db = session_db
        self.target_chat = target_chat
        self.clients = {}  # phone: client
        self.running_tasks = {}  # phone: task
        self.pending_auth = {}  # phone: phone_data
    
    def _get_session_path(self, phone: str) -> str:
        """Получает путь для файла сессии"""
        return os.path.join(SESSIONS_DIR, phone.replace('+', ''))
    
    async def request_code(self, phone: str) -> dict:
        """Запрашивает код подтверждения"""
        try:
            session_path = self._get_session_path(phone)
            
            # Создаем клиента
            client = TelegramClient(
                session_path,
                self.api_id,
                self.api_hash
            )
            
            await client.connect()
            
            # Отправляем запрос на код
            await client.send_code_request(phone)
            
            # Сохраняем клиента для ввода кода
            self.pending_auth[phone] = {
                "client": client,
                "phone": phone,
                "session_path": session_path
            }
            
            return {
                "status": "need_code",
                "message": "Введите код от Telegram",
                "phone": phone
            }
            
        except Exception as e:
            logger.error(f"Error requesting code: {e}")
            return {"status": "error", "message": str(e)}
    
    async def authorize_client(self, phone: str, code: str) -> dict:
        """Авторизует клиент с кодом"""
        try:
            # Получаем ожидающего клиента
            pending = self.pending_auth.get(phone)
            if not pending:
                return {"status": "error", "message": "Сессия не найдена. Используйте /add"}
            
            client = pending["client"]
            session_path = pending["session_path"]
            
            # Пытаемся войти с кодом
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                # Если нужен пароль двухфакторной аутентификации
                return {"status": "error", "message": "Требуется пароль двухфакторной аутентификации. Используйте /password"}
            
            # Сохраняем сессию
            self.session_db.add_session(phone, session_path)
            self.clients[phone] = client
            
            # Удаляем из ожидающих
            del self.pending_auth[phone]
            
            # Запускаем мониторинг чата
            await self.start_monitoring(phone)
            
            return {"status": "success", "message": "Авторизация успешна"}
            
        except Exception as e:
            logger.error(f"Error during authorization: {e}")
            return {"status": "error", "message": str(e)}
    
    async def start_monitoring(self, phone: str):
        """Запускает мониторинг чата для пользователя"""
        if phone not in self.clients:
            return
        
        client = self.clients[phone]
        
        try:
            # Проверяем, авторизован ли клиент
            if not await client.is_user_authorized():
                await client.start()
            
            # Получаем информацию о пользователе
            me = await client.get_me()
            username = me.username if me.username else f"user_{me.id}"
            
            # Обновляем статус
            self.session_db.update_status(phone, "active")
            
            # Запускаем задачу мониторинга
            task = asyncio.create_task(self._monitor_chat(client, username, phone))
            self.running_tasks[phone] = task
            
            logger.info(f"Started monitoring for {phone}")
            
        except Exception as e:
            logger.error(f"Error starting monitoring: {e}")
            self.session_db.update_status(phone, "error")
    
    async def _monitor_chat(self, client: TelegramClient, username: str, phone: str):
        """Мониторит чат и отвечает на упоминания"""
        try:
            # Получаем чат
            try:
                chat = await client.get_entity(self.target_chat)
            except:
                # Если не удается получить по username, пробуем по ссылке
                chat = await client.get_entity(f"https://t.me/{self.target_chat}")
            
            # Отправляем сообщение "б"
            await client.send_message(chat, "б")
            logger.info(f"Sent 'б' to {self.target_chat}")
            
            # Счетчик сообщений для защиты от спама
            message_count = 0
            last_message_time = datetime.now()
            
            # Регистрируем обработчик сообщений
            @client.on(events.NewMessage(chats=chat))
            async def handle_message(event):
                nonlocal message_count, last_message_time
                
                try:
                    message = event.message
                    if not message or not message.text:
                        return
                    
                    # Проверяем, упомянут ли пользователь
                    if f"@{username}" in message.text.lower():
                        # Защита от спама (не более 5 сообщений в минуту)
                        now = datetime.now()
                        if (now - last_message_time).seconds < 60:
                            message_count += 1
                            if message_count > 5:
                                logger.warning(f"Rate limit hit for {phone}")
                                return
                        else:
                            message_count = 1
                            last_message_time = now
                        
                        # Отвечаем на упоминание
                        await message.reply("Что хочешь?")
                        logger.info(f"Replied to mention in chat {self.target_chat}")
                        
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
            
            # Держим клиент активным
            while True:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in monitoring: {e}")
            self.session_db.update_status(phone, "error")
    
    async def stop_monitoring(self, phone: str):
        """Останавливает мониторинг для пользователя"""
        if phone in self.running_tasks:
            self.running_tasks[phone].cancel()
            del self.running_tasks[phone]
        
        if phone in self.clients:
            try:
                await self.clients[phone].disconnect()
            except:
                pass
            del self.clients[phone]
        
        if phone in self.pending_auth:
            try:
                await self.pending_auth[phone]["client"].disconnect()
            except:
                pass
            del self.pending_auth[phone]
        
        self.session_db.update_status(phone, "stopped")
    
    async def restart_all_clients(self):
        """Перезапускает всех клиентов (при старте бота)"""
        sessions = self.session_db.get_all_sessions()
        for phone, session_data in sessions.items():
            session_name = session_data.get("session_name")
            if session_name:
                try:
                    # Проверяем существование файла сессии
                    if not os.path.exists(f"{session_name}.session"):
                        logger.warning(f"Session file not found for {phone}")
                        continue
                    
                    client = TelegramClient(
                        session_name,
                        self.api_id,
                        self.api_hash
                    )
                    
                    await client.connect()
                    
                    # Проверяем авторизацию
                    if not await client.is_user_authorized():
                        await client.start()
                    
                    self.clients[phone] = client
                    
                    # Запускаем мониторинг
                    me = await client.get_me()
                    await self.start_monitoring(phone)
                    
                    logger.info(f"Restarted client for {phone}")
                    
                except Exception as e:
                    logger.error(f"Error restarting client for {phone}: {e}")
                    self.session_db.update_status(phone, "error")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
# Создаем экземпляры
db = SessionDatabase(SESSIONS_DB)
user_manager = UserBotManager(API_ID, API_HASH, db, TARGET_CHAT)

# Создаем бота и диспетчер
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== ФУНКЦИИ-ПРОВЕРКИ ==========
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@dp.message(Command("start"))
async def start_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    await message.answer(
        "🤖 **Бот для управления аккаунтами Telegram**\n\n"
        "**Доступные команды:**\n"
        "🔹 `/add [номер]` - добавить аккаунт\n"
        "🔹 `/code [номер] [код]` - ввести код подтверждения\n"
        "🔹 `/list` - список аккаунтов\n"
        "🔹 `/remove [номер]` - удалить аккаунт\n"
        "🔹 `/status` - статус всех аккаунтов\n"
        "🔹 `/stop [номер]` - остановить аккаунт\n"
        "🔹 `/restart [номер]` - перезапустить аккаунт\n"
        "🔹 `/start` - это меню\n\n"
        "**Примеры:**\n"
        "`/add +79999999999`\n"
        "`/code +79999999999 12345`",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("add"))
async def add_account(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите номер телефона.\n"
            "Пример: `/add +79999999999`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    phone = args[1].strip()
    
    # Проверяем формат номера
    if not re.match(r'^\+\d{10,15}$', phone):
        await message.answer("❌ Неверный формат номера. Используйте: +79999999999")
        return
    
    # Проверяем, не существует ли уже аккаунт
    if db.get_session(phone):
        await message.answer(f"❌ Аккаунт {phone} уже добавлен.")
        return
    
    # Начинаем авторизацию
    try:
        result = await user_manager.request_code(phone)
        
        if result["status"] == "need_code":
            await message.answer(
                f"📱 Отправлен код подтверждения на {phone}\n\n"
                "Введите код командой:\n"
                f"`/code {phone} [код]`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.answer(f"❌ Ошибка: {result['message']}")
            
    except Exception as e:
        logger.error(f"Error adding account: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("code"))
async def enter_code(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❌ Используйте: `/code [номер телефона] [код]`\n"
            "Пример: `/code +79999999999 12345`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    phone = args[1]
    code = args[2]
    
    # Проверяем, ожидается ли ввод кода
    if phone not in user_manager.pending_auth:
        await message.answer(f"❌ Аккаунт {phone} не ожидает ввода кода.\nИспользуйте /add")
        return
    
    try:
        result = await user_manager.authorize_client(phone, code)
        
        if result["status"] == "success":
            await message.answer(f"✅ Аккаунт {phone} успешно авторизован!\n\n"
                               f"Бот начал мониторинг чата @{TARGET_CHAT}")
        else:
            await message.answer(f"❌ Ошибка: {result['message']}")
            
    except Exception as e:
        logger.error(f"Error entering code: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("list"))
async def list_accounts(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    sessions = db.get_all_sessions()
    
    if not sessions:
        await message.answer("📭 Нет добавленных аккаунтов.")
        return
    
    text = "📋 **Список аккаунтов:**\n\n"
    for phone, session_data in sessions.items():
        status = session_data.get("status", "unknown")
        status_emoji = {
            "active": "🟢",
            "stopped": "🔴",
            "error": "🟡",
            "unknown": "⚪️"
        }.get(status, "⚪️")
        
        added_at = session_data.get("added_at", "Неизвестно")
        if len(added_at) > 16:
            added_at = added_at[:16].replace("T", " ")
        
        text += f"{status_emoji} **{phone}**\n"
        text += f"   📅 Добавлен: {added_at}\n"
        text += f"   📊 Статус: {status}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("remove"))
async def remove_account(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите номер телефона.\n"
            "Пример: `/remove +79999999999`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    phone = args[1].strip()
    
    # Проверяем существование
    if not db.get_session(phone):
        await message.answer(f"❌ Аккаунт {phone} не найден.")
        return
    
    # Останавливаем мониторинг
    await user_manager.stop_monitoring(phone)
    
    # Удаляем из БД
    db.remove_session(phone)
    
    await message.answer(f"✅ Аккаунт {phone} удален.")

@dp.message(Command("status"))
async def status_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    sessions = db.get_all_sessions()
    
    if not sessions:
        await message.answer("📭 Нет добавленных аккаунтов.")
        return
    
    active_count = 0
    stopped_count = 0
    error_count = 0
    
    for phone, session_data in sessions.items():
        status = session_data.get("status", "unknown")
        if status == "active":
            active_count += 1
        elif status == "stopped":
            stopped_count += 1
        elif status == "error":
            error_count += 1
    
    text = (
        "📊 **Общая статистика:**\n\n"
        f"🟢 Активных: {active_count}\n"
        f"🔴 Остановленных: {stopped_count}\n"
        f"🟡 С ошибкой: {error_count}\n"
        f"📱 Всего: {len(sessions)}\n\n"
        "Подробнее: `/list`"
    )
    
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("stop"))
async def stop_account(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите номер телефона.\n"
            "Пример: `/stop +79999999999`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    phone = args[1].strip()
    
    if phone not in user_manager.clients:
        await message.answer(f"❌ Аккаунт {phone} не активен.")
        return
    
    await user_manager.stop_monitoring(phone)
    await message.answer(f"✅ Аккаунт {phone} остановлен.")

@dp.message(Command("restart"))
async def restart_account(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите номер телефона.\n"
            "Пример: `/restart +79999999999`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    phone = args[1].strip()
    
    # Проверяем существование
    if not db.get_session(phone):
        await message.answer(f"❌ Аккаунт {phone} не найден.")
        return
    
    # Останавливаем если активен
    if phone in user_manager.clients:
        await user_manager.stop_monitoring(phone)
    
    # Перезапускаем
    session_name = db.get_session(phone)
    if session_name:
        try:
            client = TelegramClient(
                session_name,
                API_ID,
                API_HASH
            )
            
            await client.connect()
            if not await client.is_user_authorized():
                await client.start()
            
            user_manager.clients[phone] = client
            
            # Запускаем мониторинг
            me = await client.get_me()
            await user_manager.start_monitoring(phone)
            
            await message.answer(f"✅ Аккаунт {phone} перезапущен.")
            
        except Exception as e:
            logger.error(f"Error restarting account: {e}")
            await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("password"))
async def enter_password(message: Message):
    """Команда для ввода пароля двухфакторной аутентификации"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❌ Используйте: `/password [номер телефона] [пароль]`\n"
            "Пример: `/password +79999999999 mypassword`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    phone = args[1]
    password = args[2]
    
    # Проверяем, ожидается ли ввод кода
    if phone not in user_manager.pending_auth:
        await message.answer(f"❌ Аккаунт {phone} не ожидает ввода пароля.")
        return
    
    try:
        pending = user_manager.pending_auth[phone]
        client = pending["client"]
        session_path = pending["session_path"]
        
        # Вводим пароль
        await client.sign_in(password=password)
        
        # Сохраняем сессию
        db.add_session(phone, session_path)
        user_manager.clients[phone] = client
        
        # Удаляем из ожидающих
        del user_manager.pending_auth[phone]
        
        # Запускаем мониторинг чата
        await user_manager.start_monitoring(phone)
        
        await message.answer(f"✅ Аккаунт {phone} успешно авторизован с паролем!")
        
    except Exception as e:
        logger.error(f"Error entering password: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

# ========== ЗАПУСК БОТА ==========

async def on_startup():
    logger.info("🚀 Starting bot...")
    logger.info(f"📁 Data directory: {DATA_DIR}")
    logger.info(f"📁 Sessions directory: {SESSIONS_DIR}")
    
    # Перезапускаем все сохраненные сессии
    await user_manager.restart_all_clients()
    
    logger.info("✅ Bot started successfully!")

async def on_shutdown():
    logger.info("🛑 Shutting down...")
    
    # Останавливаем всех клиентов
    for phone in list(user_manager.clients.keys()):
        await user_manager.stop_monitoring(phone)
    
    # Закрываем ожидающие авторизации
    for phone in list(user_manager.pending_auth.keys()):
        try:
            await user_manager.pending_auth[phone]["client"].disconnect()
        except:
            pass
    
    # Закрываем сессию бота
    await bot.session.close()
    
    logger.info("✅ Bot stopped!")

async def main():
    print("=" * 50)
    print("🤖 Telegram Account Manager Bot (Telethon)")
    print("=" * 50)
    print(f"📁 Data directory: {DATA_DIR}")
    print(f"📁 Sessions directory: {SESSIONS_DIR}")
    print(f"👤 Admin ID: {ADMIN_ID}")
    print(f"🎯 Target chat: @{TARGET_CHAT}")
    print("=" * 50)
    
    # Регистрируем хуки для запуска и остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
