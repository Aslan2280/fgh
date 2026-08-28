import asyncio
import json
import os
import aiohttp
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Dice
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ========== КОНФИГ ==========
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"
ADMINS = [6539341659, 987654321]  # ID админов

# Конфиг для piarflow API
PIARFLOW_API_KEY = "ВАШ_API_КЛЮЧ"
PIARFLOW_API_URL = "https://piarflow.com/api/v1"

# ========== JSON БД ==========
DB_FILE = "bot_db.json"

def load_db():
    if not os.path.exists(DB_FILE):
        default = {
            "checks": [],
            "users": {},
            "waiting_users": []  # Пользователи, ожидающие чек
        }
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
        return default
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TASKS_REQUIRED = 5
DICE_COOLDOWN = 45  # минут
DICE_NUMBER = random.randint(1, 6)  # Случайное число для кубика

# ========== РАБОТА С API PIARFLOW ==========
class PiarflowAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    async def get_tasks(self, count: int = 5) -> List[Dict]:
        async with aiohttp.ClientSession() as session:
            try:
                params = {
                    "limit": count,
                    "type": "subscribe"
                }
                async with session.get(
                    f"{PIARFLOW_API_URL}/tasks",
                    headers=self.headers,
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("tasks", [])
                    else:
                        print(f"API Error: {response.status}")
                        return []
            except Exception as e:
                print(f"API Exception: {e}")
                return []
    
    async def check_task_completion(self, user_id: int, task_id: str) -> bool:
        async with aiohttp.ClientSession() as session:
            try:
                data = {
                    "task_id": task_id,
                    "user_id": str(user_id)
                }
                async with session.post(
                    f"{PIARFLOW_API_URL}/tasks/check",
                    headers=self.headers,
                    json=data
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get("completed", False)
                    return False
            except Exception as e:
                print(f"Check Task Exception: {e}")
                return False
    
    async def get_user_tasks_status(self, user_id: int, task_ids: List[str]) -> List[bool]:
        results = []
        for task_id in task_ids:
            completed = await self.check_task_completion(user_id, task_id)
            results.append(completed)
        return results

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_user_data(user_id: int) -> dict:
    db = load_db()
    user_id_str = str(user_id)
    if user_id_str not in db["users"]:
        db["users"][user_id_str] = {
            "tasks_completed": 0,
            "checks_received": 0,
            "task_ids": [],
            "last_dice_time": None,
            "dice_number": None,
            "has_waiting_check": False  # Флаг ожидания чека
        }
        save_db(db)
    return db["users"][user_id_str]

def update_user_data(user_id: int, data: dict):
    db = load_db()
    user_id_str = str(user_id)
    if user_id_str not in db["users"]:
        db["users"][user_id_str] = {}
    db["users"][user_id_str].update(data)
    save_db(db)

def get_available_checks_count() -> int:
    db = load_db()
    return len(db["checks"])

def get_random_check() -> Optional[str]:
    db = load_db()
    if not db["checks"]:
        return None
    check = db["checks"].pop(0)
    save_db(db)
    return check

def add_check(check_link: str):
    db = load_db()
    if check_link not in db["checks"]:
        db["checks"].append(check_link)
        save_db(db)
        return True
    return False

def add_waiting_user(user_id: int):
    db = load_db()
    if user_id not in db["waiting_users"]:
        db["waiting_users"].append(user_id)
        save_db(db)
        return True
    return False

def remove_waiting_user(user_id: int):
    db = load_db()
    if user_id in db["waiting_users"]:
        db["waiting_users"].remove(user_id)
        save_db(db)
        return True
    return False

def check_and_send_waiting_checks():
    """Проверяет ожидающих пользователей и отправляет чеки"""
    db = load_db()
    waiting = db["waiting_users"].copy()
    
    for user_id in waiting:
        if get_available_checks_count() > 0:
            check = get_random_check()
            if check:
                user_data = get_user_data(user_id)
                user_data["checks_received"] += 1
                user_data["has_waiting_check"] = False
                update_user_data(user_id, user_data)
                remove_waiting_user(user_id)
                
                # Отправляем сообщение пользователю
                asyncio.create_task(send_check_notification(user_id, check))

async def send_check_notification(user_id: int, check: str):
    try:
        await bot.send_message(
            user_id,
            f"✅ Появился новый чек! Ваш чек:\n\n"
            f"🔗 {check}\n\n"
            f"📊 Всего получено чеков: {get_user_data(user_id)['checks_received']}"
        )
    except Exception as e:
        print(f"Failed to send notification to {user_id}: {e}")

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🎁 Получить чек", callback_data="get_check"),
        InlineKeyboardButton(text="📊 Наличие чеков", callback_data="check_availability"),
        InlineKeyboardButton(text="🎲 Чек за кубик", callback_data="dice_check")
    )
    builder.adjust(1)
    return builder.as_markup()

def get_dice_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🎲 Бросить кубик", callback_data="roll_dice"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")
    )
    builder.adjust(1)
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user_data(message.from_user.id)
    
    text = """бᴇᴄᴨᴧᴀᴛныᴇ мᴄᴏᴎн 
━━━━━━━━━━━━━━━━━━━━
чᴇᴋᴎ: 111k–1kk mCoin 👑
ᴋᴀждыᴇ 29 мᴎнуᴛ"""
    
    await message.answer(text, reply_markup=get_main_keyboard())

@dp.callback_query(lambda c: c.data == "check_availability")
async def handle_check_availability(callback: types.CallbackQuery):
    count = get_available_checks_count()
    await callback.message.edit_text(
        f"🎁 чᴇᴋᴏʙ нᴀ мᴄᴏᴎн: {count}",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "get_check")
async def handle_get_check(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = get_user_data(user_id)
    
    # Проверяем, есть ли доступные чеки
    if get_available_checks_count() == 0:
        # Добавляем пользователя в лист ожидания
        add_waiting_user(user_id)
        user_data["has_waiting_check"] = True
        update_user_data(user_id, user_data)
        
        await callback.message.edit_text(
            "😔 К сожалению, чеки закончились.\n\n"
            "✅ Вы добавлены в лист ожидания. Как только появятся новые чеки - мы вам пришлём уведомление!\n"
            "⏰ Обычно чеки появляются каждые 29 минут.",
            reply_markup=get_main_keyboard()
        )
        await callback.answer()
        return
    
    # Получаем задания из API piarflow
    api = PiarflowAPI(PIARFLOW_API_KEY)
    tasks = await api.get_tasks(TASKS_REQUIRED)
    
    if not tasks:
        await callback.message.edit_text(
            "😔 Не удалось получить задания. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
        await callback.answer()
        return
    
    # Сохраняем ID заданий
    task_ids = [task["id"] for task in tasks]
    user_data["task_ids"] = task_ids
    user_data["tasks_completed"] = 0
    update_user_data(user_id, user_data)
    
    # Формируем сообщение с заданиями
    tasks_text = "\n".join([f"{i+1}. {task['name']}" for i, task in enumerate(tasks)])
    
    text = f"""1️⃣ ʙыᴨᴏᴧнᴎ ɜᴀдᴀнᴎя
2️⃣ ᴨᴏᴧучᴎ чᴇᴋ нᴀ мᴄᴏᴎн
━━━━━━━━━━━━━━━━━━━━
📋 0/{TASKS_REQUIRED}
━━━━━━━━━━━━━━━━━━━━
⚪️ ᴏᴄᴛᴀᴧᴏᴄь: {TASKS_REQUIRED}

📌 Задания:
{tasks_text}

💡 После выполнения всех заданий нажмите кнопку проверки."""
    
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Проверить выполнение", callback_data="check_tasks"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")
    )
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "check_tasks")
async def handle_check_tasks(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = get_user_data(user_id)
    
    task_ids = user_data.get("task_ids", [])
    
    if not task_ids:
        await callback.message.edit_text(
            "❌ У вас нет активных заданий. Нажмите 'Получить чек' для начала.",
            reply_markup=get_main_keyboard()
        )
        await callback.answer()
        return
    
    # Проверяем выполнение через API
    api = PiarflowAPI(PIARFLOW_API_KEY)
    statuses = await api.get_user_tasks_status(user_id, task_ids)
    completed_count = sum(statuses)
    user_data["tasks_completed"] = completed_count
    update_user_data(user_id, user_data)
    
    if completed_count >= TASKS_REQUIRED:
        # Проверяем наличие чеков
        if get_available_checks_count() == 0:
            # Добавляем в лист ожидания
            add_waiting_user(user_id)
            user_data["has_waiting_check"] = True
            user_data["task_ids"] = []
            update_user_data(user_id, user_data)
            
            await callback.message.edit_text(
                "✅ Вы выполнили все задания!\n\n"
                "😔 Но чеки закончились. Вы добавлены в лист ожидания.\n"
                "📨 Как только появятся новые чеки - мы вам пришлём уведомление!",
                reply_markup=get_main_keyboard()
            )
        else:
            # Выдаём чек
            check = get_random_check()
            if check:
                user_data["checks_received"] += 1
                user_data["task_ids"] = []
                update_user_data(user_id, user_data)
                
                await callback.message.edit_text(
                    f"✅ Поздравляем! Вы выполнили все задания и получили чек:\n\n"
                    f"🔗 {check}\n\n"
                    f"📊 Всего получено чеков: {user_data['checks_received']}\n"
                    f"💡 Чеки можно использовать в игре @gminesbot",
                    reply_markup=get_main_keyboard()
                )
    else:
        remaining = TASKS_REQUIRED - completed_count
        await callback.message.edit_text(
            f"❌ Выполнено заданий: {completed_count}/{TASKS_REQUIRED}\n"
            f"Осталось: {remaining}\n\n"
            f"📌 Выполните все задания и нажмите проверку снова.",
            reply_markup=get_main_keyboard()
        )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "dice_check")
async def handle_dice_check(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = get_user_data(user_id)
    
    # Генерируем случайное число для кубика
    dice_number = random.randint(1, 6)
    user_data["dice_number"] = dice_number
    update_user_data(user_id, user_data)
    
    text = f"""⭐️ ᴋубиᴋ нᴀ чᴇᴋ ⭐️
━━━━━━━━━━━━━━
😐 ɜᴀᴦᴀдᴀнᴏ: {dice_number}
ʙыᴨᴀдᴇᴛ {dice_number} — чек!

ᴘᴀɜ ʙ 45 мᴎн​"""
    
    # Проверяем кулдаун
    last_dice_time = user_data.get("last_dice_time")
    can_roll = True
    
    if last_dice_time:
        last_time = datetime.fromisoformat(last_dice_time)
        time_diff = datetime.now() - last_time
        if time_diff.total_seconds() < DICE_COOLDOWN * 60:
            can_roll = False
            remaining = DICE_COOLDOWN * 60 - time_diff.total_seconds()
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            text += f"\n\n⏳ Доступно через: {minutes}м {seconds}с"
    
    builder = InlineKeyboardBuilder()
    if can_roll:
        builder.add(
            InlineKeyboardButton(text="🎲 Бросить кубик", callback_data="roll_dice"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")
        )
    else:
        builder.add(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")
        )
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "roll_dice")
async def handle_roll_dice(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = get_user_data(user_id)
    
    # Проверяем кулдаун
    last_dice_time = user_data.get("last_dice_time")
    if last_dice_time:
        last_time = datetime.fromisoformat(last_dice_time)
        time_diff = datetime.now() - last_time
        if time_diff.total_seconds() < DICE_COOLDOWN * 60:
            remaining = DICE_COOLDOWN * 60 - time_diff.total_seconds()
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            await callback.answer(f"⏳ Подождите {minutes}м {seconds}с", show_alert=True)
            return
    
    # Отправляем кубик
    dice_message = await callback.message.answer_dice(emoji="🎲")
    dice_value = dice_message.dice.value
    
    # Обновляем время последнего броска
    user_data["last_dice_time"] = datetime.now().isoformat()
    update_user_data(user_id, user_data)
    
    # Проверяем, совпало ли число
    target_number = user_data.get("dice_number", random.randint(1, 6))
    
    if dice_value == target_number:
        # Проверяем наличие чеков
        if get_available_checks_count() == 0:
            add_waiting_user(user_id)
            user_data["has_waiting_check"] = True
            update_user_data(user_id, user_data)
            
            await callback.message.edit_text(
                f"🎲 Выпало: {dice_value} (загадано: {target_number})\n\n"
                "🎉 Поздравляем! Вы выиграли чек!\n"
                "😔 Но чеки закончились. Вы добавлены в лист ожидания.\n"
                "📨 Как только появятся новые чеки - мы вам пришлём уведомление!",
                reply_markup=get_main_keyboard()
            )
        else:
            check = get_random_check()
            if check:
                user_data["checks_received"] += 1
                update_user_data(user_id, user_data)
                
                await callback.message.edit_text(
                    f"🎲 Выпало: {dice_value} (загадано: {target_number})\n\n"
                    "🎉 Поздравляем! Вы выиграли чек!\n\n"
                    f"🔗 {check}\n\n"
                    f"📊 Всего получено чеков: {user_data['checks_received']}",
                    reply_markup=get_main_keyboard()
                )
    else:
        await callback.message.edit_text(
            f"🎲 Выпало: {dice_value} (загадано: {target_number})\n\n"
            "😔 К сожалению, числа не совпали. Попробуйте снова через 45 минут!",
            reply_markup=get_main_keyboard()
        )
    
    await callback.answer()

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def handle_back_to_menu(callback: types.CallbackQuery):
    text = """бᴇᴄᴨᴧᴀᴛныᴇ мᴄᴏᴎн 
━━━━━━━━━━━━━━━━━━━━
чᴇᴋᴎ: 111k–1kk mCoin 👑
ᴋᴀждыᴇ 29 мᴎнуᴛ"""
    await callback.message.edit_text(text, reply_markup=get_main_keyboard())
    await callback.answer()

# ========== АДМИН-КОМАНДЫ ==========
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("⛔ У вас нет прав для этой команды.")
        return
    
    db = load_db()
    total_users = len(db["users"])
    total_checks = len(db["checks"])
    total_checks_received = sum(user.get("checks_received", 0) for user in db["users"].values())
    waiting_users = len(db["waiting_users"])
    
    text = f"""📊 СТАТИСТИКА БОТА
━━━━━━━━━━━━━━━━━━━━
👥 Всего пользователей: {total_users}
🎁 Доступно чеков: {total_checks}
✅ Выдано чеков: {total_checks_received}
⏳ Ожидают чек: {waiting_users}
━━━━━━━━━━━━━━━━━━━━
⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"""
    
    await message.answer(text)

@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("⛔ У вас нет прав для этой команды.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /add [ссылка на чек]")
        return
    
    check_link = args[1].strip()
    if add_check(check_link):
        await message.answer(f"✅ Чек успешно добавлен!\n\n🔗 {check_link}")
        # Проверяем, есть ли ожидающие пользователи
        await check_and_send_waiting_checks_async()
    else:
        await message.answer("⚠️ Такой чек уже существует в базе.")

@dp.message(Command("cheks"))
async def cmd_checks(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("⛔ У вас нет прав для этой команды.")
        return
    
    db = load_db()
    checks = db["checks"]
    
    if not checks:
        await message.answer("📭 Нет доступных чеков.")
        return
    
    text = f"🎁 Доступные чеки ({len(checks)}):\n\n"
    for i, check in enumerate(checks, 1):
        text += f"{i}. {check}\n"
        if i > 20:
            text += f"\n... и ещё {len(checks) - 20} чеков"
            break
    
    await message.answer(text)

async def check_and_send_waiting_checks_async():
    """Асинхронная проверка ожидающих пользователей"""
    db = load_db()
    waiting = db["waiting_users"].copy()
    
    for user_id in waiting:
        if get_available_checks_count() > 0:
            check = get_random_check()
            if check:
                user_data = get_user_data(user_id)
                user_data["checks_received"] += 1
                user_data["has_waiting_check"] = False
                update_user_data(user_id, user_data)
                remove_waiting_user(user_id)
                
                try:
                    await bot.send_message(
                        user_id,
                        f"✅ Появился новый чек! Ваш чек:\n\n"
                        f"🔗 {check}\n\n"
                        f"📊 Всего получено чеков: {user_data['checks_received']}\n"
                        f"💡 Чеки можно использовать в игре @gminesbot"
                    )
                except Exception as e:
                    print(f"Failed to send to {user_id}: {e}")

# ========== ЗАПУСК ==========
async def main():
    print("🤖 Бот запущен...")
    print(f"🎲 Загаданное число для кубика: {DICE_NUMBER}")
    print(f"⏰ Кулдаун кубика: {DICE_COOLDOWN} минут")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
