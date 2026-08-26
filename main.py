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
BOT_TOKEN = "8997314334:AAGiU4-R2Im_xcVBqrFxaulltbQcXT7kxiU"
ADMIN_IDS = [6539341659, 8705040410]
CURRENCY = "GMP"
DATABASE_FILE = "deathgmp_data.json"
WITHDRAW_FILE = "withdraw_requests.json"
DEPOSIT_FILE = "deposit_requests.json"
START_BALANCE = 0
WIN_CHANCE = 0.35  # 35% шанс выигрыша для всех игр
MAX_CRASH_BET = 10000000  # Максимальная ставка в краше (10кк)
MAX_CRASH_MULTIPLIER = 100  # Максимальный множитель

logging.basicConfig(level=logging.INFO)

# ===================== СОСТОЯНИЯ =====================
class DepositStates(StatesGroup):
    waiting_amount = State()
    waiting_confirmation = State()

class BlackjackStates(StatesGroup):
    waiting_start = State()
    playing = State() 

class WithdrawStates(StatesGroup):
    waiting_amount = State()
    waiting_address = State()

class MailingStates(StatesGroup):
    waiting_text = State()

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
    
    def top_deposits(self, limit=10):
        data = self.db.read()
        users = sorted(data.items(), key=lambda x: x[1].get('total_deposited', 0), reverse=True)
        return users[:limit]
    
    def all_users(self):
        data = self.db.read()
        return data.items()

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
        
        # Проверка лимита ставки
        if bet > MAX_CRASH_BET:
            return {'ok': False, 'msg': f'❌ Максимальная ставка {fmt(MAX_CRASH_BET)} {CURRENCY}'}
        
        if target < 1.1 or target > MAX_CRASH_MULTIPLIER:
            return {'ok': False, 'msg': f'❌ Множитель от 1.1 до {MAX_CRASH_MULTIPLIER}'}
        
        new_balance = user['balance'] - bet
        self.db.update(uid, balance=new_balance)
        
        # Генерируем краш
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

# ===================== ИГРА МОНЕТКА =====================
class CoinGame:
    def __init__(self, db):
        self.db = db
    
    def play(self, uid, bet, choice):
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        self.db.update(uid, balance=user['balance'] - bet)
        
        if random.random() < WIN_CHANCE:
            win_amount = int(bet * 1.96)
            self.db.update(uid, balance=user['balance'] - bet + win_amount,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            result = random.choice(['орёл', 'решка'])
            return {'ok': True, 'win': True, 'result': result, 'amount': win_amount, 'balance': user['balance'] - bet + win_amount}
        else:
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            result = random.choice(['орёл', 'решка'])
            return {'ok': True, 'win': False, 'result': result, 'amount': bet, 'balance': user['balance'] - bet}

# ===================== ИГРА КУБИК (БЕЗ ШАНСА) =====================
class DiceGame:
    def __init__(self, db):
        self.db = db
        self.multipliers = {
            'number': 5.8,
            'even': 1.94,
            'odd': 1.94,
            'equal': 5.8,
            'over': 1.94,
            'under': 2.9
        }
    
    async def play(self, msg, uid, bet, bet_type, value=None):
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        self.db.update(uid, balance=user['balance'] - bet)
        
        # Отправляем Telegram Dice
        dice_msg = await msg.answer_dice(emoji='🎲')
        
        # Ждём 4.6 секунды (время анимации Dice)
        await asyncio.sleep(4.6)
        
        roll = dice_msg.dice.value
        
        win = False
        mult = 0
        
        # Проверяем правила игры
        if bet_type == 'number':
            if value is not None and roll == value:
                win = True
                mult = self.multipliers['number']
        elif bet_type == 'even':
            if roll in [2, 4, 6]:
                win = True
                mult = self.multipliers['even']
        elif bet_type == 'odd':
            if roll in [1, 3, 5]:
                win = True
                mult = self.multipliers['odd']
        elif bet_type == 'equal':
            if roll == 3:
                win = True
                mult = self.multipliers['equal']
        elif bet_type == 'over':  # больше 3
            if roll in [4, 5, 6]:
                win = True
                mult = self.multipliers['over']
        elif bet_type == 'under':  # меньше 3
            if roll in [1, 2]:
                win = True
                mult = self.multipliers['under']
        
        if win:
            win_amount = int(bet * mult)
            self.db.update(uid, balance=user['balance'] - bet + win_amount,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'roll': roll, 'amount': win_amount, 'mult': mult, 'balance': user['balance'] - bet + win_amount}
        else:
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            return {'ok': True, 'win': False, 'roll': roll, 'amount': bet, 'balance': user['balance'] - bet}

# ===================== ИГРА СЛОТЫ (ПОЛНОСТЬЮ ИЗ ПЕРВОГО КОДА) =====================
class SlotsGame:
    def __init__(self, db):
        self.db = db
    
    async def play(self, msg, uid, bet):
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        self.db.update(uid, balance=user['balance'] - bet)
        
        slots_msg = await msg.answer_dice(emoji='🎰')
        
        # Ждём анимацию
        await asyncio.sleep(3.6)
        
        slots_value = slots_msg.dice.value
        
        # Маппинг значений на эмодзи
        emoji_map = {
            1: '🍒',
            22: '🍋', 
            43: '🍊',
            64: '7️⃣'
        }
        
        other_emojis = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
        
        if slots_value == 64:
            emoji_combo = '7️⃣ 7️⃣ 7️⃣'
            mult = 10
            win = bet * mult
            self.db.update(uid, balance=user['balance'] - bet + win,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'value': slots_value, 'mult': mult, 'amount': win, 'balance': user['balance'] - bet + win, 'emoji_combo': emoji_combo}
        elif slots_value in [1, 22, 43]:
            emoji = emoji_map.get(slots_value, '🎰')
            emoji_combo = f"{emoji} {emoji} {emoji}"
            mult = 5
            win = bet * mult
            self.db.update(uid, balance=user['balance'] - bet + win,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'value': slots_value, 'mult': mult, 'amount': win, 'balance': user['balance'] - bet + win, 'emoji_combo': emoji_combo}
        else:
            random_combo = [random.choice(other_emojis) for _ in range(3)]
            emoji_combo = ' '.join(random_combo)
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            return {'ok': True, 'win': False, 'value': slots_value, 'amount': bet, 'balance': user['balance'] - bet, 'emoji_combo': emoji_combo}

# ===================== ИГРА БАШНЯ =====================
class TowerGame:
    def __init__(self, db):
        self.db = db
        self.games = {}
        self.base_mults = [1.2, 1.5, 2.0, 2.5, 3.2, 4.0, 5.0, 6.0, 7.0]
    
    def mults(self, mines):
        if mines == 1:
            return self.base_mults
        elif mines == 2:
            return [round(x * 1.4, 2) for x in self.base_mults]
        elif mines == 3:
            return [round(x * 1.8, 2) for x in self.base_mults]
        elif mines == 4:
            return [round(x * 2.2, 2) for x in self.base_mults]
        return self.base_mults
    
    def start(self, uid, bet, mines=1):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if mines < 1 or mines > 4:
            return {'ok': False, 'msg': '❌ Мин от 1 до 4'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        new_bal = user['balance'] - bet
        self.db.update(uid, balance=new_bal)
        
        row = {
            'cells': ['⬜'] * 5,
            'mines': random.sample(range(5), mines)
        }
        
        self.games[uid] = {
            'bet': bet,
            'mines': mines,
            'row': 0,
            'rows': [row],
            'opened': [],
            'mult': 1.0,
            'mults': self.mults(mines),
            'bal': new_bal,
            'won': 0,
            'total_rows': 9
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid, r, c):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if r != g['row']:
            return {'ok': False, 'msg': '❌ Можно открывать только текущий ряд'}
        
        if f"{r}_{c}" in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        row = g['rows'][r]
        
        if c in row['mines']:
            for i in range(5):
                row['cells'][i] = '💣' if i in row['mines'] else '⬛'
            row['cells'][c] = '💥'
            
            user = self.db.get(uid)
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'mine': True, 'bet': g['bet']}
        
        g['opened'].append(f"{r}_{c}")
        row['cells'][c] = '🟩'
        g['mult'] = g['mults'][r]
        g['won'] = int(g['bet'] * g['mult'])
        
        if r >= g['total_rows'] - 1:
            user = self.db.get(uid)
            final_win = g['won']
            new_bal = g['bal'] + final_win
            self.db.update(uid, balance=new_bal,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'won': final_win, 'mult': g['mult'], 'rows': r + 1, 'balance': new_bal}
        
        g['row'] += 1
        if len(g['rows']) <= g['row']:
            g['rows'].append({
                'cells': ['⬜'] * 5,
                'mines': random.sample(range(5), g['mines'])
            })
        
        return {'ok': True, 'over': False, 'row': r, 'col': c, 'next': g['row'], 'mult': g['mult'], 'won': g['won']}
    
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
        del self.games[uid]
        return {'ok': True, 'won': final_win, 'mult': g['mult'], 'rows': g['row'], 'balance': new_bal}
    
    def cancel_game(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        user = self.db.get(uid)
        new_bal = user['balance'] + g['bet']
        self.db.update(uid, balance=new_bal)
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} {CURRENCY} возвращено'}
    
    def kb(self, uid, g):
        kb = []
        for r in range(g['total_rows'] - 1, -1, -1):
            if r < len(g['rows']):
                row = g['rows'][r]
                btns = []
                if r > g['row']:
                    for c in range(5):
                        btns.append(InlineKeyboardButton(text="❓", callback_data="ignore"))
                elif r == g['row']:
                    for c in range(5):
                        if f"{r}_{c}" in g['opened']:
                            btns.append(InlineKeyboardButton(text="💰", callback_data="ignore"))
                        else:
                            btns.append(InlineKeyboardButton(text="❓", callback_data=f"tower_{uid}_{r}_{c}"))
                else:
                    for c in range(5):
                        if f"{r}_{c}" in g['opened']:
                            btns.append(InlineKeyboardButton(text="💰", callback_data="ignore"))
                        else:
                            btns.append(InlineKeyboardButton(text="❓", callback_data="ignore"))
                kb.append(btns)
            else:
                btns = [InlineKeyboardButton(text="⬛", callback_data="ignore") for _ in range(5)]
                kb.append(btns)
        
        if g['opened']:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"tower_cash_{uid}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

# ===================== ИГРА АЛМАЗЫ =====================
class DiamondsGame:
    def __init__(self, db):
        self.db = db
        self.games = {}
        self.base_mults = [1.3, 1.7, 2.2, 2.8, 3.5, 4.3, 5.2, 6.2, 7.3]
    
    def mults(self, mines):
        if mines == 1:
            return self.base_mults
        elif mines == 2:
            return [round(x * 1.5, 2) for x in self.base_mults]
        return self.base_mults
    
    def start(self, uid, bet, mines=1):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if mines < 1 or mines > 2:
            return {'ok': False, 'msg': '❌ Мин от 1 до 2'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        new_bal = user['balance'] - bet
        self.db.update(uid, balance=new_bal)
        
        row = {
            'cells': ['⬜'] * 3,
            'mines': random.sample(range(3), mines)
        }
        
        self.games[uid] = {
            'bet': bet,
            'mines': mines,
            'row': 0,
            'rows': [row],
            'opened': [],
            'mult': 1.0,
            'mults': self.mults(mines),
            'bal': new_bal,
            'won': 0
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid, r, c):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if r != g['row']:
            return {'ok': False, 'msg': '❌ Можно открывать только текущий ряд'}
        
        if f"{r}_{c}" in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        row = g['rows'][r]
        
        if c in row['mines']:
            for i in range(3):
                row['cells'][i] = '💣' if i in row['mines'] else '⬛'
            row['cells'][c] = '💥'
            
            user = self.db.get(uid)
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'mine': True, 'bet': g['bet']}
        
        g['opened'].append(f"{r}_{c}")
        row['cells'][c] = '💎'
        g['mult'] = g['mults'][r]
        g['won'] = int(g['bet'] * g['mult'])
        
        if r >= 8:
            user = self.db.get(uid)
            final_win = g['won']
            new_bal = g['bal'] + final_win
            self.db.update(uid, balance=new_bal,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'won': final_win, 'mult': g['mult'], 'rows': r + 1, 'balance': new_bal}
        
        g['row'] += 1
        if len(g['rows']) <= g['row']:
            g['rows'].append({
                'cells': ['⬜'] * 3,
                'mines': random.sample(range(3), g['mines'])
            })
        
        return {'ok': True, 'over': False, 'row': r, 'col': c, 'next': g['row'], 'mult': g['mult'], 'won': g['won']}
    
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
        del self.games[uid]
        return {'ok': True, 'won': final_win, 'mult': g['mult'], 'rows': g['row'], 'balance': new_bal}
    
    def cancel_game(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        user = self.db.get(uid)
        new_bal = user['balance'] + g['bet']
        self.db.update(uid, balance=new_bal)
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} {CURRENCY} возвращено'}
    
    def kb(self, uid, g):
        kb = []
        for r in range(len(g['rows'])):
            row = g['rows'][r]
            btns = []
            if r < g['row']:
                for c in range(3):
                    if f"{r}_{c}" in g['opened']:
                        btns.append(InlineKeyboardButton(text="💎", callback_data="ignore"))
                    else:
                        btns.append(InlineKeyboardButton(text="⬛", callback_data="ignore"))
            elif r == g['row']:
                for c in range(3):
                    btns.append(InlineKeyboardButton(text="❓", callback_data=f"diamonds_{uid}_{r}_{c}"))
            else:
                for c in range(3):
                    btns.append(InlineKeyboardButton(text="⬛", callback_data="ignore"))
            kb.append(btns)
        
        if g['opened']:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"diamonds_cash_{uid}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

# ===================== ИГРА ПИРАМИДА =====================
class PyramidGame:
    def __init__(self, db):
        self.db = db
        self.games = {}
        self.multipliers = {
            1: [1.7, 2.3, 3.1, 4.2, 5.7, 7.7, 10.4, 14.0, 18.9, 25.5, 34.4, 46.4],
            2: [1.5, 2.0, 2.7, 3.6, 4.9, 6.6, 8.9, 12.0, 16.2, 21.9, 29.6, 40.0],
            3: [1.31, 1.74, 2.32, 3.10, 4.13, 5.51, 7.34, 9.79, 13.05, 17.40, 23.20, 30.93]
        }
    
    def start(self, uid, bet, doors=3):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if doors < 1 or doors > 3:
            return {'ok': False, 'msg': '❌ Дверей от 1 до 3'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        new_bal = user['balance'] - bet
        self.db.update(uid, balance=new_bal)
        
        level = self.generate_level(doors)
        
        self.games[uid] = {
            'bet': bet,
            'doors': doors,
            'level': 0,
            'levels': [level],
            'opened': [],
            'mult': 1.0,
            'multipliers': self.multipliers[doors],
            'bal': new_bal,
            'won': 0
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def generate_level(self, doors):
        cells = ['🚪'] * 4
        safe_positions = random.sample(range(4), doors)
        return {'cells': cells, 'safe': safe_positions}
    
    def open(self, uid, level_idx, cell_idx):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if level_idx != g['level']:
            return {'ok': False, 'msg': '❌ Можно открывать только текущий уровень'}
        
        cell_key = f"{level_idx}_{cell_idx}"
        if cell_key in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        level = g['levels'][level_idx]
        
        if cell_idx not in level['safe']:
            for i in range(4):
                if i in level['safe']:
                    level['cells'][i] = '🚪'
                else:
                    level['cells'][i] = '💀'
            level['cells'][cell_idx] = '💥'
            
            user = self.db.get(uid)
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': False, 'level': level_idx + 1, 'bet': g['bet']}
        
        g['opened'].append(cell_key)
        level['cells'][cell_idx] = '✅'
        g['mult'] = g['multipliers'][level_idx]
        g['won'] = int(g['bet'] * g['mult'])
        
        if level_idx >= 11:
            user = self.db.get(uid)
            final_win = g['won']
            new_bal = g['bal'] + final_win
            self.db.update(uid, balance=new_bal,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'won': final_win, 'mult': g['mult'], 'level': level_idx + 1, 'balance': new_bal}
        
        g['level'] += 1
        if len(g['levels']) <= g['level']:
            g['levels'].append(self.generate_level(g['doors']))
        
        return {'ok': True, 'over': False, 'level': level_idx + 1, 'next': g['level'] + 1, 'mult': g['mult'], 'won': g['won'], 'max_level': 12}
    
    def cashout(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if g['level'] == 0:
            return {'ok': False, 'msg': '❌ Сначала открой дверь'}
        
        user = self.db.get(uid)
        final_win = g['won']
        new_bal = g['bal'] + final_win
        self.db.update(uid, balance=new_bal,
                      games_played=user.get('games_played', 0) + 1,
                      wins=user.get('wins', 0) + 1)
        del self.games[uid]
        return {'ok': True, 'won': final_win, 'mult': g['mult'], 'level': g['level'], 'balance': new_bal}
    
    def cancel_game(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        user = self.db.get(uid)
        new_bal = user['balance'] + g['bet']
        self.db.update(uid, balance=new_bal)
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} {CURRENCY} возвращено'}
    
    def format_field(self, levels):
        result = []
        for level_idx, level in enumerate(levels):
            cells = level['cells']
            result.append(f"{level_idx + 1}:")
            result.append(f"{cells[0]} {cells[1]}")
            result.append(f"{cells[2]} {cells[3]}")
            result.append("")
        return "\n".join(result)
    
    def kb(self, uid, game):
        kb = []
        current_level = game['levels'][game['level']]
        
        row1 = []
        row2 = []
        
        for i in range(4):
            if f"{game['level']}_{i}" in game['opened']:
                btn = InlineKeyboardButton(text="✅", callback_data="ignore")
            else:
                btn = InlineKeyboardButton(text="🚪", callback_data=f"pyramid_{uid}_{game['level']}_{i}")
            
            if i < 2:
                row1.append(btn)
            else:
                row2.append(btn)
        
        kb.append(row1)
        kb.append(row2)
        
        if game['opened']:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"pyramid_cash_{uid}")])
        
        return InlineKeyboardMarkup(inline_keyboard=kb)

# ===================== ИГРА ФУТБОЛ (БЕЗ ШАНСА) =====================
class FootballGame:
    def __init__(self, db):
        self.db = db
    
    async def play(self, msg, uid, bet, choice):
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        self.db.update(uid, balance=user['balance'] - bet)
        
        # Отправляем Dice
        football_msg = await msg.answer_dice(emoji='⚽')
        
        # Ждём 4.6 секунды (время анимации Dice)
        await asyncio.sleep(4.6)
        
        value = football_msg.dice.value
        
        # ГОЛ при value >= 4 (4 или 5)
        is_goal = value >= 3
        result = 'гол' if is_goal else 'мимо'
        
        # Проверяем выбор пользователя
        if choice == 'гол':
            win = is_goal
            mult = 1.6
        else:  # choice == 'мимо'
            win = not is_goal
            mult = 2.2
        
        if win:
            win_amount = int(bet * mult)
            self.db.update(uid, balance=user['balance'] - bet + win_amount,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'result': result, 'amount': win_amount, 'mult': mult, 'balance': user['balance'] - bet + win_amount, 'value': value}
        else:
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            return {'ok': True, 'win': False, 'result': result, 'amount': bet, 'balance': user['balance'] - bet, 'value': value}

# ===================== ИГРА БАСКЕТБОЛ (БЕЗ ШАНСА) =====================
class BasketballGame:
    def __init__(self, db):
        self.db = db
    
    async def play(self, msg, uid, bet, choice):
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        self.db.update(uid, balance=user['balance'] - bet)
        
        # Отправляем Dice
        basketball_msg = await msg.answer_dice(emoji='🏀')
        
        # Ждём 4.6 секунды (время анимации Dice)
        await asyncio.sleep(4.6)
        
        value = basketball_msg.dice.value
        
        # Попадание при value >= 4 (4 или 5)
        is_goal = value >= 4
        result = 'попадание' if is_goal else 'мимо'
        
        # Проверяем выбор пользователя
        if choice == 'п':
            win = is_goal
            mult = 2.2
        else:  # choice == 'мимо'
            win = not is_goal
            mult = 1.6
        
        if win:
            win_amount = int(bet * mult)
            self.db.update(uid, balance=user['balance'] - bet + win_amount,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'result': result, 'amount': win_amount, 'mult': mult, 'balance': user['balance'] - bet + win_amount, 'value': value}
        else:
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            return {'ok': True, 'win': False, 'result': result, 'amount': bet, 'balance': user['balance'] - bet, 'value': value}
        
# ===================== ИГРА ДАРТС (БЕЗ ШАНСА) =====================
class DartGame:
    def __init__(self, db):
        self.db = db
        self.sectors = {
            'ц': {'emoji': '🎯', 'mult': 5.4, 'name': 'ЦЕНТР'},
            'к': {'emoji': '🔴', 'mult': 1.6, 'name': 'КРАСНОЕ'},
            'б': {'emoji': '⚪', 'mult': 2.5, 'name': 'БЕЛОЕ'},
            'м': {'emoji': '💢', 'mult': 5.4, 'name': 'МИМО'}
        }
    
    async def play(self, msg, uid, bet, choice):
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        self.db.update(uid, balance=user['balance'] - bet)
        
        # Отправляем Dice
        dart_msg = await msg.answer_dice(emoji='🎯')
        
        # Ждём 4.6 секунды (время анимации Dice)
        await asyncio.sleep(4.6)
        
        value = dart_msg.dice.value
        
        # Определяем сектор по значению
        if value == 1:
            result = 'м'
        elif value == 2:
            result = 'к'
        elif value == 3:
            result = 'б'
        elif value == 4:
            result = 'к'
        elif value == 5:
            result = 'б'
        elif value == 6:
            result = 'ц'
        
        sector = self.sectors[result]
        win = (choice == result)
        
        if win:
            win_amount = int(bet * sector['mult'])
            self.db.update(uid, balance=user['balance'] - bet + win_amount,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            return {'ok': True, 'win': True, 'result': result, 'amount': win_amount, 'balance': user['balance'] - bet + win_amount, 'value': value, 'sector': sector}
        else:
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            return {'ok': True, 'win': False, 'result': result, 'amount': bet, 'balance': user['balance'] - bet, 'value': value, 'sector': sector}

# ===================== ИГРА КВАК =====================
class QuackGame:
    def __init__(self, db):
        self.db = db
        self.games = {}
        self.multipliers = [1.21, 2.0, 5.05, 23.55]
        self.mines_count = [1, 2, 3, 4]
    
    def start(self, uid, bet):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        new_bal = user['balance'] - bet
        self.db.update(uid, balance=new_bal)
        
        rows = []
        for row_idx in range(4):
            mines = self.mines_count[row_idx]
            row = {'cells': ['⬜'] * 5, 'mines': random.sample(range(5), mines)}
            rows.append(row)
        
        self.games[uid] = {
            'bet': bet,
            'row': 0,
            'rows': rows,
            'opened': [],
            'mult': 1.0,
            'bal': new_bal,
            'won': 0
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid, r, c):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if r != g['row']:
            return {'ok': False, 'msg': '❌ Можно открывать только текущий ряд'}
        
        cell_key = f"{r}_{c}"
        if cell_key in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        row = g['rows'][r]
        
        if c in row['mines']:
            for i in range(5):
                if i in row['mines']:
                    row['cells'][i] = '💣'
                else:
                    row['cells'][i] = '⬛'
            row['cells'][c] = '💥'
            
            user = self.db.get(uid)
            self.db.update(uid, games_played=user.get('games_played', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'mine': True, 'row_idx': r + 1, 'mines_count': self.mines_count[r], 'bet': g['bet']}
        
        g['opened'].append(cell_key)
        row['cells'][c] = '🟩'
        g['mult'] = self.multipliers[r]
        g['won'] = int(g['bet'] * g['mult'])
        
        if r >= 3:
            user = self.db.get(uid)
            final_win = g['won']
            new_bal = g['bal'] + final_win
            self.db.update(uid, balance=new_bal,
                          games_played=user.get('games_played', 0) + 1,
                          wins=user.get('wins', 0) + 1)
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'won': final_win, 'mult': g['mult'], 'row': r + 1, 'balance': new_bal}
        
        g['row'] += 1
        return {'ok': True, 'over': False, 'row': r, 'col': c, 'next': g['row'] + 1, 'mult': g['mult'], 'won': g['won']}
    
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
        del self.games[uid]
        return {'ok': True, 'won': final_win, 'mult': g['mult'], 'row': g['row'], 'balance': new_bal}
    
    def cancel_game(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        user = self.db.get(uid)
        new_bal = user['balance'] + g['bet']
        self.db.update(uid, balance=new_bal)
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} {CURRENCY} возвращено'}
    
    def kb(self, uid, g):
        kb = []
        for r in range(3, -1, -1):
            row = g['rows'][r]
            btns = []
            if r < g['row']:
                for c in range(5):
                    if f"{r}_{c}" in g['opened']:
                        btns.append(InlineKeyboardButton(text="🟩", callback_data="ignore"))
                    else:
                        btns.append(InlineKeyboardButton(text="⬛", callback_data="ignore"))
            elif r == g['row']:
                for c in range(5):
                    if f"{r}_{c}" in g['opened']:
                        btns.append(InlineKeyboardButton(text="🟩", callback_data="ignore"))
                    else:
                        btns.append(InlineKeyboardButton(text="🟦", callback_data=f"quack_{uid}_{r}_{c}"))
            else:
                for c in range(5):
                    btns.append(InlineKeyboardButton(text="⬛", callback_data="ignore"))
            kb.append(btns)
        
        mines_info = f"💣 Мин в ряду: {self.mines_count[g['row']]}"
        kb.append([InlineKeyboardButton(text=mines_info, callback_data="ignore")])
        
        if g['opened']:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"quack_cash_{uid}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)


# ===================== ИГРА ОЧКО (BLACKJACK) =====================
class Blackjack:
    def __init__(self, db):
        self.db = db
        self.games = {}
        self.suits = ['♠', '♥', '♦', '♣']
        self.ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        self.values = {
            '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
            '10': 10, 'J': 10, 'Q': 10, 'K': 10, 'A': 11
        }
    
    def create_deck(self):
        deck = []
        for _ in range(6):
            for suit in self.suits:
                for rank in self.ranks:
                    deck.append({'rank': rank, 'suit': suit, 'value': self.values[rank]})
        random.shuffle(deck)
        return deck
    
    def card_str(self, card):
        return f"{card['rank']}{card['suit']}"
    
    def hand_value(self, hand):
        value = sum(card['value'] for card in hand)
        aces = sum(1 for card in hand if card['rank'] == 'A')
        
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        
        return value
    
    def hand_str(self, hand):
        cards = [self.card_str(card) for card in hand]
        return " ".join(cards)
    
    def start(self, uid, bet):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        user = self.db.get(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': f'❌ Не хватает, баланс {fmt(user["balance"])} {CURRENCY}'}
        
        deck = self.create_deck()
        
        player_hand = []
        dealer_hand = []
        
        # Первая карта игроку
        player_hand.append(deck.pop())
        # Первая карта дилеру
        dealer_hand.append(deck.pop())
        
        # Раздаём вторую карту игроку с проверкой на > 19
        while True:
            card = deck.pop()
            player_hand.append(card)
            player_value = self.hand_value(player_hand)
            
            if player_value > 19:
                deck.append(card)
                random.shuffle(deck)
                if len(deck) < 1:
                    player_hand.pop()
                    player_hand.append(card)
                    break
                continue
            break
        
        # Вторая карта дилеру
        dealer_hand.append(deck.pop())
        
        player_value = self.hand_value(player_hand)
        dealer_value = self.hand_value(dealer_hand)
        
        # Списываем ставку
        new_balance = user['balance'] - bet
        self.db.update(uid, balance=new_balance)
        
        game = {
            'bet': bet,
            'deck': deck,
            'player_hand': player_hand,
            'dealer_hand': dealer_hand,
            'player_value': player_value,
            'dealer_value': dealer_value,
            'bal': new_balance,
            'status': 'playing',
            'doubled': False
        }
        
        self.games[uid] = game
        return {'ok': True, 'data': game}
    
    def hit(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        game = self.games[uid]
        if game['status'] != 'playing':
            return {'ok': False, 'msg': '❌ Игра уже завершена'}
        
        card = game['deck'].pop()
        game['player_hand'].append(card)
        game['player_value'] = self.hand_value(game['player_hand'])
        
        if game['player_value'] > 21:
            return self.end_game(uid, 'lose')
        
        return {'ok': True, 'data': game}
    
    def stand(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        game = self.games[uid]
        if game['status'] != 'playing':
            return {'ok': False, 'msg': '❌ Игра уже завершена'}
        
        game['status'] = 'stand'
        
        while game['dealer_value'] < 17:
            card = game['deck'].pop()
            game['dealer_hand'].append(card)
            game['dealer_value'] = self.hand_value(game['dealer_hand'])
        
        return self.end_game(uid, 'compare')
    
    def double(self, uid):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        game = self.games[uid]
        if game['status'] != 'playing':
            return {'ok': False, 'msg': '❌ Игра уже завершена'}
        
        if game['doubled']:
            return {'ok': False, 'msg': '❌ Уже удвоено'}
        
        user = self.db.get(uid)
        if user['balance'] < game['bet']:
            return {'ok': False, 'msg': f'❌ Не хватает для удвоения'}
        
        game['bet'] *= 2
        game['doubled'] = True
        self.db.update(uid, balance=user['balance'] - game['bet'] // 2)
        game['bal'] = user['balance'] - game['bet']
        
        card = game['deck'].pop()
        game['player_hand'].append(card)
        game['player_value'] = self.hand_value(game['player_hand'])
        
        if game['player_value'] > 21:
            return self.end_game(uid, 'lose')
        
        return self.stand(uid)
    
    def end_game(self, uid, result):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        game = self.games[uid]
        
        if result == 'blackjack':
            win_amount = int(game['bet'] * 1.94)
            new_bal = game['bal'] + win_amount + game['bet']
            self.db.update(uid, balance=new_bal,
                          games_played=self.db.get(uid).get('games_played', 0) + 1,
                          wins=self.db.get(uid).get('wins', 0) + 1)
            msg = f"🎉 BLACKJACK! +{fmt(win_amount)}"
            
        elif result == 'lose':
            new_bal = game['bal']
            msg = f"❌ Перебор! -{fmt(game['bet'])}"
            
        elif result == 'compare':
            player_value = game['player_value']
            dealer_value = game['dealer_value']
            
            if dealer_value > 21:
                final_win = game['bet'] * 1.94
                new_bal = game['bal'] + int(final_win)
                self.db.update(uid, balance=new_bal,
                              games_played=self.db.get(uid).get('games_played', 0) + 1,
                              wins=self.db.get(uid).get('wins', 0) + 1)
                msg = f"🎉 Дилер перебрал! +{fmt(int(final_win))}"
                
            elif player_value > dealer_value:
                final_win = game['bet'] * 1.94
                new_bal = game['bal'] + int(final_win)
                self.db.update(uid, balance=new_bal,
                              games_played=self.db.get(uid).get('games_played', 0) + 1,
                              wins=self.db.get(uid).get('wins', 0) + 1)
                msg = f"🎉 Ты выиграл! +{fmt(int(final_win))}"
                
            elif player_value == dealer_value:
                new_bal = game['bal'] + game['bet']
                self.db.update(uid, balance=new_bal,
                              games_played=self.db.get(uid).get('games_played', 0) + 1)
                msg = f"🤝 Ничья! {fmt(game['bet'])} возвращено"
                
            else:
                new_bal = game['bal']
                self.db.update(uid, balance=new_bal,
                              games_played=self.db.get(uid).get('games_played', 0) + 1)
                msg = f"❌ Ты проиграл! -{fmt(game['bet'])}"
        
        dealer_str = self.hand_str(game['dealer_hand'])
        player_str = self.hand_str(game['player_hand'])
        
        dealer_display = f"{dealer_str} ({game['dealer_value']})"
        
        result_data = {
            'ok': True,
            'over': True,
            'dealer_hand': dealer_display,
            'player_hand': player_str,
            'player_value': game['player_value'],
            'dealer_value': game['dealer_value'],
            'result_msg': msg,
            'balance': new_bal
        }
        
        del self.games[uid]
        return result_data

# ===================== ОСНОВНОЙ КЛАСС =====================
class BotCore:
    def __init__(self):
        self.db = UserDB()
        self.blackjack = Blackjack(self.db)
        self.crash = CrashGame(self.db)
        self.mines = Mines(self.db)
        self.coin = CoinGame(self.db)
        self.dice = DiceGame(self.db)
        self.slots = SlotsGame(self.db)
        self.tower = TowerGame(self.db)
        self.diamonds = DiamondsGame(self.db)
        self.pyramid = PyramidGame(self.db)
        self.football = FootballGame(self.db)
        self.basketball = BasketballGame(self.db)
        self.dart = DartGame(self.db)
        self.quack = QuackGame(self.db)
        self.deposits = RequestDB(DEPOSIT_FILE)
        self.withdraws = RequestDB(WITHDRAW_FILE)
        self.logs = DB("admin_logs.json")
    
    def parse_bet(self, text, bal=None):
        if not text:
            return 0
        text = text.lower().strip()
        if text in ['всё', 'все'] and bal is not None:
            return bal
        if text in ['пол', 'половина'] and bal is not None:  # <-- ДОБАВИТЬ ЭТО
            return bal // 2
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
    
    def add_log(self, admin_id, action, details=""):
        """Добавляет запись в лог"""
        data = self.logs.read()
        if "logs" not in data:
            data["logs"] = []
        data["logs"].append({
            "timestamp": datetime.datetime.now().isoformat(),
            "admin_id": admin_id,
            "action": action,
            "details": details
        })
        self.logs.write(data)

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

# ===================== РАССЫЛКА =====================
async def cmd_mailing(msg: Message, state: FSMContext):
    """Рассылка сообщения всем пользователям (только для админов)"""
    if not is_admin(msg.from_user.id):
        await msg.reply("❌ Только для админов")
        return
    
    # Проверяем формат команды
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply(
            "📨 РАССЫЛКА\n\n"
            "расс [текст сообщения]\n\n"
            "Пример:\n"
            "расс Привет всем!\n\n"
            "Текст может содержать переносы строк и эмодзи.\n"
            "Для отмены напишите 'отмена'"
        )
        return
    
    message_text = parts[1].strip()
    
    if not message_text:
        await msg.reply("❌ Текст сообщения не может быть пустым")
        return
    
    # Сохраняем текст в состояние
    await state.update_data(mailing_text=message_text)
    
    # Запрашиваем подтверждение
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отправить", callback_data="mailing_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="mailing_cancel")
        ]
    ])
    
    # Показываем превью
    preview = message_text[:300] + ("..." if len(message_text) > 300 else "")
    
    await msg.reply(
        f"📨 ПОДТВЕРЖДЕНИЕ РАССЫЛКИ\n\n"
        f"📝 Текст:\n{preview}\n\n"
        f"⚠️ ВНИМАНИЕ!\n"
        f"Сообщение будет отправлено ВСЕМ пользователям бота.\n"
        f"Это действие нельзя отменить!\n\n"
        f"Продолжить?",
        reply_markup=kb
    )

async def process_mailing_confirm(cb: CallbackQuery, state: FSMContext):
    """Обрабатывает подтверждение рассылки"""
    uid = cb.from_user.id
    
    if not is_admin(uid):
        await cb.answer("❌ Только для админов", show_alert=True)
        return
    
    data = await state.get_data()
    message_text = data.get('mailing_text')
    
    if not message_text:
        await cb.answer("❌ Ошибка: текст не найден", show_alert=True)
        await state.clear()
        return
    
    await cb.message.edit_text("📨 НАЧАЛО РАССЫЛКИ...\n\n⏳ Подождите, это может занять некоторое время.")
    
    # Получаем всех пользователей
    users = core.db.all_users()
    total = len(users)
    
    sent = 0
    failed = 0
    blocked = 0
    
    for uid_str, user_data in users:
        try:
            uid_int = int(uid_str)
            
            await cb.bot.send_message(
                uid_int,
                message_text,
                parse_mode="HTML"
            )
            sent += 1
            
        except Exception as e:
            if "bot was blocked by the user" in str(e):
                blocked += 1
            else:
                failed += 1
        
        # Небольшая задержка чтобы не спамить
        await asyncio.sleep(0.05)
    
    # Формируем отчёт
    report = f"✅ РАССЫЛКА ЗАВЕРШЕНА!\n\n"
    report += f"📤 Отправлено: {sent}\n"
    report += f"❌ Ошибок: {failed}\n"
    if blocked > 0:
        report += f"🚫 Заблокировали бота: {blocked}\n"
    report += f"📊 Всего пользователей: {total}\n"
    report += f"📝 Текст:\n{message_text[:200]}{'...' if len(message_text) > 200 else ''}"
    
    await cb.message.edit_text(report)
    
    # Логируем
    core.add_log(
        admin_id=uid,
        action="mailing",
        details=f"Отправлено: {sent} | Ошибок: {failed} | Текст: {message_text[:50]}..."
    )
    
    await state.clear()
    await cb.answer()

async def process_mailing_cancel(cb: CallbackQuery, state: FSMContext):
    """Отменяет рассылку"""
    await state.clear()
    await cb.message.edit_text("❌ Рассылка отменена")
    await cb.answer()

# ===================== ОБРАБОТЧИК КОМАНД /start =====================
async def cmd_start(msg: Message):
    uid = msg.from_user.id
    user = core.db.get(uid)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Краш", callback_data="menu_crash"),
         InlineKeyboardButton(text="💣 Мины", callback_data="menu_mines")],
        [InlineKeyboardButton(text="🎲 Игры", callback_data="menu_games"),
         InlineKeyboardButton(text="💰 Кошелёк", callback_data="menu_wallet")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="menu_top")]
    ])
    
    await msg.reply(
        f"🎰 Добро пожаловать в DeathGmp!\n"
        f"💸 Делайте депозит, апайте GMP, а затем выводите!\n\n"
        f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}\n"
        f"🎮 Игр: {user.get('games_played', 0)} | 🏆 Побед: {user.get('wins', 0)}\n\n"
        f"Выберите действие:",
        reply_markup=kb
    )

# ===================== ТОП ДЕПОЗИТОВ =====================
async def cmd_top_deposits(msg: Message):
    top = core.db.top_deposits(limit=10)
    if not top:
        await msg.reply("📊 Нет игроков с депозитами")
        return
    
    text = "🏆 ТОП ПО ДЕПОЗИТАМ\n\n"
    total_deposits = 0
    for i, (uid, u) in enumerate(top, 1):
        try:
            chat = await msg.bot.get_chat(int(uid))
            name = chat.first_name
        except:
            name = f"ID {uid[-4:]}"
        
        deposited = u.get('total_deposited', 0)
        total_deposits += deposited
        
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — {fmt(deposited)} {CURRENCY}\n"
    
    text += f"\n💎 Всего депозитов: {fmt(total_deposits)} {CURRENCY}"
    await msg.reply(text)

# ===================== ОБРАБОТЧИК ТЕКСТА (БЕЗ /) =====================
async def handle_text(msg: Message, state: FSMContext):
    text = msg.text.lower().strip()
    uid = msg.from_user.id
    
    # ===== ОТМЕНА =====
    if text == 'отмена' or text == 'cancel':
        current_state = await state.get_state()
        if current_state:
            await state.clear()
            await msg.reply("❌ Операция отменена")
        else:
            cancelled = False
            if uid in core.crash.games:
                res = core.crash.cancel_game(uid)
                await msg.reply(res['msg'])
                cancelled = True
            elif uid in core.mines.games:
                res = core.mines.cancel_game(uid)
                await msg.reply(res['msg'])
                cancelled = True
            elif uid in core.tower.games:
                res = core.tower.cancel_game(uid)
                await msg.reply(res['msg'])
                cancelled = True
            elif uid in core.diamonds.games:
                res = core.diamonds.cancel_game(uid)
                await msg.reply(res['msg'])
                cancelled = True
            elif uid in core.pyramid.games:
                res = core.pyramid.cancel_game(uid)
                await msg.reply(res['msg'])
                cancelled = True
            elif uid in core.quack.games:
                res = core.quack.cancel_game(uid)
                await msg.reply(res['msg'])
                cancelled = True
            if not cancelled:
                await msg.reply("❌ Нет активной игры или операции")
        return
    
    # ===== ПРОВЕРКА СОСТОЯНИЙ =====
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
        user = core.db.get(uid)
        if user['balance'] < amount:
            await msg.reply(f"❌ Не хватает средств. Баланс: {fmt(user['balance'])} {CURRENCY}")
            return
        await state.update_data(withdraw_amount=amount)
        await state.set_state(WithdrawStates.waiting_address)
        await msg.reply(
            f"📤 ВЫВОД СРЕДСТВ\n\n"
            f"💰 Сумма: {fmt(amount)} {CURRENCY}\n\n"
            f"Введите @username для вывода средств:"
        )
        return
    
    if current_state == WithdrawStates.waiting_address:
        address = text.strip()
        if len(address) < 4:
            await msg.reply("❌ Слишком короткий @username. Попробуйте снова:")
            return
        data = await state.get_data()
        amount = data.get('withdraw_amount')
        if not amount:
            await state.clear()
            await msg.reply("❌ Ошибка. Начните заново: напишите 'депозит' или 'вывод'")
            return
        await process_withdraw(msg, amount, address)
        await state.clear()
        return
    
    # ===== КОМАНДЫ БЕЗ / =====
    
    # Рассылка
    if text.startswith('расс'):
        await cmd_mailing(msg, state)
        return

    # Очко (Blackjack)
    if text.startswith('очко'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            if bet <= 0 or bet > core.db.get(uid)['balance']:
                await msg.reply(f"❌ Неверная ставка, баланс {fmt(core.db.get(uid)['balance'])} {CURRENCY}")
                return

            await state.update_data(blackjack_bet=bet)
            await state.set_state(BlackjackStates.waiting_start)

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Начать", callback_data=f"blackjack_start_{uid}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="blackjack_cancel")]
            ])

            await msg.reply(
                f"🃏 ОЧКО (21)\n\n"
                f"💰 Ставка: {fmt(bet)} {CURRENCY}\n"
                f"Хотите начать игру?\n\n"
                f"⚠️ После начала игру нельзя отменить!",
                reply_markup=kb
            )
        else:
            await msg.reply(
            "🃏 ОЧКО (21)\n\n"
            "очко [ставка]\n"
            "Пример: очко 1000\n\n"
            "💰 Множители:\n"
            "• Выигрыш - x1.94\n\n"
            "Команды во время игры:\n"
            "• взять - взять карту\n"
            "• стоп - закончить ход\n"
            "• удвоить - удвоить ставку"
            )
        return
    
    # Главное меню
    if text == 'старт' or text == 'меню' or text == 'start':
        await cmd_start(msg)
        return
    
    # Баланс
    if text == 'баланс' or text == 'б':
        user = core.db.get(uid)
        await msg.reply(f"💰 Баланс: {fmt(user['balance'])} {CURRENCY}")
        return
    
    # Профиль
    if text == 'профиль' or text == 'проф' or text == 'п':
        await cmd_profile(msg)
        return
    
    # Топ
    if text == 'топ' or text == 'топ игроков':
        await cmd_top(msg)
        return
    
    # Топ депозитов
    if text == 'топ деп' or text == 'топ депозитов' or text == 'топдеп':
        await cmd_top_deposits(msg)
        return
    
    # Депозит
    if text.startswith('депозит') or text.startswith('деп'):
        parts = text.split()
        if len(parts) > 1:
            amount = core.parse_bet(parts[1])
            if amount <= 0:
                await msg.reply(f"❌ Неверная сумма. Пример: депозит 1000")
                return
        
            await state.update_data(deposit_amount=amount)
            await state.set_state(DepositStates.waiting_confirmation)
        
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Я перевёл", callback_data=f"deposit_confirm_{uid}_{amount}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="deposit_cancel")]
            ])
        
            await msg.reply(
            f"⚠️ ПРЕЖДЕ ЧЕМ ОТПРАВИТЬ ЗАЯВКУ!\n\n"
            f"Переведите {fmt(amount)} {CURRENCY} в @gminesbot\n"
            f"на аккаунт: @nikaIsLove (ID: 8894401294)\n\n"
            f"После перевода нажмите «Я перевёл»",
            reply_markup=kb
            )
        else:
            await state.set_state(DepositStates.waiting_amount)
            await msg.reply(
            f"💰 ДЕПОЗИТ\n\n"
            f"Введите сумму в {CURRENCY}, которую хотите пополнить:\n\n"
            f"Форматы: 1000, 5к, 50к, 1кк\n\n"
            f"Для отмены напишите 'отмена'"
            )
        return

    # Вывод
    if text.startswith('вывод'):
        parts = text.split()
        if len(parts) >= 3:
            amount = core.parse_bet(parts[1])
            address = ' '.join(parts[2:])
            if amount <= 0:
                await msg.reply(f"❌ Неверная сумма. Пример: вывод 500 @username")
                return
            await process_withdraw(msg, amount, address)
        else:
            await state.set_state(WithdrawStates.waiting_amount)
            await msg.reply(
                f"📤 ВЫВОД СРЕДСТВ\n\n"
                f"Введите сумму в {CURRENCY}, которую хотите вывести:\n\n"
                f"Форматы: 100, 5к\n\n"
                f"Для отмены напишите 'отмена'"
            )
        return
    
    # Мои заявки
    if text == 'мои заявки' or text == 'заявки':
        await cmd_my_requests(msg)
        return

        # Список игр
    if text == 'игры':
        await msg.reply(
            "🎮 ДОСТУПНЫЕ ИГРЫ:\n\n"
            "🚀 КРАШ\n"
            "💣 МИНЫ\n"
            "🎲 КУБИК\n"
            "🎰 СЛОТЫ\n"
            "🏗️ БАШНЯ\n"
            "💎 АЛМАЗЫ\n"
            "🔺 ПИРАМИДА\n"
            "⚽ ФУТБОЛ\n"
            "🏀 БАСКЕТБОЛ\n"
            "🎯 ДАРТС\n"
            "🐸 КВАК\n"
            "🃏 ОЧКО"
        )
        return
    
    # ===== ИГРЫ =====
    
    if text.startswith('краш'):
        parts = text.split()
        if len(parts) >= 3:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            if bet <= 0 or bet > core.db.get(uid)['balance']:
                await msg.reply(f"❌ Неверная ставка, баланс {fmt(core.db.get(uid)['balance'])} {CURRENCY}")
                return
            try:
                target = float(parts[2].replace(',', '.'))
            except:
                await msg.reply("❌ Неверный множитель")
                return
            await cmd_crash_play(msg, bet, target)
        else:
            await msg.reply(
                "🚀 КРАШ\n\n"
                "краш [ставка] [множитель]\n"
                "Пример: краш 10 2\n\n"
                f"Макс. ставка: {fmt(MAX_CRASH_BET)} {CURRENCY}\n"
                f"Множитель от 1.1 до {MAX_CRASH_MULTIPLIER}\n"
                "Для отмены напишите 'отмена'"
            )
        return
    
    # Мины
    if text.startswith('мины'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            mines = int(parts[2]) if len(parts) > 2 else 1
            await cmd_mines_play(msg, bet, mines)
        else:
            await msg.reply(
                "💣 МИНЫ\n\n"
                "мины [ставка] [количество мин]\n"
                "Пример: мины 10 3\n\n"
                "Мин от 1 до 6\n\n"
                "Для отмены напишите 'отмена'"
            )
        return
    
    # Монетка
    if text.startswith('монетка') or text.startswith('мон'):
        parts = text.split()
        if len(parts) >= 3:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            choice = parts[2].lower()
            if choice not in ['орёл', 'орел', 'решка']:
                await msg.reply("❌ орёл или решка")
                return
            choice = 'орёл' if choice in ['орёл', 'орел'] else 'решка'
            await cmd_coin_play(msg, bet, choice)
        else:
            await msg.reply(
                "🪙 МОНЕТКА\n\n"
                "монетка [ставка] [орёл/решка]\n"
                "Пример: монетка 10 орёл\n\n"
                "💰 Множитель: x1.96"
            )
        return
    
    # Кубик
    if text.startswith('кубик') or text.startswith('куб'):
        parts = text.split()
        if len(parts) >= 3:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            if bet <= 0 or bet > core.db.get(uid)['balance']:
                await msg.reply(f"❌ Неверная ставка, баланс {fmt(core.db.get(uid)['balance'])} {CURRENCY}")
                return
            bet_type = parts[2].lower()
        
            if bet_type in ['ч', 'чёт', 'чётное', 'even']:
                await cmd_dice_play(msg, bet, 'even')
            elif bet_type in ['н', 'неч', 'нечётное', 'odd']:
                await cmd_dice_play(msg, bet, 'odd')
            elif bet_type in ['р', 'равно', 'equal']:
                await cmd_dice_play(msg, bet, 'equal')
            elif bet_type in ['б', 'больше', 'over']:
                await cmd_dice_play(msg, bet, 'over')
            elif bet_type in ['м', 'меньше', 'under']:
                await cmd_dice_play(msg, bet, 'under')
            else:
                try:
                    num = int(bet_type)
                    if 1 <= num <= 6:
                        await cmd_dice_play(msg, bet, 'number', num)
                    else:
                        await msg.reply("❌ Число от 1 до 6")
                except:
                    await msg.reply(
                        "❌ Неверная ставка!\n\n"
                        "Доступные ставки:\n"
                        "• [число] - ставка на число (x5.8)\n"
                        "• ч/чётное - чётное (x1.94)\n"
                        "• н/нечётное - нечётное (x1.94)\n"
                        "• р/равно - равно 3 (x5.8)\n"
                        "• б/больше - больше 3 (x1.94)\n"
                        "• м/меньше - меньше 3 (x2.9)"
                    )
            return
        else:
            await msg.reply(
                "🎲 КУБИК\n\n"
                "кубик [ставка] [ставка]\n"
                "Пример: кубик 10 3\n\n"
                "Доступные ставки:\n"
                "• [число] - ставка на число (x5.8)\n"
                "• ч/чётное - чётное (x1.94)\n"
                "• н/нечётное - нечётное (x1.94)\n"
                "• р/равно - равно 3 (x5.8)\n"
                "• б/больше - больше 3 (x1.94)\n"
                "• м/меньше - меньше 3 (x2.9)"
            )
        return
    
    # Слоты
    if text.startswith('слоты') or text.startswith('слот'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            await cmd_slots_play(msg, bet)
        else:
            await msg.reply(
                "🎰 СЛОТЫ\n\n"
                "слоты [ставка]\n"
                "Пример: слоты 10\n\n"
                "💰 Множители: x3-x10"
            )
        return
    
    # Башня
    if text.startswith('башня'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            mines = int(parts[2]) if len(parts) > 2 else 1
            await cmd_tower_play(msg, bet, mines)
        else:
            await msg.reply(
                "🏗️ БАШНЯ\n\n"
                "башня [ставка] [мин]\n"
                "Пример: башня 10 2\n\n"
                "Мин от 1 до 4\n\n"
                "Для отмены напишите 'отмена'"
            )
        return
    
    # Алмазы
    if text.startswith('алмазы') or text.startswith('алм'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            mines = int(parts[2]) if len(parts) > 2 else 1
            await cmd_diamonds_play(msg, bet, mines)
        else:
            await msg.reply(
                "💎 АЛМАЗЫ\n\n"
                "алмазы [ставка] [мин]\n"
                "Пример: алмазы 10 1\n\n"
                "Мин от 1 до 2\n\n"
                "Для отмены напишите 'отмена'"
            )
        return
    
    # Пирамида
    if text.startswith('пирамида') or text.startswith('пир'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            doors = int(parts[2]) if len(parts) > 2 else 3
            await cmd_pyramid_play(msg, bet, doors)
        else:
            await msg.reply(
                "🔺 ПИРАМИДА\n\n"
                "пирамида [ставка] [дверей]\n"
                "Пример: пирамида 10 3\n\n"
                "Дверей от 1 до 3\n\n"
                "Для отмены напишите 'отмена'"
            )
        return
    
    # Футбол
    if text.startswith('футбол') or text.startswith('фт'):
        parts = text.split()
        if len(parts) >= 3:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            choice = parts[2].lower()
            if choice not in ['гол', 'мимо']:
                await msg.reply("❌ гол или мимо")
                return
            await cmd_football_play(msg, bet, choice)
        else:
            await msg.reply(
                "⚽ ФУТБОЛ\n\n"
                "футбол [ставка] [гол/мимо]\n"
                "Пример: футбол 10 гол\n\n"
                "💰 Множители:\n"
                "Гол - x1.6\n"
                "Мимо - x2.2"
            )
        return
    
    # Баскетбол
    if text.startswith('баскетбол') or text.startswith('бс'):
        parts = text.split()
        if len(parts) >= 3:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            if bet <= 0 or bet > core.db.get(uid)['balance']:
                await msg.reply(f"❌ Неверная ставка, баланс {fmt(core.db.get(uid)['balance'])} {CURRENCY}")
                return
            choice = parts[2].lower()
            if choice == 'попадание':
                choice = 'п'
            if choice not in ['п', 'мимо']:
                await msg.reply("❌ п (попадание) или мимо")
                return
            await cmd_basketball_play(msg, bet, choice)
        else:
            await msg.reply(
                "🏀 БАСКЕТБОЛ\n\n"
                "баскетбол [ставка] [п/мимо]\n"
                "Пример: баскетбол 10 п\n\n"
                "💰 Множители:\n"
                "п (попадание) - x2.2\n"
                "мимо - x1.6"
            )
        return

    # Дартс
    if text.startswith('дартс') or text.startswith('дс'):
        parts = text.split()
        if len(parts) >= 3:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            choice = parts[2].lower()
            if choice not in ['ц', 'к', 'б', 'м']:
                await msg.reply("❌ ц (центр), к (красное), б (белое), м (мимо)")
                return
            await cmd_dart_play(msg, bet, choice)
        else:
            await msg.reply(
                "🎯 ДАРТС\n\n"
                "дартс [ставка] [ц/к/б/м]\n"
                "Пример: дартс 10 ц\n\n"
                "💰 Множители:\n"
                "ц - центр (x5.4)\n"
                "к - красное (x1.6)\n"
                "б - белое (x2.5)\n"
                "м - мимо (x5.4)"
            )
        return
    
    # Квак
    if text.startswith('квак'):
        parts = text.split()
        if len(parts) >= 2:
            bet = core.parse_bet(parts[1], core.db.get(uid)['balance'])
            await cmd_quack_play(msg, bet)
        else:
            await msg.reply(
                "🐸 КВАК\n\n"
                "квак [ставка]\n"
                "Пример: квак 10\n\n"
                "4 ряда с возрастающими минами\n"
                "💰 Множители:\n"
                "1 ряд - x1.21\n"
                "2 ряд - x2.0\n"
                "3 ряд - x5.05\n"
                "4 ряд - x23.55\n\n"
                "Для отмены напишите 'отмена'"
            )
        return
    
    # Помощь
    if text == 'помощь' or text == 'help' or text == 'команды':
        await msg.reply(
            "📋 ДОСТУПНЫЕ КОМАНДЫ\n\n"
            "💰 Финансы:\n"
            "• депозит [сумма] - пополнить баланс\n"
            "• вывод [сумма] [@username] - вывести средства\n"
            "• мои заявки - посмотреть статус заявок\n"
            "• баланс - проверить баланс\n"
            "• профиль - посмотреть профиль\n"
            "• топ - топ игроков\n"
            "• топ деп - топ по депозитам\n\n"
            "🎮 Игры:\n"
            "• краш [ставка] [множитель] - Краш\n"
            "• мины [ставка] [мин] - Мины\n"
            "• монетка [ставка] [орёл/решка] - Монетка (x1.96)\n"
            "• кубик [ставка] [ставка] - Кубик\n"
            "• слоты [ставка] - Слоты\n"
            "• башня [ставка] [мин] - Башня\n"
            "• алмазы [ставка] [мин] - Алмазы\n"
            "• пирамида [ставка] [дверей] - Пирамида\n"
            "• футбол [ставка] [гол/мимо] - Футбол\n"
            "• баскетбол [ставка] [попадание/мимо] - Баскетбол\n"
            "• дартс [ставка] [ц/к/б/м] - Дартс\n"
            "• квак [ставка] - Квак\n"
            "• отмена - отменить игру или операцию\n\n"
            "👑 Для админов:\n"
            "• расс [текст] - массовая рассылка"
        )
        return
    
# ===================== ФУНКЦИИ ДЛЯ ИГР =====================

async def cmd_crash_play(msg: Message, bet: int, target: float):
    uid = msg.from_user.id
    user = core.db.get(uid)
    
    if bet <= 0:
        await msg.reply(f"❌ Ставка должна быть больше 0")
        return
    
    if bet > user['balance']:
        await msg.reply(f"❌ Не хватает, баланс {fmt(user['balance'])} {CURRENCY}")
        return
    
    if bet > MAX_CRASH_BET:
        await msg.reply(f"❌ Максимальная ставка {fmt(MAX_CRASH_BET)} {CURRENCY}")
        return
    
    if target < 1.1 or target > MAX_CRASH_MULTIPLIER:
        await msg.reply(f"❌ Множитель от 1.1 до {MAX_CRASH_MULTIPLIER}")
        return
    
    res = core.crash.start(uid, bet, target)
    
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

async def cmd_mines_play(msg: Message, bet: int, mines: int):
    uid = msg.from_user.id
    user = core.db.get(uid)
    
    if bet <= 0 or bet > user['balance']:
        await msg.reply(f"❌ Неверная ставка, баланс {fmt(user['balance'])} {CURRENCY}")
        return
    
    if mines < 1 or mines > 6:
        await msg.reply("❌ Мин от 1 до 6")
        return
    
    res = core.mines.start(uid, bet, mines)
    if not res['ok']:
        await msg.reply(res['msg'])
        return
    
    await msg.reply(
        f"💣 МИНЫ | 💣 {mines} мин\n"
        f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
        f"📈 Множитель x1.0\n\n"
        f"Выберите клетку:",
        reply_markup=core.mines.kb(uid, res['data']['field'])
    )

async def cmd_coin_play(msg: Message, bet: int, choice: str):
    uid = msg.from_user.id
    res = core.coin.play(uid, bet, choice)
    
    if res['ok']:
        if res['win']:
            await msg.reply(
                f"🪙 МОНЕТКА\n\n"
                f"Выпало: {res['result']}\n"
                f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY} (x1.96)\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
        else:
            await msg.reply(
                f"🪙 МОНЕТКА\n\n"
                f"Выпало: {res['result']}\n"
                f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
    else:
        await msg.reply(res['msg'])

async def cmd_dice_play(msg: Message, bet: int, bet_type: str, value: int = None):
    uid = msg.from_user.id
    res = await core.dice.play(msg, uid, bet, bet_type, value)
    
    if res['ok']:
        if res['win']:
            type_names = {
                'number': f"число {value}",
                'even': 'чётное',
                'odd': 'нечётное',
                'equal': 'равно 3',
                'over': 'больше 3',
                'under': 'меньше 3'
            }
            await msg.reply(
                f"🎲 КУБИК\n\n"
                f"Выпало: {res['roll']}\n"
                f"Ставка: {type_names.get(bet_type, bet_type)}\n"
                f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY} (x{res['mult']})\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
        else:
            await msg.reply(
                f"🎲 КУБИК\n\n"
                f"Выпало: {res['roll']}\n"
                f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
    else:
        await msg.reply(res['msg'])

async def cmd_slots_play(msg: Message, bet: int):
    uid = msg.from_user.id
    res = await core.slots.play(msg, uid, bet)
    
    if res['ok']:
        if res['win']:
            await msg.reply(
                f"🎰 СЛОТЫ\n\n"
                f"✅ ВЫИГРЫШ: x{res['mult']} = +{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
        else:
            await msg.reply(
                f"🎰 СЛОТЫ\n\n"
                f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
    else:
        await msg.reply(res['msg'])

async def cmd_tower_play(msg: Message, bet: int, mines: int):
    uid = msg.from_user.id
    res = core.tower.start(uid, bet, mines)
    if not res['ok']:
        await msg.reply(res['msg'])
        return
    
    await msg.reply(
        f"🏗️ БАШНЯ | 1/9 | 💣 {mines} мины\n"
        f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
        f"📈 Макс. множитель: x{core.tower.mults(mines)[-1]}\n\n"
        f"Выберите клетку:",
        reply_markup=core.tower.kb(uid, res['data'])
    )

async def cmd_diamonds_play(msg: Message, bet: int, mines: int):
    uid = msg.from_user.id
    res = core.diamonds.start(uid, bet, mines)
    if not res['ok']:
        await msg.reply(res['msg'])
        return
    
    await msg.reply(
        f"💎 АЛМАЗЫ | 1/9 | 💣 {mines} мины\n"
        f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
        f"📈 Макс. множитель: x{core.diamonds.mults(mines)[-1]}\n\n"
        f"Выберите клетку:",
        reply_markup=core.diamonds.kb(uid, res['data'])
    )

async def cmd_pyramid_play(msg: Message, bet: int, doors: int):
    uid = msg.from_user.id
    res = core.pyramid.start(uid, bet, doors)
    if not res['ok']:
        await msg.reply(res['msg'])
        return
    
    await msg.reply(
        f"🔺 ПИРАМИДА | 1/12 | 🚪 {doors} двери\n"
        f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
        f"📈 Макс. множитель: x{core.pyramid.multipliers[doors][-1]}\n\n"
        f"Выберите дверь:",
        reply_markup=core.pyramid.kb(uid, res['data'])
    )

async def cmd_football_play(msg: Message, bet: int, choice: str):
    uid = msg.from_user.id
    res = await core.football.play(msg, uid, bet, choice)
    
    if res['ok']:
        result_emoji = '⚽' if res['result'] == 'гол' else '🥅'
        if res['win']:
            await msg.reply(
                f"⚽ ФУТБОЛ\n\n"
                f"{result_emoji} {res['result'].upper()}! [{res['value']}]\n"
                f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY} (x{res['mult']})\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
        else:
            await msg.reply(
                f"⚽ ФУТБОЛ\n\n"
                f"{result_emoji} {res['result'].upper()}! [{res['value']}]\n"
                f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
    else:
        await msg.reply(res['msg'])

async def cmd_basketball_play(msg: Message, bet: int, choice: str):
    uid = msg.from_user.id
    res = await core.basketball.play(msg, uid, bet, choice)
    
    if res['ok']:
        result_emoji = '🏀' if res['result'] == 'попадание' else '🧺'
        if res['win']:
            await msg.reply(
                f"🏀 БАСКЕТБОЛ\n\n"
                f"{result_emoji} {res['result'].upper()}! [{res['value']}]\n"
                f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY} (x{res['mult']})\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
        else:
            await msg.reply(
                f"🏀 БАСКЕТБОЛ\n\n"
                f"{result_emoji} {res['result'].upper()}! [{res['value']}]\n"
                f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
    else:
        await msg.reply(res['msg'])

async def cmd_dart_play(msg: Message, bet: int, choice: str):
    uid = msg.from_user.id
    res = await core.dart.play(msg, uid, bet, choice)
    
    if res['ok']:
        sector = res['sector']
        if res['win']:
            await msg.reply(
                f"🎯 ДАРТС\n\n"
                f"{sector['emoji']} {sector['name']}! [{res['value']}] (x{sector['mult']})\n"
                f"✅ ВЫИГРЫШ: +{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
        else:
            await msg.reply(
                f"🎯 ДАРТС\n\n"
                f"{sector['emoji']} {sector['name']}! [{res['value']}]\n"
                f"❌ ПОТЕРЯНО: -{fmt(res['amount'])} {CURRENCY}\n"
                f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
            )
    else:
        await msg.reply(res['msg'])

async def cmd_quack_play(msg: Message, bet: int):
    uid = msg.from_user.id
    res = core.quack.start(uid, bet)
    if not res['ok']:
        await msg.reply(res['msg'])
        return
    
    g = res['data']
    await msg.reply(
        f"🐸 КВАК | 1/4 | 💣 1 мина\n"
        f"💰 Ставка {fmt(bet)} {CURRENCY}\n"
        f"📈 Множитель x1.21\n\n"
        f"Выберите клетку (ряд снизу):",
        reply_markup=core.quack.kb(uid, g)
    )

# ===================== ПРОФИЛЬ =====================
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

# ===================== ТОП =====================
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

# ===================== ДЕПОЗИТЫ И ВЫВОДЫ =====================

async def process_deposit(msg: Message, amount: int):
    uid = msg.from_user.id
    username = msg.from_user.username or msg.from_user.full_name
    
    # Сохраняем сумму в состояние
    await state.update_data(deposit_amount=amount)
    await state.update_data(deposit_username=username)
    await state.set_state(DepositStates.waiting_confirmation)
    
    # Показываем инструкцию
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я перевёл", callback_data=f"deposit_confirm_{uid}_{amount}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="deposit_cancel")]
    ])
    
    await msg.reply(
        f"⚠️ ПРЕЖДЕ ЧЕМ ОТПРАВИТЬ ЗАЯВКУ!\n\n"
        f"Переведите {fmt(amount)} {CURRENCY} в @gminesbot\n"
        f"на аккаунт: @nikaIsLove (ID: 8894401294)\n\n"
        f"После перевода нажмите «Я перевёл»",
        reply_markup=kb
    )
    
async def process_withdraw(msg: Message, amount: int, address: str):
    uid = msg.from_user.id
    username = msg.from_user.username or msg.from_user.full_name
    
    user = core.db.get(uid)
    if user['balance'] < amount:
        await msg.reply(f"❌ Не хватает средств. Баланс: {fmt(user['balance'])} {CURRENCY}")
        return
    
    request = core.withdraws.create(uid, username, amount, address)
    core.db.update(uid, balance=user['balance'] - amount)
    
    await msg.reply(
        f"✅ ЗАЯВКА НА ВЫВОД СОЗДАНА!\n\n"
        f"📋 Номер заявки: #{request['id']}\n"
        f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
        f"👤 username: {address}\n"
        f"⏳ Статус: Ожидает подтверждения"
    )
    
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
                f"👤 username: {address}\n"
                f"📋 Заявка: #{request['id']}",
                reply_markup=kb
            )
        except:
            pass

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

# ===================== АДМИН-ПАНЕЛЬ =====================

async def cmd_admin_panel(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Депозиты", callback_data="admin_deposits"),
         InlineKeyboardButton(text="📤 Выводы", callback_data="admin_withdraws")]
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
    
    text = "📥 ЗАЯВКИ НА ДЕПОЗИТ\n\n"
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
    
    text = "📤 ЗАЯВКИ НА ВЫВОД\n\n"
    kb = []
    
    for r in pending:
        text += f"#{r['id']} | {r['username']} | {fmt(r['amount'])} {CURRENCY}\n"
        text += f"📍 {r['address']}\n\n"
        kb.append([
            InlineKeyboardButton(text=f"✅ #{r['id']}", callback_data=f"withdraw_approve_{r['id']}"),
            InlineKeyboardButton(text=f"❌ #{r['id']}", callback_data=f"withdraw_reject_{r['id']}")
        ])
    
    await msg.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# ===================== КОЛБЭКИ =====================
async def callback_handler(cb: CallbackQuery, state: FSMContext):
    data = cb.data
    uid = cb.from_user.id
    
    try:

        if data.startswith("deposit_confirm_"):
            parts = data.split('_')
            user_id = int(parts[2])
            amount = int(parts[3])
    
            if uid != user_id:
                await cb.answer("❌ Не твоя заявка", show_alert=True)
                return
    
            username = cb.from_user.username or cb.from_user.full_name
    
            request = core.deposits.create(uid, username, amount)
    
            await cb.message.edit_text(
        f"✅ ЗАЯВКА НА ДЕПОЗИТ СОЗДАНА!\n\n"
        f"📋 Номер заявки: #{request['id']}\n"
        f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
        f"⏳ Статус: Ожидает подтверждения\n\n"
        f"⏰ Ожидайте, администратор подтвердит заявку."
            )
    
            for admin_id in ADMIN_IDS:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"deposit_approve_{request['id']}"),
                     InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deposit_reject_{request['id']}")]
                ])

                try:
                    await cb.bot.send_message(
                        admin_id,
                        f"📥 НОВАЯ ЗАЯВКА НА ДЕПОЗИТ\n\n"
                        f"👤 Пользователь: {username}\n"
                        f"🆔 ID: {uid}\n"
                        f"💰 Сумма: {fmt(amount)} {CURRENCY}\n"
                        f"📋 Заявка: #{request['id']}",
                        reply_markup=kb
                    )
                except:
                    pass

            await cb.answer("✅ Заявка создана!", show_alert=True)
            await state.clear()
            return

        if data == "deposit_cancel":
            await state.clear()
            await cb.message.edit_text("❌ Депозит отменён")
            await cb.answer()
            return


        if data.startswith("blackjack_start_"):
                user_id = int(data.split('_')[2])
        
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
        
                data_state = await state.get_data()
                bet = data_state.get('blackjack_bet')
        
                if not bet:
                    await cb.answer("❌ Ошибка: ставка не найдена", show_alert=True)
                    return
        
                res = core.blackjack.start(uid, bet)
        
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    await state.clear()
                    return

                game = res['data']
        
                kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🃏 Взять", callback_data=f"bj_hit_{uid}"),
             InlineKeyboardButton(text="✋ Стоп", callback_data=f"bj_stand_{uid}")],
            [InlineKeyboardButton(text="🔁 Удвоить", callback_data=f"bj_double_{uid}")]
                ])
        
                dealer_card = core.blackjack.card_str(game['dealer_hand'][0])
                player_cards = core.blackjack.hand_str(game['player_hand'])
                player_value = game['player_value']
        
                await cb.message.edit_text(
            f"🃏 ОЧКО (21)\n\n"
            f"Дилер: {dealer_card} ❓\n"
            f"Ты: {player_cards} ({player_value})\n\n"
            f"💰 Ставка: {fmt(game['bet'])} {CURRENCY}\n"
            f"💳 Баланс: {fmt(game['bal'])} {CURRENCY}\n\n"
            f"Твой ход:",
            reply_markup=kb
             )
        
                await state.set_state(BlackjackStates.playing)
                await cb.answer()
                return

        if data == "blackjack_cancel":
            await state.clear()
            await cb.message.edit_text("❌ Игра отменена")
            await cb.answer()
            return

        if data.startswith("bj_hit_"):
            user_id = int(data.split('_')[2])
    
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return

            res = core.blackjack.hit(user_id)

            if not res['ok']:
                await cb.answer(res['msg'], show_alert=True)
                return

            if res.get('over'):
                await cb.message.edit_text(
                    f"🃏 РЕЗУЛЬТАТ\n\n"
            f"Дилер: {res['dealer_hand']}\n"
            f"Ты: {res['player_hand']}\n\n"
            f"{res['result_msg']}\n"
            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await state.clear()
                await cb.answer()
                return

            game = res['data']

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🃏 Взять", callback_data=f"bj_hit_{uid}"),
                 InlineKeyboardButton(text="✋ Стоп", callback_data=f"bj_stand_{uid}")],
                [InlineKeyboardButton(text="🔁 Удвоить", callback_data=f"bj_double_{uid}")]
            ])

            dealer_card = core.blackjack.card_str(game['dealer_hand'][0])
            player_cards = core.blackjack.hand_str(game['player_hand'])
            player_value = game['player_value']
            
            await cb.message.edit_text(
        f"🃏 ОЧКО (21)\n\n"
        f"Дилер: {dealer_card} ❓\n"
        f"Ты: {player_cards} ({player_value})\n\n"
        f"💰 Ставка: {fmt(game['bet'])} {CURRENCY}\n"
        f"💳 Баланс: {fmt(game['bal'])} {CURRENCY}\n\n"
        f"Твой ход:",
        reply_markup=kb
            )
            await cb.answer()
            return

        if data.startswith("bj_stand_"):
            user_id = int(data.split('_')[2])
    
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return
    
            res = core.blackjack.stand(user_id)
    
            if not res['ok']:
                await cb.answer(res['msg'], show_alert=True)
                return
    
            if res.get('over'):
                await cb.message.edit_text(
            f"🃏 РЕЗУЛЬТАТ\n\n"
            f"Дилер: {res['dealer_hand']}\n"
            f"Ты: {res['player_hand']}\n\n"
            f"{res['result_msg']}\n"
            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await state.clear()
                await cb.answer()
                return
    
            await cb.answer()

        if data.startswith("bj_double_"):
            user_id = int(data.split('_')[2])
    
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return

            res = core.blackjack.double(user_id)

            if not res['ok']:
                await cb.answer(res['msg'], show_alert=True)
                return

            if res.get('over'):
                await cb.message.edit_text(
            f"🃏 РЕЗУЛЬТАТ\n\n"
            f"Дилер: {res['dealer_hand']}\n"
            f"Ты: {res['player_hand']}\n\n"
            f"{res['result_msg']}\n"
            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await state.clear()
                await cb.answer()
                return
        
            await cb.answer()
        

        # ===== РАССЫЛКА =====
        if data == "mailing_confirm":
            await process_mailing_confirm(cb, state)
            return
        
        if data == "mailing_cancel":
            await process_mailing_cancel(cb, state)
            return
        
        # ===== МЕНЮ =====
        if data == "back_to_main":
            await cmd_start(cb.message)
            await cb.answer()
            return
        
        if data == "menu_crash":
            await cb.message.edit_text(
                "🚀 КРАШ\n\n"
                "Напишите: краш [ставка] [множитель]\n"
                "Пример: краш 10 2\n\n"
                "Множитель от 1.1 до 100"
            )
            await cb.answer()
            return
        
        if data == "menu_mines":
            await cb.message.edit_text(
                "💣 МИНЫ\n\n"
                "Напишите: мины [ставка] [количество мин]\n"
                "Пример: мины 10 3\n\n"
                "Мин от 1 до 6"
            )
            await cb.answer()
            return
        
        if data == "menu_games":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🪙 Монетка", callback_data="game_coin"),
                 InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice")],
                [InlineKeyboardButton(text="🎰 Слоты", callback_data="game_slots"),
                 InlineKeyboardButton(text="🏗️ Башня", callback_data="game_tower")],
                [InlineKeyboardButton(text="💎 Алмазы", callback_data="game_diamonds"),
                 InlineKeyboardButton(text="🔺 Пирамида", callback_data="game_pyramid")],
                [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football"),
                 InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basketball")],
                [InlineKeyboardButton(text="🎯 Дартс", callback_data="game_dart"),
                 InlineKeyboardButton(text="🐸 Квак", callback_data="game_quack")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            
            await cb.message.edit_text(
                "🎮 ВЫБЕРИТЕ ИГРУ\n\n"
                "🪙 Монетка - x1.96\n"
                "🎲 Кубик - разные ставки\n"
                "🎰 Слоты - x3-x10\n"
                "🏗️ Башня - до x7\n"
                "💎 Алмазы - до x7.3\n"
                "🔺 Пирамида - до x46\n"
                "⚽ Футбол - гол x1.6, мимо x2.2\n"
                "🏀 Баскетбол - попадание x2.2, мимо x1.6\n"
                "🎯 Дартс - центр x5.4, красное x1.6, белое x2.5, мимо x5.4\n"
                "🐸 Квак - до x23.55",
                reply_markup=kb
            )
            await cb.answer()
            return
        
        if data.startswith("game_"):
            game = data[5:]
            game_info = {
                'coin': 'монетка [ставка] [орёл/решка]',
                'dice': 'кубик [ставка] [число/ч/н/р/б/м]',
                'slots': 'слоты [ставка]',
                'tower': 'башня [ставка] [мин]',
                'diamonds': 'алмазы [ставка] [мин]',
                'pyramid': 'пирамида [ставка] [дверей]',
                'football': 'футбол [ставка] [гол/мимо]',
                'basketball': 'баскетбол [ставка] [попадание/мимо]',
                'dart': 'дартс [ставка] [ц/к/б/м]',
                'quack': 'квак [ставка]'
            }
            await cb.message.edit_text(
                f"📝 Напишите команду для игры:\n\n"
                f"{game_info.get(game, '')}\n\n"
                f"Пример: {game} 10 ..."
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
            await cb.message.edit_text(
                f"💰 ДЕПОЗИТ\n\n"
                f"Напишите: депозит [сумма]\n"
                f"Пример: депозит 1000\n\n"
                f"Форматы: 1000, 5к, 50к, 1кк"
            )
            await cb.answer()
            return
        
        if data == "wallet_withdraw":
            await cb.message.edit_text(
                f"📤 ВЫВОД\n\n"
                f"Напишите: вывод [сумма] [@username]\n"
                f"Пример: вывод 100 @username\n\n"
                f"Форматы: 100, 1к"
            )
            await cb.answer()
            return
        
        if data == "wallet_requests":
            await cmd_my_requests(cb.message)
            await cb.answer()
            return
        
        # ===== АДМИН =====
        if data == "admin_deposits":
            await cmd_pending_deposits(cb.message)
            await cb.answer()
            return
        
        if data == "admin_withdraws":
            await cmd_pending_withdraws(cb.message)
            await cb.answer()
            return
        
        # ===== ЗАЯВКИ =====
        if data.startswith("deposit_approve_"):
            if not is_admin(uid):
                await cb.answer("❌ Только для админов", show_alert=True)
                return
            
            request_id = int(data.split('_')[2])
            request = core.deposits.get_by_id(request_id)
            
            if not request or request['status'] != 'pending':
                await cb.answer("❌ Заявка не найдена или уже обработана", show_alert=True)
                return
            
            core.deposits.approve(request_id, uid)
            user = core.db.get(request['user_id'])
            core.db.update(request['user_id'], 
                          balance=user['balance'] + request['amount'],
                          total_deposited=user.get('total_deposited', 0) + request['amount'])
            
            await cb.message.edit_text(f"✅ Заявка #{request_id} одобрена! Начислено {fmt(request['amount'])} {CURRENCY}")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"✅ ДЕПОЗИТ ОДОБРЕН!\n\n"
                    f"💰 +{fmt(request['amount'])} {CURRENCY}\n"
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
            
            if not request or request['status'] != 'pending':
                await cb.answer("❌ Заявка не найдена или уже обработана", show_alert=True)
                return
            
            core.deposits.reject(request_id, uid)
            await cb.message.edit_text(f"❌ Заявка #{request_id} отклонена")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"❌ ДЕПОЗИТ ОТКЛОНЁН\n\n"
                    f"📋 Заявка #{request_id}"
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
            
            if not request or request['status'] != 'pending':
                await cb.answer("❌ Заявка не найдена или уже обработана", show_alert=True)
                return
            
            core.withdraws.approve(request_id, uid)
            await cb.message.edit_text(f"✅ Заявка #{request_id} одобрена!")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"✅ ВЫВОД ОДОБРЕН!\n\n"
                    f"💰 -{fmt(request['amount'])} {CURRENCY}\n"
                    f"👤 username: {request['address']}"
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
            
            if not request or request['status'] != 'pending':
                await cb.answer("❌ Заявка не найдена или уже обработана", show_alert=True)
                return
            
            user = core.db.get(request['user_id'])
            core.db.update(request['user_id'], balance=user['balance'] + request['amount'])
            core.withdraws.reject(request_id, uid)
            
            await cb.message.edit_text(f"❌ Заявка #{request_id} отклонена, средства возвращены")
            
            try:
                await cb.bot.send_message(
                    request['user_id'],
                    f"❌ ВЫВОД ОТКЛОНЁН\n\n"
                    f"📋 Заявка #{request_id}\n"
                    f"💰 Средства возвращены на баланс"
                )
            except:
                pass
            
            await cb.answer()
            return
        
        # ===== МИНЫ =====
        if data.startswith("mines_"):
            parts = data.split('_')
            if len(parts) == 4 and parts[0] == 'mines':
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
            await cmd_start(cb.message)
            await cb.answer()
            return
        
        # ===== БАШНЯ =====
        if data.startswith("tower_"):
            if data.startswith("tower_cash_"):
                user_id = int(data.split('_')[2])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.tower.cashout(user_id)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                await cb.message.edit_text(
                    f"🏆 ВЫИГРЫШ ЗАБРАН!\n"
                    f"+{fmt(res['won'])} {CURRENCY}\n"
                    f"📈 x{res['mult']:.1f}\n"
                    f"🎯 Пройдено {res['rows']}/9 этажей\n"
                    f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await cb.answer()
                return
            
            parts = data.split('_')
            if len(parts) == 4:
                user_id, r, c = int(parts[1]), int(parts[2]), int(parts[3])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.tower.open(user_id, r, c)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                if res.get('over'):
                    if res.get('mine'):
                        await cb.message.edit_text(f"💥 БУМ! МИНА!\n❌ Потеряно: -{fmt(res['bet'])} {CURRENCY}")
                    else:
                        await cb.message.edit_text(
                            f"🏆 ПОБЕДА! БАШНЯ ПРОЙДЕНА!\n"
                            f"+{fmt(res['won'])} {CURRENCY}\n"
                            f"📈 x{res['mult']:.1f}\n"
                            f"🎯 Пройдено {res['rows']}/9 этажей\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                        )
                else:
                    game = core.tower.games.get(user_id)
                    if game:
                        await cb.message.edit_text(
                            f"🏗️ БАШНЯ | {game['row'] + 1}/9 | 💣 {game['mines']} мины\n"
                            f"💰 Ставка {fmt(game['bet'])} {CURRENCY}\n"
                            f"📈 Множитель x{res['mult']:.1f}\n"
                            f"💎 Выигрыш: {fmt(res['won'])} {CURRENCY}\n\n"
                            f"Выберите клетку:",
                            reply_markup=core.tower.kb(user_id, game)
                        )
                await cb.answer()
                return
        
        # ===== АЛМАЗЫ =====
        if data.startswith("diamonds_"):
            if data.startswith("diamonds_cash_"):
                user_id = int(data.split('_')[2])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.diamonds.cashout(user_id)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                await cb.message.edit_text(
                    f"🏆 ВЫИГРЫШ ЗАБРАН!\n"
                    f"+{fmt(res['won'])} {CURRENCY}\n"
                    f"📈 x{res['mult']:.1f}\n"
                    f"🎯 Пройдено {res['rows']}/9 этажей\n"
                    f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await cb.answer()
                return
            
            parts = data.split('_')
            if len(parts) == 4:
                user_id, r, c = int(parts[1]), int(parts[2]), int(parts[3])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.diamonds.open(user_id, r, c)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                if res.get('over'):
                    if res.get('mine'):
                        await cb.message.edit_text(f"💥 БУМ! МИНА!\n❌ Потеряно: -{fmt(res['bet'])} {CURRENCY}")
                    else:
                        await cb.message.edit_text(
                            f"🏆 ПОБЕДА! ВСЕ АЛМАЗЫ СОБРАНЫ!\n"
                            f"+{fmt(res['won'])} {CURRENCY}\n"
                            f"📈 x{res['mult']:.1f}\n"
                            f"🎯 Пройдено {res['rows']}/9 этажей\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                        )
                else:
                    game = core.diamonds.games.get(user_id)
                    if game:
                        await cb.message.edit_text(
                            f"💎 АЛМАЗЫ | {game['row'] + 1}/9 | 💣 {game['mines']} мины\n"
                            f"💰 Ставка {fmt(game['bet'])} {CURRENCY}\n"
                            f"📈 Множитель x{res['mult']:.1f}\n"
                            f"💎 Выигрыш: {fmt(res['won'])} {CURRENCY}\n\n"
                            f"Выберите клетку:",
                            reply_markup=core.diamonds.kb(user_id, game)
                        )
                await cb.answer()
                return
        
        # ===== ПИРАМИДА =====
        if data.startswith("pyramid_"):
            if data.startswith("pyramid_cash_"):
                user_id = int(data.split('_')[2])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.pyramid.cashout(user_id)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                await cb.message.edit_text(
                    f"🏆 ВЫИГРЫШ ЗАБРАН!\n"
                    f"+{fmt(res['won'])} {CURRENCY}\n"
                    f"📈 x{res['mult']:.2f}\n"
                    f"🎯 Пройдено {res['level']}/12 уровней\n"
                    f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await cb.answer()
                return
            
            parts = data.split('_')
            if len(parts) == 4:
                user_id, level, cell = int(parts[1]), int(parts[2]), int(parts[3])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.pyramid.open(user_id, level, cell)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                if res.get('over'):
                    if res.get('win'):
                        await cb.message.edit_text(
                            f"🏆 ПОБЕДА! ПИРАМИДА ПРОЙДЕНА!\n"
                            f"+{fmt(res['won'])} {CURRENCY}\n"
                            f"📈 x{res['mult']:.2f}\n"
                            f"🎯 Пройдено {res['level']}/12 уровней\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                        )
                    else:
                        await cb.message.edit_text(
                            f"💀 ЛОВУШКА!\n"
                            f"❌ Потеряно: -{fmt(res['bet'])} {CURRENCY}\n"
                            f"🎯 Пройдено {res['level']}/12 уровней"
                        )
                else:
                    game = core.pyramid.games.get(user_id)
                    if game:
                        await cb.message.edit_text(
                            f"🔺 ПИРАМИДА | {game['level'] + 1}/12 | 🚪 {game['doors']} двери\n"
                            f"💰 Ставка {fmt(game['bet'])} {CURRENCY}\n"
                            f"📈 Множитель x{res['mult']:.2f}\n"
                            f"💎 Выигрыш: {fmt(res['won'])} {CURRENCY}\n\n"
                            f"Выберите дверь:",
                            reply_markup=core.pyramid.kb(user_id, game)
                        )
                await cb.answer()
                return
        
        # ===== КВАК =====
        if data.startswith("quack_"):
            if data.startswith("quack_cash_"):
                user_id = int(data.split('_')[2])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.quack.cashout(user_id)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                await cb.message.edit_text(
                    f"🐸 КВАК\n\n"
                    f"🏆 ВЫИГРЫШ ЗАБРАН!\n"
                    f"+{fmt(res['won'])} {CURRENCY}\n"
                    f"📈 x{res['mult']}\n"
                    f"🎯 Пройдено {res['row'] + 1}/4 рядов\n"
                    f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                )
                await cb.answer()
                return
            
            parts = data.split('_')
            if len(parts) == 4:
                user_id, r, c = int(parts[1]), int(parts[2]), int(parts[3])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                res = core.quack.open(user_id, r, c)
                if not res['ok']:
                    await cb.answer(res['msg'], show_alert=True)
                    return
                if res.get('over'):
                    if res.get('mine'):
                        await cb.message.edit_text(
                            f"🐸 КВАК\n\n"
                            f"💥 БУМ! {res['row_idx']} ряд ({res['mines_count']} мины)\n"
                            f"❌ Потеряно: -{fmt(res['bet'])} {CURRENCY}"
                        )
                    else:
                        await cb.message.edit_text(
                            f"🐸 КВАК\n\n"
                            f"🏆 ПОБЕДА! ВСЕ РЯДЫ ПРОЙДЕНЫ!\n"
                            f"+{fmt(res['won'])} {CURRENCY}\n"
                            f"📈 x{res['mult']}\n"
                            f"🎯 Пройдено {res['row']}/4 рядов\n"
                            f"💰 Баланс: {fmt(res['balance'])} {CURRENCY}"
                        )
                else:
                    game = core.quack.games.get(user_id)
                    if game:
                        await cb.message.edit_text(
                            f"🐸 КВАК | {game['row'] + 1}/4 | 💣 {core.quack.mines_count[game['row']]} мины\n"
                            f"💰 Ставка {fmt(game['bet'])} {CURRENCY}\n"
                            f"📈 Множитель x{res['mult']}\n"
                            f"💎 Выигрыш: {fmt(res['won'])} {CURRENCY}\n\n"
                            f"Выберите клетку:",
                            reply_markup=core.quack.kb(user_id, game)
                        )
                await cb.answer()
                return
        
        if data == "ignore":
            await cb.answer()
            return
        
        await cb.answer()
    except Exception as e:
        print(f"Ошибка: {e}")
        await cb.answer("❌ Ошибка", show_alert=True)

# ===================== ЗАПУСК =====================

async def main():
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.get_me()
        print("✅ Бот DeathGmp запущен!")
        print(f"👑 Админы: {ADMIN_IDS}")
        print(f"💰 Валюта: {CURRENCY}")
        print(f"🎯 Шанс выигрыша во всех играх: 35%")
        print("🎮 Доступные игры: Краш, Мины, Монетка, Кубик, Слоты, Башня, Алмазы, Пирамида, Футбол, Баскетбол, Дартс, Квак")
        print("📨 Доступна команда 'расс' для массовой рассылки")
        
        dp = Dispatcher(storage=MemoryStorage())
        
        dp.message.register(cmd_start, Command("start"))
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
