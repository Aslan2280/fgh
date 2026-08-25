#!/usr/bin/env python3
"""
GMPLAY — Telegram Bot на aiogram + JSON-база данных
"""

import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
import random
import math
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

# ==================== КОНФИГУРАЦИЯ ====================

BOT_TOKEN = "8986114517:AAHoIHq-Kgk6iYEfxQLWsqN_sMNHc2pRYL8"
WEBAPP_URL = "https://deathgmp.telecoder.workers.dev"  # Ваш CloudFlare Pages URL

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gmplay.json")

# Настройки игры
STARTING_BALANCE = 0
DAILY_BONUS_AMOUNT = 2
DAILY_BONUS_PERIOD_MS = 24 * 60 * 60 * 1000
REFERRAL_CUT = 0.01

CRASH_BETTING_MS = 6000
CRASH_PAUSE_MS = 2600
CRASH_GROWTH_RATE = 0.22
CRASH_MAX_BETS_PER_USER = 1
CRASH_MIN_BET = 1

MIN_DEPOSIT_FOR_WITHDRAW = 100
MIN_BALANCE_FOR_WITHDRAW = 10
WITHDRAW_WEEK_MS = 7 * 24 * 60 * 60 * 1000

# Настройки логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== JSON-БАЗА ДАННЫХ ====================

_db_lock = threading.RLock()


def _init_db():
    if os.path.exists(DB_PATH):
        return

    initial_data = {
        "users": {},
        "bets": [],
        "deposits": [],
        "withdraw_requests": [],
        "banned_users": {},
        "admins": [{"tg_id": 6539341659}],  # Ваш TG ID
        "crash": {
            "round_id": 0,
            "history": [],
        },
        "_meta": {
            "bet_id_counter": 1,
            "deposit_id_counter": 1,
            "withdraw_id_counter": 1,
        }
    }

    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(initial_data, f, ensure_ascii=False, indent=2)


def _load_db():
    _init_db()
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_db(data):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user(tg_id: int) -> Optional[Dict]:
    with _db_lock:
        db = _load_db()
        return db["users"].get(str(tg_id))


def get_user_by_id(user_id: int) -> Optional[Dict]:
    with _db_lock:
        db = _load_db()
        for user in db["users"].values():
            if user.get("id") == user_id:
                return user
        return None


def is_admin(tg_id: int) -> bool:
    with _db_lock:
        db = _load_db()
        return any(a.get("tg_id") == tg_id for a in db.get("admins", []))


def get_or_create_user(tg_user: Dict, start_param: str = None) -> tuple:
    with _db_lock:
        db = _load_db()
        now = int(time.time() * 1000)
        tg_id = tg_user["id"]
        key = str(tg_id)

        if key in db["users"]:
            user = db["users"][key]
            user.update({
                "first_name": tg_user.get("first_name", ""),
                "last_name": tg_user.get("last_name", ""),
                "username": tg_user.get("username", ""),
                "photo_url": tg_user.get("photo_url", ""),
                "updated_at": now,
            })
            _save_db(db)
            return user, False

        user_id = len(db["users"]) + 1
        referred_by = None

        if start_param and start_param.upper().startswith("GM"):
            try:
                ref_tg_id = int(start_param[2:], 16)
                if ref_tg_id != tg_id:
                    ref_key = str(ref_tg_id)
                    if ref_key in db["users"]:
                        referred_by = db["users"][ref_key]["id"]
            except ValueError:
                pass

        user = {
            "id": user_id,
            "tg_id": tg_id,
            "first_name": tg_user.get("first_name", ""),
            "last_name": tg_user.get("last_name", ""),
            "username": tg_user.get("username", ""),
            "photo_url": tg_user.get("photo_url", ""),
            "balance": STARTING_BALANCE,
            "referred_by": referred_by,
            "last_bonus_at": None,
            "created_at": now,
            "last_deposit_check": now,
        }

        db["users"][key] = user
        _save_db(db)
        return user, True


def is_user_banned(tg_id: int) -> Optional[Dict]:
    with _db_lock:
        db = _load_db()
        return db["banned_users"].get(str(tg_id))


def ref_code_for(tg_id: int) -> str:
    return "GM" + format(int(tg_id), "x").upper()


# ==================== ХЕНДЛЕРЫ КОМАНД ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    try:
        # Проверяем бан
        if is_user_banned(message.from_user.id):
            await message.answer(
                "⛔ <b>Доступ запрещён</b>\n\n"
                "Вы были забанены. Обратитесь в поддержку.",
                parse_mode="HTML"
            )
            return

        # Разбираем start_param (реферальный код)
        start_param = None
        if " " in message.text:
            start_param = message.text.split(" ", 1)[1]

        # Создаём или обновляем пользователя
        tg_user = {
            "id": message.from_user.id,
            "first_name": message.from_user.first_name,
            "last_name": message.from_user.last_name or "",
            "username": message.from_user.username or "",
            "photo_url": None,
        }

        user, is_new = get_or_create_user(tg_user, start_param)

        logger.info(f"Пользователь {message.from_user.id} запустил бота. Новый: {is_new}")

        # Формируем URL для WebApp с реферальным кодом
        webapp_url = WEBAPP_URL
        if user:
            webapp_url += f"?startapp={ref_code_for(user['tg_id'])}"

        # Проверяем админа
        is_admin_user = is_admin(message.from_user.id)

        # Клавиатура
        keyboard = [
            [InlineKeyboardButton(
                text="🎮 Играть в GMPLAY",
                web_app=WebAppInfo(url=webapp_url)
            )]
        ]

        # Кнопка админ-панели для админов
        if is_admin_user:
            keyboard.append([
                InlineKeyboardButton(
                    text="⚙️ Админ-панель",
                    web_app=WebAppInfo(url=f"{WEBAPP_URL}/admin.html")
                )
            ])

        keyboard.append([
            InlineKeyboardButton(text="📢 Канал", url="https://t.me/gmplay_news"),
            InlineKeyboardButton(text="🛟 Поддержка", url="https://t.me/gmp_help")
        ])

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # Приветственное сообщение
        welcome_text = (
            "🚀 <b>Добро пожаловать в GMPLAY!</b>\n\n"
            "🎮 <b>Твоя победа начинается здесь!</b>\n\n"
            "⚡ Играй в <b>Краш</b> — забирай выигрыш до того, как ракета улетит!\n"
            "💰 Получай ежедневный бонус\n"
            "👥 Приглашай друзей и зарабатывай 1% от их проигрышей\n"
            "💸 Выводи средства в любой момент\n\n"
            "👇 <b>Нажми на кнопку ниже, чтобы начать!</b>"
        )

        await message.answer(welcome_text, parse_mode="HTML", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Проверка админ-доступа"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    webapp_url = f"{WEBAPP_URL}/admin.html"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚙️ Открыть админ-панель",
            web_app=WebAppInfo(url=webapp_url)
        )]
    ])

    await message.answer(
        "👋 <b>Добро пожаловать в админ-панель!</b>\n\n"
        "Здесь вы можете управлять пользователями, заявками на вывод и депозиты.",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Команда помощи"""
    await message.answer(
        "🤖 <b>Помощь по GMPLAY</b>\n\n"
        "/start — запустить бота\n"
        "/help — эта справка\n"
        "/admin — админ-панель (только для администраторов)\n\n"
        "По всем вопросам: @gmp_help",
        parse_mode="HTML"
    )


@dp.message(Command("me"))
async def cmd_me(message: types.Message):
    """Информация о пользователе"""
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Пользователь не найден. Напишите /start")
        return

    ref_code = ref_code_for(user["tg_id"])
    balance = user["balance"]

    await message.answer(
        f"👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user['tg_id']}</code>\n"
        f"💰 Баланс: <b>{balance} GMP</b>\n"
        f"🔗 Реферальный код: <code>{ref_code}</code>\n\n"
        f"Приглашайте друзей и получайте 1% от их проигрышей!",
        parse_mode="HTML"
    )


@dp.message()
async def echo(message: types.Message):
    """Ответ на любое сообщение"""
    await message.answer(
        "👋 Напишите /start, чтобы начать игру в GMPLAY!\n"
        "Или /help для справки."
    )


# ==================== ЗАПУСК БОТА ====================

async def main():
    """Главная функция запуска бота"""
    _init_db()
    logger.info("🚀 Запуск бота GMPLAY...")
    logger.info(f"📁 База данных: {DB_PATH}")
    logger.info(f"🌐 WebApp URL: {WEBAPP_URL}")

    # Запускаем краш-игру в отдельном потоке
    threading.Thread(target=crash_game_loop, daemon=True).start()

    # Запускаем бота с long polling
    logger.info("✅ Бот запущен и готов к работе!")
    await dp.start_polling(bot)


# ==================== КРАШ-ИГРА ====================

crash_state = {
    "phase": "betting",
    "round_id": 0,
    "phase_started_at": int(time.time() * 1000),
    "crash_point": None,
    "bets": {},
}
crash_next_bet_id = 1
crash_lock = threading.Lock()


def crash_roll_point():
    r = random.random()
    if r < 0.02:
        return 1.00
    point = 0.98 / (1 - r)
    return round(min(point, 250), 2)


def crash_current_multiplier():
    if crash_state["phase"] != "playing":
        return crash_state.get("crash_point") if crash_state["phase"] == "crashed" else 1.0
    elapsed_s = (int(time.time() * 1000) - crash_state["phase_started_at"]) / 1000
    m = math.exp(CRASH_GROWTH_RATE * elapsed_s)
    return min(m, crash_state["crash_point"])


def crash_auto_cashout_tick(current_multiplier):
    to_resolve = []
    with crash_lock:
        for bet_id, bet in list(crash_state["bets"].items()):
            if bet["status"] == "active" and bet.get("auto_cashout") and current_multiplier >= bet["auto_cashout"]:
                bet["status"] = "won"
                bet["win_amount"] = bet["amount"] * bet["auto_cashout"]
                bet["cashout_multiplier"] = bet["auto_cashout"]
                to_resolve.append((bet_id, dict(bet)))

    if not to_resolve:
        return

    with _db_lock:
        db = _load_db()
        now = int(time.time() * 1000)

        for bet_id, bet in to_resolve:
            tg_id = bet["tg_id"]
            key = str(tg_id)
            if key in db["users"]:
                user = db["users"][key]
                win_amount = bet["amount"] * bet["auto_cashout"]
                user["balance"] += win_amount

                db["bets"].append({
                    "id": db["_meta"]["bet_id_counter"],
                    "user_id": bet["user_id"],
                    "tg_id": tg_id,
                    "game": "crash",
                    "bet_amount": bet["amount"],
                    "multiplier": bet["auto_cashout"],
                    "result": "win",
                    "win_amount": win_amount,
                    "created_at": now,
                })
                db["_meta"]["bet_id_counter"] += 1

        _save_db(db)


def crash_resolve_round_losses():
    with _db_lock:
        db = _load_db()
        now = int(time.time() * 1000)

        for bet in list(crash_state["bets"].values()):
            if bet["status"] == "active":
                bet["status"] = "lost"
                db["bets"].append({
                    "id": db["_meta"]["bet_id_counter"],
                    "user_id": bet["user_id"],
                    "tg_id": bet["tg_id"],
                    "game": "crash",
                    "bet_amount": bet["amount"],
                    "multiplier": crash_state["crash_point"],
                    "result": "lose",
                    "win_amount": 0,
                    "created_at": now,
                })
                db["_meta"]["bet_id_counter"] += 1

        _save_db(db)


def crash_game_loop():
    while True:
        with crash_lock:
            crash_state["phase"] = "betting"
            crash_state["round_id"] += 1
            crash_state["phase_started_at"] = int(time.time() * 1000)
            crash_state["crash_point"] = crash_roll_point()
            crash_state["bets"] = {}

        time.sleep(CRASH_BETTING_MS / 1000)

        with crash_lock:
            crash_state["phase"] = "playing"
            crash_state["phase_started_at"] = int(time.time() * 1000)
            target = crash_state["crash_point"]

        while True:
            elapsed_s = (int(time.time() * 1000) - crash_state["phase_started_at"]) / 1000
            m = math.exp(CRASH_GROWTH_RATE * elapsed_s)
            if m >= target:
                break
            crash_auto_cashout_tick(min(m, target))
            time.sleep(0.05)

        with crash_lock:
            crash_state["phase"] = "crashed"
            crash_state["phase_started_at"] = int(time.time() * 1000)

            with _db_lock:
                db = _load_db()
                db["crash"]["history"].insert(0, crash_state["crash_point"])
                db["crash"]["history"] = db["crash"]["history"][:15]
                _save_db(db)

            crash_resolve_round_losses()

        time.sleep(CRASH_PAUSE_MS / 1000)


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
