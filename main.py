import json
import os
import random
import logging
import asyncio
import re
import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ===================== КОНФИГ =====================
BOT_TOKEN = "8986114517:AAHoIHq-Kgk6iYEfxQLWsqN_sMNHc2pRYL8"
ADMIN_IDS = [6539341659, 8705040410]  # Список админов
CURRENCY = "GMP"
DATABASE_FILE = "deathgmp_data.json"
WITHDRAW_FILE = "withdraw_requests.json"
DEPOSIT_FILE = "deposit_requests.json"
START_BALANCE = 0  # Начальный баланс 0

logging.basicConfig(level=logging.INFO)

# ===================== СОСТОЯНИЯ =====================
class DepositStates(StatesGroup):
    waiting_amount = State()

class WithdrawStates(StatesGroup):
    waiting_amount = State()
    waiting_address = State()

# ===================== БАЗА ДАННЫХ =====================
class DB:
    def __init__(self, file):
        self.file = file
        self._ensure()
    
    def _ensure(self):
        if not os.path.exists(self.file):
            with open(self.file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
    
    def read(self):
        try:
            with open(self.file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def write(self, data):
        with open(self.file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

class UserDB:
    def __init__(self):
        self.db = DB(DATABASE_FILE)
    
    def get(self, uid):
        data = self.db.read()
        uid = str(uid)
        if uid not in data:
            data[uid] = {
                'balance': START_BALANCE, 
                'games_played': 0, 
                'wins': 0, 
                'total_deposited': 0, 
                'total_withdrawn': 0
            }
            self.db.write(data)
        return data[uid]
    
    def update(self, uid, **kwargs):
        data = self.db.read()
        uid = str(uid)
        if uid not in data:
            data[uid] = self.get(uid)
        data[uid].update(kwargs)
        self.db.write(data)
    
    def top(self, limit=10):
        data = self.db.read()
        users = sorted(data.items(), key=lambda x: x[1].get('balance', 0), reverse=True)
        return users[:limit]

class RequestDB:
    def __init__(self, file):
        self.db = DB(file)
        self._ensure()
    
    def _ensure(self):
        data = self.db.read()
        if not data or 'requests' not in data:
            data = {'requests': [], 'counter': 0}
            self.db.write(data)
    
    def create(self, user_id, username, amount, address=None):
        data = self.db.read()
        data['counter'] += 1
        request_id = data['counter']
        
        request = {
            'id': request_id,
            'user_id': user_id,
            'username': username,
            'amount': amount,
            'address': address,
            'status': 'pending',
            'created_at': datetime.datetime.now().isoformat(),
            'processed_at': None,
            'processed_by': None
        }
        
        data['requests'].append(request)
        self.db.write(data)
        return request
    
    def get_pending(self):
        data = self.db.read()
        return [r for r in data['requests'] if r['status'] == 'pending']
    
    def get_by_user(self, user_id):
        data = self.db.read()
        return [r for r in data['requests'] if r['user_id'] == user_id]
    
    def get_by_id(self, request_id):
        data = self.db.read()
        for r in data['requests']:
            if r['id'] == request_id:
                return r
        return None
    
    def approve(self, request_id, admin_id):
        data = self.db.read()
        for r in data['requests']:
            if r['id'] == request_id and r['status'] == 'pending':
                r['status'] = 'approved'
                r['processed_at'] = datetime.datetime.now().isoformat()
                r['processed_by'] = admin_id
                self.db.write(data)
                return r
        return None
    
    def reject(self, request_id, admin_id):
        data = self.db.read()
        for r in data['requests']:
            if r['id'] == request_id and r['status'] == 'pending':
                r['status'] = 'rejected'
                r['processed_at'] = datetime.datetime.now().isoformat()
                r['processed_by'] = admin_id
                self.db.write(data)
                return r
        return None

# ===================== ИГРА КРАШ =====================
class CrashGame:
    def __init__(self, db):
        self.db = db
        self.games = {}
    
    def start(self, uid, bet, target):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        if target < 1.1 or target > 100:
            return {'ok': False, 'msg': '❌ Множитель от 1.1 до 100'}
        
        new_balance = user['balance'] - bet
        self.db.update(uid, balance=new_balance)
        
        r = random.random()
        if r < 0.4:
            crash = round(random.uniform(1.01, 1.5), 2)
        elif r < 0.65:
            crash = round(random.uniform(1.51, 2.5), 2)
        elif r < 0.8:
            crash = round(random.uniform(2.51, 4.0), 2)
        elif r < 0.9:
            crash = round(random.uniform(4.01, 7.0), 2)
        elif r < 0.96:
            crash = round(random.uniform(7.01, 15.0), 2)
        elif r < 0.99:
            crash = round(random.uniform(15.01, 50.0), 2)
        else:
            crash = round(random.uniform(50.01, 100.0), 2)
        
        if crash >= target:
            win = int(bet * target)
            self.db.update(uid, balance=new_balance + win,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'crash': crash, 'amount': win, 'balance': new_balance + win}
        else:
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            return {'ok': True, 'win': False, 'crash': crash, 'amount': bet, 'balance': new_balance}
    
    def cancel_game(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        g = self.games[uid]
        user = self.db.get(uid)
        new_bal = user['balance'] + g['bet']
        self.db.update(uid, balance=new_bal)
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} {CURRENCY} возвращено'}

# ===================== ИГРА МИНЫ =====================
class Mines:
    def __init__(self, db):
        self.db = db
        self.games = {}
        self.mults_data = {
            1: [1.01, 1.05, 1.10, 1.15, 1.21, 1.27, 1.34, 1.41, 1.48, 1.56, 1.64, 1.72, 1.81, 1.90, 2.00, 2.10, 2.21, 2.32, 2.44, 2.56, 2.69, 2.82, 2.96, 3.11],
            2: [1.05, 1.15, 1.26, 1.39, 1.53, 1.68, 1.85, 2.04, 2.24, 2.46, 2.71, 2.98, 3.28, 3.61, 3.97, 4.37, 4.81, 5.29, 5.82, 6.40, 7.04, 7.74, 8.51, 9.36],
            3: [1.10, 1.26, 1.45, 1.68, 1.94, 2.24, 2.59, 3.00, 3.47, 4.01, 4.64, 5.37, 6.21, 7.19, 8.32, 9.63, 11.14, 12.89, 14.92, 17.26, 19.97, 23.11, 26.74, 30.94],
            4: [1.15, 1.39, 1.68, 2.04, 2.47, 3.00, 3.64, 4.41, 5.35, 6.49, 7.87, 9.55, 11.58, 14.05, 17.04, 20.67, 25.08, 30.42, 36.90, 44.76, 54.30, 65.86, 79.89, 96.91],
            5: [1.21, 1.53, 1.94, 2.47, 3.14, 3.99, 5.07, 6.45, 8.20, 10.43, 13.26, 16.86, 21.44, 27.26, 34.66, 44.07, 56.04, 71.25, 90.60, 115.20, 146.48, 186.25, 236.83, 301.13],
            6: [1.27, 1.68, 2.24, 3.00, 4.01, 5.37, 7.19, 9.63, 12.89, 17.26, 23.11, 30.94, 41.43, 55.47, 74.27, 99.44, 133.14, 178.25, 238.65, 319.54, 427.86, 572.90, 767.09, 1027.23]
        }
    
    def mults(self, count):
        return self.mults_data.get(count, [1.0] * 24)
    
    def start(self, uid, bet, mines=1):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if mines < 1 or mines > 6:
            return {'ok': False, 'msg': '❌ Мин от 1 до 6'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        new_balance = user['balance'] - bet
        self.db.update(uid, balance=new_balance)
        
        field = [['⬜'] * 5 for _ in range(5)]
        
        mpos = []
        while len(mpos) < mines:
            p = (random.randint(0, 4), random.randint(0, 4))
            if p not in mpos:
                mpos.append(p)
        
        self.games[uid] = {
            'bet': bet,
            'field': field,
            'mines': mpos,
            'count': mines,
            'opened': [],
            'mult': 1.0,
            'mults': self.mults(mines),
            'won': 0,
            'bal': new_balance
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid, r, c):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if (r, c) in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        if (r, c) in g['mines']:
            for rr, cc in g['mines']:
                g['field'][rr][cc] = '💣'
            g['field'][r][c] = '💥'
            
            opened = len(g['opened'])
            field_copy = [row[:] for row in g['field']]
            bet = g['bet']
            
            user = self.db.get(uid)
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            
            del self.games[uid]
            
            return {
                'ok': True, 'over': True, 'win': False,
                'field': field_copy, 'opened': opened, 'bet': bet
            }
        
        g['opened'].append((r, c))
        g['field'][r][c] = '💰'
        opened = len(g['opened'])
        g['mult'] = g['mults'][opened - 1] if opened - 1 < len(g['mults']) else 2.5
        g['won'] = int(g['bet'] * g['mult'])
        
        if opened >= 25 - g['count']:
            user = self.db.get(uid)
            final_win = g['won']
            new_bal = g['bal'] + final_win
            self.db.update(uid, balance=new_bal,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            
            for rr, cc in g['mines']:
                g['field'][rr][cc] = '💣'
            
            field_copy = [row[:] for row in g['field']]
            mult = g['mult']
            
            del self.games[uid]
            
            return {
                'ok': True, 'over': True, 'win': True,
                'field': field_copy, 'opened': opened, 'won': final_win,
                'balance': new_bal, 'mult': mult
            }
        
        return {
            'ok': True, 'over': False, 'field': g['field'],
            'opened': opened, 'mult': g['mult'], 'won': g['won'],
            'max': 25 - g['count']
        }
    
    def cashout(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if not g['opened']:
            return {'ok': False, 'msg': '❌ Сначала открой клетку'}
        
        user = self.db.get(uid)
        final_win = g['won']
        new_bal = g['bal'] + final_win
        self.db.update(uid, balance=new_bal,
                      games_played=user.get('games_played', 0) + 1,
                      wins=user.get('wins', 0) + 1)
        
        for rr, cc in g['mines']:
            g['field'][rr][cc] = '💣'
        
        field = [row[:] for row in g['field']]
        won = final_win
        opened = len(g['opened'])
        mult = g['mult']
        
        del self.games[uid]
        
        return {
            'ok': True, 'won': won, 'balance': new_bal,
            'field': field, 'opened': opened, 'mult': mult
        }
    
    def cancel_game(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        user = self.db.get(uid)
        new_bal = user['balance'] + g['bet']
        self.db.update(uid, balance=new_bal)
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} {CURRENCY} возвращено'}
    
    def kb(self, uid, field, active=True):
        kb = []
        for i in range(5):
            row = []
            for j in range(5):
                if field[i][j] in ['💰', '💣', '💥']:
                    row.append(InlineKeyboardButton(text=field[i][j], callback_data="ignore"))
                else:
                    row.append(InlineKeyboardButton(text="❓" if active else "⬛", callback_data=f"mines_{uid}_{i}_{j}"))
            kb.append(row)
        if active:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"cashout_{uid}")])
        kb.append([InlineKeyboardButton(text="🎮 Новая", callback_data="mines_new")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

# ===================== ОСНОВНОЙ КЛАСС =====================
class BotCore:
    def __init__(self):
        self.db = UserDB()
        self.crash = CrashGame(self.db)
        self.mines = Mines(self.db)
        self.deposits = RequestDB(DEPOSIT_FILE)
        self.withdraws = RequestDB(WITHDRAW_FILE)
    
    def parse_bet(self, text, bal=None):
        if not text:
            return 0
        text = text.lower().strip()
        if text in ['всё', 'все'] and bal is not None:
            return bal
        m = re.match(r'^(\d+(?:\.\d+)?)(к+)$', text)
        if m:
            n, k = float(m[1]), len(m[2])
            return int(n * [1000, 1000000, 1000000000][min(k - 1, 2)])
        try:
            return int(text)
        except:
            return 0
    
    def is_admin(self, uid):
        return uid in ADMIN_IDS

core = BotCore()

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def fmt(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}ккк"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}кк"
    if n >= 1000:
        return f"{n / 1000:.1f}к"
    return str(n)

def is_admin(uid):
    return core.is_admin(uid)

# ===================== ОБРАБОТЧИКИ КОМАНД =====================

async def cmd_start(msg: Message):
    uid = msg.from_user.id
    user = core.db.get(uid)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Краш", callback_data="menu_crash"),
         InlineKeyboardButton(text="💣 Мины", callback_data="menu_mines")],
        [InlineKeyboardButton(text="💰 Кошелёк", callback_data="menu_wallet"),
         InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton(text="🏆 Топ", callback_data="menu_top")]
    ])
    
    await msg.reply(
        f"🎰 Добро пожаловать в DeathGmp!\n\n"
        f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n"
        f"🎮 Игр: {user.get('games_played', 0)} | 🏆 Побед: {user.get('wins', 0)}\n\n"
        f"Выберите действие:",
        reply_markup=kb
    )

async def cmd_balance(msg: Message):
    user = core.db.get(msg.from_user.id)
    await msg.reply(f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}")

async def cmd_profile(msg: Message):
    uid = msg.from_user.id
    user = core.db.get(uid)
    games = user.get('games_played', 0)
    wins = user.get('wins', 0)
    win_rate = (wins / games * 100) if games > 0 else 0
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])
    
    await msg.reply(
        f"👤 ПРОФИЛЬ\n\n"
        f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n"
        f"🎮 Игр: {games}\n"
        f"🏆 Побед: {wins}\n"
        f"⭐ Винрейт: {win_rate:.1f}%\n"
        f"📥 Всего депозитов: {fmt(user.get('total_deposited', 0))} {CURRENCY}\n"
        f"📤 Всего выводов: {fmt(user.get('total_withdrawn', 0))} {CURRENCY}",
        reply_markup=kb
    )

async def cmd_top(msg: Message):
    top = core.db.top(limit=10)
    if not top:
        await msg.reply("📊 Нет игроков")
        return
    
    text = "🏆 ТОП ИГРОКОВ\n\n"
    for i, (uid, u) in enumerate(top, 1):
        try:
            chat = await msg.bot.get_chat(int(uid))
            name = chat.first_name
        except:
            name = f"ID {uid[-4:]}"
        
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — {fmt(u.get('balance', 0))} {CURRENCY}\n"
    
    await msg.reply(text)

# ===================== КОМАНДЫ ДЕПОЗИТА И ВЫВОДА =====================

async def cmd_deposit(msg: Message, state: FSMContext, command: CommandObject = None):
    if command and command.args:
        amount = core.parse_bet(command.args)
        if amount <= 0:
            await msg.reply(f"❌ Неверная сумма. Используйте: депозит [сумма]")
            return
        
        await process_deposit(msg, amount)
    else:
        await state.set_state(DepositStates.waiting_amount)
        await msg.reply(
            f"💰 ДЕПОЗИТ\n\n"
            f"Введите сумму в {CURRENCY}, которую хотите пополнить:\n\n"
            f"Форматы: 1000, 5к, 50к, 1кк\n\n"
            f"Для отмены введите /cancel"
        )

async def process_deposit(msg: Message, amount: int):
    uid = msg.from_user.id
    username = msg.from_user.username or msg.from_user.full_name
    
    # Создаём заявку
    request = core.deposits.create(uid, username, amount)
    
    await msg.reply(
        f"✅ ЗАЯВКА НА ДЕПОЗИТ СОЗДАНА!\n\n"
        f"📋 Номер заявки: #{request['id']}\n"
        f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
        f"⏳ Статус: Ожидает подтверждения\n\n"
        f"⏰ Ожидайте, администратор подтвердит заявку в ближайшее время."
    )
    
    # Уведомляем всех админов
    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"deposit_approve_{request['id']}"),
             InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deposit_reject_{request['id']}")]
        ])
        
        try:
            await msg.bot.send_message(
                admin_id,
                f"📥 НОВАЯ ЗАЯВКА НА ДЕПОЗИТ\n\n"
                f"👤 Пользователь: {username}\n"
                f"🆔 ID: {uid}\n"
                f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
                f"📋 Заявка: #{request['id']}\n"
                f"🕐 Создана: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}",
                reply_markup=kb
            )
        except:
            pass

async def cmd_withdraw(msg: Message, state: FSMContext, command: CommandObject = None):
    if command and command.args:
        args = command.args.split()
        if len(args) >= 2:
            amount = core.parse_bet(args[0])
            address = ' '.join(args[1:])
            if amount <= 0:
                await msg.reply(f"❌ Неверная сумма. Используйте: вывод [сумма] [адрес]")
                return
            
            await process_withdraw(msg, amount, address)
            return
        else:
            await msg.reply(f"❌ Неверный формат. Используйте: вывод [сумма] [адрес]\nПример: вывод 1000 GMP...")
            return
    
    await state.set_state(WithdrawStates.waiting_amount)
    await msg.reply(
        f"📤 ВЫВОД СРЕДСТВ\n\n"
        f"Введите сумму в {CURRENCY}, которую хотите вывести:\n\n"
        f"Форматы: 1000, 5к, 50к, 1кк\n\n"
        f"Для отмены введите /cancel"
    )

async def process_withdraw_amount(msg: Message, state: FSMContext):
    if msg.text.lower() == '/cancel':
        await state.clear()
        await msg.reply("❌ Операция отменена")
        return
    
    amount = core.parse_bet(msg.text)
    if amount <= 0:
        await msg.reply(f"❌ Неверная сумма. Попробуйте снова:")
        return
    
    user = core.db.get(msg.from_user.id)
    if user['balance'] < amount:
        await msg.reply(f"❌ Не хватает средств. Баланс: {fmt(user['balance'])} {CURRENCY}")
        return
    
    await state.update_data(withdraw_amount=amount)
    await state.set_state(WithdrawStates.waiting_address)
    await msg.reply(
        f"📤 ВЫВОД СРЕДСТВ\n\n"
        f"💰 Сумма: {fmt(amount)} {CURRENCY}\n\n"
        f"Введите адрес для вывода средств:"
    )

async def process_withdraw_address(msg: Message, state: FSMContext):
    if msg.text.lower() == '/cancel':
        await state.clear()
        await msg.reply("❌ Операция отменена")
        return
    
    address = msg.text.strip()
    if len(address) < 5:
        await msg.reply("❌ Слишком короткий адрес. Попробуйте снова:")
        return
    
    data = await state.get_data()
    amount = data.get('withdraw_amount')
    
    if not amount:
        await state.clear()
        await msg.reply("❌ Ошибка. Начните заново: /вывод")
        return
    
    await process_withdraw(msg, amount, address)
    await state.clear()

async def process_withdraw(msg: Message, amount: int, address: str):
    uid = msg.from_user.id
    username = msg.from_user.username or msg.from_user.full_name
    
    user = core.db.get(uid)
    if user['balance'] < amount:
        await msg.reply(f"❌ Не хватает средств. Баланс: {fmt(user['balance'])} {CURRENCY}")
        return
    
    # Создаём заявку
    request = core.withdraws.create(uid, username, amount, address)
    
    # Блокируем средства
    core.db.update(uid, balance=user['balance'] - amount)
    
    await msg.reply(
        f"✅ ЗАЯВКА НА ВЫВОД СОЗДАНА!\n\n"
        f"📋 Номер заявки: #{request['id']}\n"
        f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
        f"📍 Адрес: {address}\n"
        f"⏳ Статус: Ожидает подтверждения\n\n"
        f"⏰ Ожидайте, администратор подтвердит заявку в ближайшее время."
    )
    
    # Уведомляем всех админов
    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"withdraw_approve_{request['id']}"),
             InlineKeyboardButton(text="❌ Отклонить", callback_data=f"withdraw_reject_{request['id']}")]
        ])
        
        try:
            await msg.bot.send_message(
                admin_id,
                f"📤 НОВАЯ ЗАЯВКА НА ВЫВОД\n\n"
                f"👤 Пользователь: {username}\n"
                f"🆔 ID: {uid}\n"
                f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
                f"📍 Адрес: {address}\n"
                f"📋 Заявка: #{request['id']}\n"
                f"🕐 Создана: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}",
                reply_markup=kb
            )
        except:
            pass

# ===================== КОМАНДЫ АДМИНА =====================

async def cmd_admin_panel(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Заявки на депозит", callback_data="admin_deposits"),
         InlineKeyboardButton(text="📤 Заявки на вывод", callback_data="admin_withdraws")],
        [InlineKeyboardButton(text="💰 Выдать баланс", callback_data="admin_give"),
         InlineKeyboardButton(text="💸 Забрать баланс", callback_data="admin_take")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])
    
    await msg.reply(
        f"👑 АДМИН-ПАНЕЛЬ\n\n"
        f"Выберите действие:",
        reply_markup=kb
    )

async def cmd_pending_deposits(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    pending = core.deposits.get_pending()
    if not pending:
        await msg.reply("📭 Нет заявок на депозит")
        return
    
    text = "📥 ЗАЯВКИ НА ДЕПОЗИТ (ожидают)\n\n"
    kb = []
    
    for r in pending:
        text += f"#{r['id']} | {r['username']} | {fmt(r['amount'])} {CURRENCY}\n"
        kb.append([
            InlineKeyboardButton(text=f"✅ #{r['id']}", callback_data=f"deposit_approve_{r['id']}"),
            InlineKeyboardButton(text=f"❌ #{r['id']}", callback_data=f"deposit_reject_{r['id']}")
        ])
    
    await msg.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

async def cmd_pending_withdraws(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    pending = core.withdraws.get_pending()
    if not pending:
        await msg.reply("📭 Нет заявок на вывод")
        return
    
    text = "📤 ЗАЯВКИ НА ВЫВОД (ожидают)\n\n"
    kb = []
    
    for r in pending:
        text += f"#{r['id']} | {r['username']} | {fmt(r['amount'])} {CURRENCY}\n"
        text += f"📍 {r['address']}\n\n"
        kb.append([
            InlineKeyboardButton(text=f"✅ #{r['id']}", callback_data=f"withdraw_approve_{r['id']}"),
            InlineKeyboardButton(text=f"❌ #{r['id']}", callback_data=f"withdraw_reject_{r['id']}")
        ])
    
    await msg.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

async def cmd_my_requests(msg: Message):
    uid = msg.from_user.id
    
    deposits = core.deposits.get_by_user(uid)
    withdraws = core.withdraws.get_by_user(uid)
    
    if not deposits and not withdraws:
        await msg.reply("📭 У вас нет заявок")
        return
    
    text = "📋 МОИ ЗАЯВКИ\n\n"
    
    if deposits:
        text += "📥 ДЕПОЗИТЫ:\n"
        for r in deposits[-5:]:
            status = "✅ Одобрен" if r['status'] == 'approved' else "❌ Отклонён" if r['status'] == 'rejected' else "⏳ Ожидает"
            text += f"#{r['id']} | {fmt(r['amount'])} {CURRENCY} | {status}\n"
        text += "\n"
    
    if withdraws:
        text += "📤 ВЫВОДЫ:\n"
        for r in withdraws[-5:]:
            status = "✅ Одобрен" if r['status'] == 'approved' else "❌ Отклонён" if r['status'] == 'rejected' else "⏳ Ожидает"
            text += f"#{r['id']} | {fmt(r['amount'])} {CURRENCY} | {status}\n"
    
    await msg.reply(text)

# ===================== КРАШ КОМАНДА =====================

async def cmd_crash(msg: Message, command: CommandObject):
    u = core.db.get(msg.from_user.id)
    
    if not command.args:
        await msg.reply(
            "🚀 КРАШ\n\n"
            "краш [ставка] [множитель]\n"
            "Пример: краш 1000 2\n\n"
            "Множитель от 1.1 до 100"
        )
        return
    
    args = command.args.split()
    if len(args) != 2:
        await msg.reply("❌ Краш [ставка] [множитель]\nПример: краш 1000 2")
        return
    
    bet = core.parse_bet(args[0], u['balance'])
    try:
        target = float(args[1].replace(',', '.'))
    except:
        await msg.reply("❌ Неверный множитель")
        return
    
    if bet <= 0 or bet > u['balance']:
        await msg.reply(f"❌ Неверная ставка, баланс {fmt(u['balance'])} {CURRENCY}")
        return
    
    if target < 1.1 or target > 100:
        await msg.reply("❌ Множитель от 1.1 до 100")
        return
    
    res = core.crash.start(msg.from_user.id, bet, target)
    
    if res['win']:
        await msg.reply(
            f"🚀 РАКЕТА УЛЕТЕЛА ДО x{res['crash']}!\n"
            f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY}\n"
            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
        )
    else:
        await msg.reply(
            f"💥 РАКЕТА ВЗОРВАЛАСЬ НА x{res['crash']}\n"
            f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
        )

# ===================== МИНЫ КОМАНДА =====================

async def cmd_mines(msg: Message, command: CommandObject):
    u = core.db.get(msg.from_user.id)
    
    if not command.args:
        await msg.reply(
            "💣 МИНЫ\n\n"
            "мины [ставка] [количество мин]\n"
            "Пример: мины 1000 3\n\n"
            "Мин от 1 до 6"
        )
        return
    
    args = command.args.split()
    if len(args) < 1:
        await msg.reply("❌ мины [ставка] [мин]")
        return
    
    bet = core.parse_bet(args[0], u['balance'])
    mines = int(args[1]) if len(args) > 1 else 1
    
    if bet <= 0 or bet > u['balance']:
        await msg.reply(f"❌ Неверная ставка, баланс {fmt(u['balance'])} {CURRENCY}")
        return
    
    if mines < 1 or mines > 6:
        await msg.reply("❌ Мин от 1 до 6")
        return
    
    res = core.mines.start(msg.from_user.id, bet, mines)
    if not res['ok']:
        await msg.reply(res['msg'])
        return
    
    await msg.reply(
        f"💣 МИНЫ | 💣 {mines} мин\n"
        f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
        f"📈 Множитель x1.0\n\n"
        f"Выберите клетку:",
        reply_markup=core.mines.kb(msg.from_user.id, res['data']['field'])
    )

async def cmd_cancel_game(msg: Message):
    uid = msg.from_user.id
    cancelled = False
    msg_text = ""
    
    if uid in core.crash.games:
        res = core.crash.cancel_game(uid)
        if res['ok']:
            msg_text += res['msg'] + "\n"
            cancelled = True
    
    if uid in core.mines.games:
        res = core.mines.cancel_game(uid)
        if res['ok']:
            msg_text += res['msg'] + "\n"
            cancelled = True
    
    if cancelled:
        await msg.reply(msg_text.strip())
    else:
        await msg.reply("❌ Нет активной игры")

# ===================== КОЛБЭКИ =====================
async def callback_handler(cb: CallbackQuery, state: FSMContext):
    data = cb.data
    uid = cb.from_user.id
    
    try:
        # ===== МЕНЮ =====
        if data == "back_to_main":
            await cmd_start(cb.message)
            await cb.answer()
            return
        
        if data == "menu_crash":
            user = core.db.get(uid)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="1.5x", callback_data="crash_1.5"),
                 InlineKeyboardButton(text="2x", callback_data="crash_2"),
                 InlineKeyboardButton(text="3x", callback_data="crash_3")],
                [InlineKeyboardButton(text="5x", callback_data="crash_5"),
                 InlineKeyboardButton(text="10x", callback_data="crash_10"),
                 InlineKeyboardButton(text="20x", callback_data="crash_20")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            
            await cb.message.edit_text(
                f"🚀 КРАШ\n\n"
                f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n\n"
                f"Выберите множитель:",
                reply_markup=kb
            )
            await cb.answer()
            return
        
        if data == "menu_mines":
            user = core.db.get(uid)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💣 1 мина", callback_data="mines_set_1"),
                 InlineKeyboardButton(text="💣💣 2 мины", callback_data="mines_set_2")],
                [InlineKeyboardButton(text="💣💣💣 3 мины", callback_data="mines_set_3"),
                 InlineKeyboardButton(text="💣💣💣💣 4 мины", callback_data="mines_set_4")],
                [InlineKeyboardButton(text="💣💣💣💣💣 5 мин", callback_data="mines_set_5"),
                 InlineKeyboardButton(text="💣💣💣💣💣💣 6 мин", callback_data="mines_set_6")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            
            await cb.message.edit_text(
                f"💣 МИНЫ\n\n"
                f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n\n"
                f"Выберите количество мин:",
                reply_markup=kb
            )
            await cb.answer()
            return
        
        if data == "menu_profile":
            await cmd_profile(cb.message)
            await cb.answer()
            return
        
        if data == "menu_top":
            await cmd_top(cb.message)
            await cb.answer()
            return
        
        if data == "menu_wallet":
            user = core.db.get(uid)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Пополнить", callback_data="wallet_deposit"),
                 InlineKeyboardButton(text="📤 Вывести", callback_data="wallet_withdraw")],
                [InlineKeyboardButton(text="📋 Мои заявки", callback_data="wallet_requests"),
                 InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            
            await cb.message.edit_text(
                f"💰 КОШЕЛЁК\n\n"
                f"Баланс: {fmt(user['balance'])} {CURRENCY}\n"
                f"Всего депозитов: {fmt(user.get('total_deposited', 0))} {CURRENCY}\n"
                f"Всего выводов: {fmt(user.get('total_withdrawn', 0))} {CURRENCY}",
                reply_markup=kb
            )
            await cb.answer()
            return
        
        if data == "wallet_deposit":
            await cmd_deposit(cb.message, state, None)
            await cb.answer()
            return
        
        if data == "wallet_withdraw":
            await cmd_withdraw(cb.message, state, None)
            await cb.answer()
            return
        
        if data == "wallet_requests":
            await cmd_my_requests(cb.message)
            await cb.answer()
            return
        
        # ===== АДМИН-ПАНЕЛЬ =====
        if data == "admin_panel":
            await cmd_admin_panel(cb.message)
            await cb.answer()
            return
        
        if data == "admin_deposits":
            await cmd_pending_deposits(cb.message)
            await cb.answer()
            return
        
        if data == "admin_withdraws":
            await cmd_pending_withdraws(cb.message)
            await cb.answer()
            return
        
        # ===== КРАШ СТАВКИ =====
        if data.startswith("crash_"):
            parts = data.split('_')
            if len(parts) == 2:
                target = float(parts[1])
                user = core.db.get(uid)
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="100", callback_data=f"crash_bet_100_{target}"),
                     InlineKeyboardButton(text="500", callback_data=f"crash_bet_500_{target}"),
                     InlineKeyboardButton(text="1к", callback_data=f"crash_bet_1000_{target}")],
                    [InlineKeyboardButton(text="5к", callback_data=f"crash_bet_5000_{target}"),
                     InlineKeyboardButton(text="10к", callback_data=f"crash_bet_10000_{target}"),
                     InlineKeyboardButton(text="50к", callback_data=f"crash_bet_50000_{target}")],
                    [InlineKeyboardButton(text="💰 Всё", callback_data=f"crash_bet_all_{target}"),
                     InlineKeyboardButton(text="◀️ Назад", callback_data="menu_crash")]
                ])
                
                await cb.message.edit_text(
                    f"🚀 КРАШ | x{target}\n"
                    f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n\n"
                    f"Выберите ставку:",
                    reply_markup=kb
                )
                await cb.answer()
                return
        
        if data.startswith("crash_bet_"):
            parts = data.split('_')
            if len(parts) >= 4:
                bet_str = parts[2]
                target = float(parts[3])
                user = core.db.get(uid)
                
                if bet_str == "all":
                    bet = user['balance']
                else:
                    try:
                        bet = int(bet_str)
                    except:
                        await cb.answer("❌ Ошибка", show_alert=True)
                        return
                
                if bet <= 0 or bet > user['balance']:
                    await cb.answer(f"❌ Неверная ставка, баланс {fmt(user['balance'])} {CURRENCY}", show_alert=True)
                    return
                
                res = core.crash.start(uid, bet, target)
                
                if res['ok']:
                    if res['win']:
                        await cb.message.edit_text(
                            f"🚀 РАКЕТА УЛЕТЕЛА ДО x{res['crash']}!\n"
                            f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY}\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                        )
                    else:
                        await cb.message.edit_text(
                            f"💥 РАКЕТА ВЗОРВАЛАСЬ НА x{res['crash']}\n"
                            f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                        )
                else:
                    await cb.message.edit_text(res['msg'])
                await cb.answer()
                return
        
        # ===== МИНЫ СТАВКИ =====
        if data.startswith("mines_set_"):
            mines = int(data.split('_')[2])
            user = core.db.get(uid)
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="100", callback_data=f"mines_bet_100_{mines}"),
                 InlineKeyboardButton(text="500", callback_data=f"mines_bet_500_{mines}"),
                 InlineKeyboardButton(text="1к", callback_data=f"mines_bet_1000_{mines}")],
                [InlineKeyboardButton(text="5к", callback_data=f"mines_bet_5000_{mines}"),
                 InlineKeyboardButton(text="10к", callback_data=f"mines_bet_10000_{mines}"),
                 InlineKeyboardButton(text="50к", callback_data=f"mines_bet_50000_{mines}")],
                [InlineKeyboardButton(text="💰 Всё", callback_data=f"mines_bet_all_{mines}"),
                 InlineKeyboardButton(text="◀️ Назад", callback_data="menu_mines")]
            ])
            
            await cb.message.edit_text(
                f"💣 МИНЫ | {mines} мин\n"
                f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n\n"
                f"Выберите ставку:",
                reply_markup=kb
            )
            await cb.answer()
            return
        
        if data.startswith("mines_bet_"):
            parts = data.split('_')
            if len(parts) >= 4:
                bet_str = parts[2]
                mines = int(parts[3])
                user = core.db.get(uid)
                
                if bet_str == "all":
                    bet = user['balance']
                else:
                    try:
                        bet = int(bet_str)
                    except:
                        await cb.answer("❌ Ошибка", show_alert=True)
                        return
                
                if bet <= 0 or bet > user['balance']:
                    await cb.answer(f"❌ Неверная ставка, баланс {fmt(user['balance'])} {CURRENCY}", show_alert=True)
                    return
                
                res = core.mines.start(uid, bet, mines)
                if not res['ok']:
                    await cb.message.edit_text(res['msg'])
                    await cb.answer()
                    return
                
                await cb.message.edit_text(
                    f"💣 МИНЫ | 💣 {mines} мин\n"
                    f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
                    f"📈 Множитель x1.0\n\n"
                    f"Выберите клетку:",
                    reply_markup=core.mines.kb(uid, res['data']['field'])
                )
                await cb.answer()
                return
        
        # ===== МИНЫ КЛЕТКИ =====
        if data.startswith("mines_"):
            parts = data.split('_')
            if len(parts) == 4:
                user_id, r, c = int(parts[1]), int(parts[2]), int(parts[3])
                
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                
                res = core.mines.open(user_id, r, c)
                
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                
                if res.get('over'):
                    if res.get('win'):
                        await cb.message.edit_text(
                            f"🏆 ПОБЕДА!\n"
                            f"+{fmt(res['won'])} {CURRENCY}\n"
                            f"📈 x{res['mult']:.2f}\n"
                            f"🎯 Открыто {res['opened']} клеток\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}",
                            reply_markup=core.mines.kb(user_id, res['field'], False)
                        )
                    else:
                        await cb.message.edit_text(
                            f"💥 БУМ! МИНА!\n"
                            f"❌ Потеряно: -{fmt(res['bet'])} {CURRENCY}\n"
                            f"🎯 Открыто {res['opened']} клеток",
                            reply_markup=core.mines.kb(user_id, res['field'], False)
                        )
                else:
                    game = core.mines.games.get(user_id)
                    if game:
                        await cb.message.edit_text(
                            f"💣 МИНЫ | 💣 {game['count']} мин\n"
                            f"💰 Ставка {fmt(game['bet'])} {CURRENCY}\n"
                            f"🎯 Открыто {res['opened']}/{res['max']} | 📈 x{res['mult']:.2f}\n"
                            f"💎 Выигрыш: {fmt(res['won'])} {CURRENCY}\n\n"
                            f"Выберите клетку:",
                            reply_markup=core.mines.kb(user_id, res['field'])
                        )
                await cb.answer()
                return
        
        # ===== МИНЫ ЗАБРАТЬ =====
        if data.startswith("cashout_"):
            user_id = int(data.split('_')[1])
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return
            
            res = core.mines.cashout(user_id)
            if not res['ok']:
                await cb.answer(res['msg'], show_alert=True)
                return
            
            await cb.message.edit_text(
                f"🏆 ВЫИГРЫШ ЗАБРАН!\n"
                f"+{fmt(res['won'])} {CURRENCY}\n"
                f"📈 x{res['mult']:.2f}\n"
                f"🎯 Открыто {res['opened']} клеток\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}",
                reply_markup=core.mines.kb(user_id, res['field'], False)
            )
            await cb.answer()
            return
        
        if data == "mines_new":
            await cmd_mines(cb.message, CommandObject(args="1000 3"))
            await cb.answer()
            return
        
        # ===== ЗАЯВКИ (админ) =====
        if data.startswith("deposit_approve_"):
            if not is_admin(uid):
                await cb.answer("❌ Только для админов", show_alert=True)
                return
            
            request_id = int(data.split('_')[2])
            request = core.deposits.get_by_id(request_id)
            
            if not request:
                await cb.answer("❌ Заявка не найдена", show_alert=True)
                return
            
            if request['status'] != 'pending':
                await cb.answer(f"❌ Заявка уже {request['status']}", show_alert=True)
                return
            
            # Одобряем
            core.deposits.approve(request_id, uid)
            
            # Начисляем деньги
            user = core.db.get(request['user_id'])
            core.db.update(request['user_id'], 
                          balance=user['balance'] + request['amount'],
                          total_deposited=user.get('total_deposited', 0) + request['amount'])
            
            await cb.message.edit_text(f"✅ Заявка #{request_id} одобрена! Пользователю начислено {fmt(request['amount'])} {CURRENCY}")
            
            # Уведомляем пользователя
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"✅ ВАШ ДЕПОЗИТ ОДОБРЕН!\n\n"
                    f"💰 Сумма: +{fmt(request['amount'])} {CURRENCY}\n"
                    f"📋 Заявка: #{request_id}\n"
                    f"💳 Баланс: {fmt(user['balance'] + request['amount'])} {CURRENCY}"
                )
            except:
                pass
            
            await cb.answer()
            return
        
        if data.startswith("deposit_reject_"):
            if not is_admin(uid):
                await cb.answer("❌ Только для админов", show_alert=True)
                return
            
            request_id = int(data.split('_')[2])
            request = core.deposits.get_by_id(request_id)
            
            if not request:
                await cb.answer("❌ Заявка не найдена", show_alert=True)
                return
            
            if request['status'] != 'pending':
                await cb.answer(f"❌ Заявка уже {request['status']}", show_alert=True)
                return
            
            core.deposits.reject(request_id, uid)
            
            await cb.message.edit_text(f"❌ Заявка #{request_id} отклонена")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"❌ ВАШ ДЕПОЗИТ ОТКЛОНЁН\n\n"
                    f"📋 Заявка: #{request_id}\n"
                    f"💰 Сумма: {fmt(request['amount'])} {CURRENCY}\n\n"
                    f"Свяжитесь с администратором для уточнения."
                )
            except:
                pass
            
            await cb.answer()
            return
        
        if data.startswith("withdraw_approve_"):
            if not is_admin(uid):
                await cb.answer("❌ Только для админов", show_alert=True)
                return
            
            request_id = int(data.split('_')[2])
            request = core.withdraws.get_by_id(request_id)
            
            if not request:
                await cb.answer("❌ Заявка не найдена", show_alert=True)
                return
            
            if request['status'] != 'pending':
                await cb.answer(f"❌ Заявка уже {request['status']}", show_alert=True)
                return
            
            core.withdraws.approve(request_id, uid)
            
            await cb.message.edit_text(f"✅ Заявка #{request_id} одобрена!")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"✅ ВАШ ВЫВОД ОДОБРЕН!\n\n"
                    f"💰 Сумма: -{fmt(request['amount'])} {CURRENCY}\n"
                    f"📍 Адрес: {request['address']}\n"
                    f"📋 Заявка: #{request_id}\n\n"
                    f"Средства отправлены на указанный адрес."
                )
            except:
                pass
            
            await cb.answer()
            return
        
        if data.startswith("withdraw_reject_"):
            if not is_admin(uid):
                await cb.answer("❌ Только для админов", show_alert=True)
                return
            
            request_id = int(data.split('_')[2])
            request = core.withdraws.get_by_id(request_id)
            
            if not request:
                await cb.answer("❌ Заявка не найдена", show_alert=True)
                return
            
            if request['status'] != 'pending':
                await cb.answer(f"❌ Заявка уже {request['status']}", show_alert=True)
                return
            
            # Возвращаем деньги
            user = core.db.get(request['user_id'])
            core.db.update(request['user_id'], balance=user['balance'] + request['amount'])
            
            core.withdraws.reject(request_id, uid)
            
            await cb.message.edit_text(f"❌ Заявка #{request_id} отклонена, средства возвращены")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"❌ ВАШ ВЫВОД ОТКЛОНЁН\n\n"
                    f"📋 Заявка: #{request_id}\n"
                    f"💰 Сумма: {fmt(request['amount'])} {CURRENCY}\n"
                    f"📍 Адрес: {request['address']}\n\n"
                    f"Средства возвращены на баланс.\n"
                    f"Свяжитесь с администратором для уточнения."
                )
            except:
                pass
            
            await cb.answer()
            return
        
        if data == "ignore":
            await cb.answer()
            return
        
        await cb.answer()
    except Exception as e:
        print(f"Ошибка: {e}")
        await cb.answer("❌ Ошибка", show_alert=True)

# ===================== ОБРАБОТЧИК СООБЩЕНИЙ =====================

async def handle_text(msg: Message, state: FSMContext):
    text = msg.text.lower().strip()
    
    if text == '/cancel':
        current_state = await state.get_state()
        if current_state:
            await state.clear()
            await msg.reply("❌ Операция отменена")
        return
    
    current_state = await state.get_state()
    
    if current_state == DepositStates.waiting_amount:
        amount = core.parse_bet(text)
        if amount <= 0:
            await msg.reply(f"❌ Неверная сумма. Введите число:")
            return
        await process_deposit(msg, amount)
        await state.clear()
        return
    
    if current_state == WithdrawStates.waiting_amount:
        amount = core.parse_bet(text)
        if amount <= 0:
            await msg.reply(f"❌ Неверная сумма. Введите число:")
            return
        user = core.db.get(msg.from_user.id)
        if user['balance'] < amount:
            await msg.reply(f"❌ Не хватает средств. Баланс: {fmt(user['balance'])} {CURRENCY}")
            return
        await state.update_data(withdraw_amount=amount)
        await state.set_state(WithdrawStates.waiting_address)
        await msg.reply(
            f"📤 ВЫВОД СРЕДСТВ\n\n"
            f"💰 Сумма: {fmt(amount)} {CURRENCY}\n\n"
            f"Введите адрес для вывода средств:"
        )
        return
    
    if current_state == WithdrawStates.waiting_address:
        address = text.strip()
        if len(address) < 5:
            await msg.reply("❌ Слишком короткий адрес. Попробуйте снова:")
            return
        data = await state.get_data()
        amount = data.get('withdraw_amount')
        if not amount:
            await state.clear()
            await msg.reply("❌ Ошибка. Начните заново: /вывод")
            return
        await process_withdraw(msg, amount, address)
        await state.clear()
        return

# ===================== ЗАПУСК =====================

async def main():
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.get_me()
        print("✅ Бот DeathGmp запущен!")
        print(f"👑 Админы: {ADMIN_IDS}")
        print(f"💰 Валюта: {CURRENCY}")
        print(f"📊 Начальный баланс: {START_BALANCE}")
        
        dp = Dispatcher(storage=MemoryStorage())
        
        # Команды
        dp.message.register(cmd_start, Command("start"))
        dp.message.register(cmd_balance, Command("balance"))
        dp.message.register(cmd_balance, Command("б"))
        dp.message.register(cmd_profile, Command("profile"))
        dp.message.register(cmd_profile, Command("профиль"))
        dp.message.register(cmd_top, Command("top"))
        dp.message.register(cmd_top, Command("топ"))
        
        dp.message.register(cmd_deposit, Command("deposit"))
        dp.message.register(cmd_deposit, Command("депозит"))
        dp.message.register(cmd_withdraw, Command("withdraw"))
        dp.message.register(cmd_withdraw, Command("вывод"))
        
        dp.message.register(cmd_my_requests, Command("my_requests"))
        dp.message.register(cmd_my_requests, Command("мои_заявки"))
        
        dp.message.register(cmd_crash, Command("crash"))
        dp.message.register(cmd_crash, Command("краш"))
        dp.message.register(cmd_mines, Command("mines"))
        dp.message.register(cmd_mines, Command("мины"))
        dp.message.register(cmd_cancel_game, Command("cancel"))
        dp.message.register(cmd_cancel_game, Command("отмена"))
        
        dp.message.register(cmd_admin_panel, Command("admin"))
        dp.message.register(cmd_admin_panel, Command("админ"))
        dp.message.register(cmd_pending_deposits, Command("deposits"))
        dp.message.register(cmd_pending_deposits, Command("депозиты"))
        dp.message.register(cmd_pending_withdraws, Command("withdraws"))
        dp.message.register(cmd_pending_withdraws, Command("выводы"))
        
        dp.message.register(handle_text, F.text)
        dp.callback_query.register(callback_handler)
        
        await dp.start_polling(bot)
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
