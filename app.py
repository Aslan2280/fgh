#!/usr/bin/env python3
"""
GMPLAY — Telegram Bot + WebApp с JSON-базой данных
"""

import hashlib
import hmac
import http.server
import json
import os
import socketserver
import threading
import time
import urllib.parse
import random
import math
import requests
from datetime import datetime

# ==================== КОНФИГУРАЦИЯ ====================

HOST = "127.0.0.1"
PORT = 56480
BOT_TOKEN = "8986114517:AAHoIHq-Kgk6iYEfxQLWsqN_sMNHc2pRYL8"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gmplay.json")
STATIC_DIR = BASE_DIR

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

# WebApp URL (для CloudFlare Pages)
WEBAPP_URL = "https://your-project.pages.dev"  # Замените на ваш URL

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
        "admins": [{"tg_id": 123456789}],  # Замените на ваш TG ID
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


def get_user(tg_id):
    with _db_lock:
        db = _load_db()
        return db["users"].get(str(tg_id))


def get_user_by_id(user_id):
    with _db_lock:
        db = _load_db()
        for user in db["users"].values():
            if user.get("id") == user_id:
                return user
        return None


def get_all_users():
    with _db_lock:
        db = _load_db()
        return list(db["users"].values())


def is_admin(tg_id):
    with _db_lock:
        db = _load_db()
        return any(a.get("tg_id") == tg_id for a in db.get("admins", []))


def get_or_create_user(tg_user, start_param=None):
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


def is_user_banned(tg_id):
    with _db_lock:
        db = _load_db()
        return db["banned_users"].get(str(tg_id))


def ban_user(tg_id, reason, banned_by):
    with _db_lock:
        db = _load_db()
        now = int(time.time() * 1000)
        db["banned_users"][str(tg_id)] = {
            "reason": reason,
            "banned_at": now,
            "banned_by": banned_by
        }
        _save_db(db)


def unban_user(tg_id):
    with _db_lock:
        db = _load_db()
        db["banned_users"].pop(str(tg_id), None)
        _save_db(db)


def user_public(user):
    return {
        "user_id": user["id"],
        "tg_id": user["tg_id"],
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "username": user.get("username", ""),
        "photo_url": user.get("photo_url", ""),
        "balance": user["balance"],
        "ref_code": ref_code_for(user["tg_id"]),
        "is_admin": is_admin(user["tg_id"]),
    }


def ref_code_for(tg_id):
    return "GM" + format(int(tg_id), "x").upper()


# ==================== БОТ-ФУНКЦИИ ====================

def send_telegram_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        print(f"[BOT] Ошибка отправки: {e}")
        return None


def send_start_message(chat_id, start_param=None):
    webapp_url = WEBAPP_URL
    if start_param:
        webapp_url += f"?startapp={start_param}"

    is_admin_user = is_admin(chat_id)
    admin_button = []
    if is_admin_user:
        admin_button = [
            {
                "text": "⚙️ Админ-панель",
                "web_app": {"url": f"{WEBAPP_URL}/admin.html"}
            }
        ]

    text = (
        "🚀 <b>Добро пожаловать в GMPLAY!</b>\n\n"
        "🎮 <b>Твоя победа начинается здесь!</b>\n\n"
        "⚡ Играй в <b>Краш</b> — забирай выигрыш до того, как ракета улетит!\n"
        "💰 Получай ежедневный бонус\n"
        "👥 Приглашай друзей и зарабатывай 1% от их проигрышей\n"
        "💸 Выводи средства в любой момент\n\n"
        "👇 <b>Нажми на кнопку ниже, чтобы начать!</b>"
    )

    keyboard = [
        [
            {
                "text": "🎮 Играть в GMPLAY",
                "web_app": {"url": webapp_url}
            }
        ]
    ]
    if admin_button:
        keyboard.append(admin_button)
    keyboard.append([
        {
            "text": "📢 Канал",
            "url": "https://t.me/gmplay_news"
        },
        {
            "text": "🛟 Поддержка",
            "url": "https://t.me/gmp_help"
        }
    ])

    reply_markup = {"inline_keyboard": keyboard}
    send_telegram_message(chat_id, text, reply_markup)


# ==================== КРАШ-ИГРА (без изменений) ====================

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


def crash_public_state(user_id=None):
    with crash_lock:
        phase = crash_state["phase"]
        multiplier = crash_current_multiplier()
        my_bets = []
        all_bets = []

        for bet_id, bet in crash_state["bets"].items():
            b = dict(bet)
            b["bet_id"] = bet_id
            all_bets.append(b)
            if user_id and bet.get("user_id") == user_id:
                my_bets.append(b)

        all_bets.sort(key=lambda x: x["amount"], reverse=True)

        return {
            "phase": phase,
            "round_id": crash_state["round_id"],
            "phase_started_at": crash_state["phase_started_at"],
            "server_now": int(time.time() * 1000),
            "betting_duration": CRASH_BETTING_MS,
            "pause_duration": CRASH_PAUSE_MS,
            "multiplier": round(multiplier, 2),
            "crash_point": crash_state["crash_point"] if phase == "crashed" else None,
            "history": crash_state.get("history", []),
            "my_bets": my_bets,
            "all_bets": all_bets,
            "total_players": len({b["tg_id"] for b in crash_state["bets"].values()}),
            "total_bets": len(crash_state["bets"]),
        }


# ==================== WEBHOOK ====================

def handle_telegram_webhook(body):
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "invalid_json"}

    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text.startswith("/start"):
            start_param = None
            if " " in text:
                start_param = text.split(" ", 1)[1] if len(text.split(" ", 1)) > 1 else None

            tg_user = {
                "id": message["from"]["id"],
                "first_name": message["from"].get("first_name", ""),
                "last_name": message["from"].get("last_name", ""),
                "username": message["from"].get("username", ""),
            }

            if is_user_banned(tg_user["id"]):
                send_telegram_message(
                    chat_id,
                    "⛔ <b>Доступ запрещён</b>\n\nВы были забанены. Обратитесь в поддержку."
                )
                return {"ok": True}

            user, is_new = get_or_create_user(tg_user, start_param)
            send_start_message(chat_id, start_param)
            return {"ok": True}

    return {"ok": True}


# ==================== HTTP СЕРВЕР ====================

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/webhook":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if parsed.path.startswith("/api/"):
            return self._route_api("GET", parsed)

        # Отдаём admin.html для админки
        if parsed.path == "/admin.html":
            return super().do_GET()

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/webhook":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            result = handle_telegram_webhook(body)
            self._send_json(result)
            return

        if parsed.path.startswith("/api/"):
            return self._route_api("POST", parsed)
        self.send_error(404)

    def _route_api(self, method, parsed):
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            # Основные API
            if method == "POST" and path == "/api/init":
                return self._api_init()
            if method == "GET" and path == "/api/state":
                return self._api_state(qs)
            if method == "POST" and path == "/api/daily-bonus":
                return self._api_daily_bonus()
            if method == "POST" and path == "/api/bet":
                return self._api_bet()
            if method == "GET" and path == "/api/history":
                return self._api_history(qs)
            if method == "GET" and path == "/api/referrals":
                return self._api_referrals(qs)
            if method == "GET" and path == "/api/leaderboard":
                return self._api_leaderboard(qs)
            if method == "GET" and path == "/api/stats":
                return self._api_stats()
            if method == "GET" and path == "/api/profile-stats":
                return self._api_profile_stats(qs)
            if method == "GET" and path == "/api/withdraw-requests":
                return self._api_withdraw_requests(qs)
            if method == "GET" and path == "/api/withdraw/check":
                return self._api_withdraw_check(qs)
            if method == "POST" and path == "/api/withdraw/create":
                return self._api_withdraw_create()
            if method == "POST" and path == "/api/withdraw/update-status":
                return self._api_withdraw_update_status()
            if method == "POST" and path == "/api/deposit/webhook":
                return self._api_deposit_webhook()
            if method == "GET" and path == "/api/crash/state":
                return self._api_crash_state(qs)
            if method == "POST" and path == "/api/crash/bet":
                return self._api_crash_bet()
            if method == "POST" and path == "/api/crash/cashout":
                return self._api_crash_cashout()

            # Админ API
            if method == "GET" and path == "/api/admin/users":
                return self._api_admin_users()
            if method == "GET" and path == "/api/admin/withdraw-requests":
                return self._api_admin_withdraw_requests()
            if method == "GET" and path == "/api/admin/deposit-requests":
                return self._api_admin_deposit_requests()
            if method == "POST" and path == "/api/admin/ban":
                return self._api_admin_ban()
            if method == "POST" and path == "/api/admin/unban":
                return self._api_admin_unban()
            if method == "POST" and path == "/api/admin/withdraw/approve":
                return self._api_admin_withdraw_approve()
            if method == "POST" and path == "/api/admin/withdraw/reject":
                return self._api_admin_withdraw_reject()
            if method == "POST" and path == "/api/admin/deposit/approve":
                return self._api_admin_deposit_approve()

            self._send_json({"error": "not_found"}, 404)
        except Exception as e:
            print("API error:", e)
            import traceback
            traceback.print_exc()
            self._send_json({"error": "server_error", "detail": str(e)}, 500)

    # ==================== ОСНОВНЫЕ API ====================

    def _api_init(self):
        body = self._read_json()
        init_data = body.get("initData", "")

        if not init_data:
            return self._send_json({
                "guest": True,
                "user_id": None,
                "balance": STARTING_BALANCE,
                "first_name": "Гость",
                "last_name": "",
                "ref_code": None,
                "is_admin": False,
                "message": "Открой приложение через Telegram-бота",
            })

        data = self._verify_init_data(init_data)
        if data is None:
            return self._send_json({"error": "invalid_init_data"}, 401)

        try:
            tg_user = json.loads(data.get("user", "{}"))
        except Exception:
            tg_user = {}

        if not tg_user.get("id"):
            return self._send_json({"error": "no_user_in_init_data"}, 400)

        banned = is_user_banned(tg_user["id"])
        if banned:
            return self._send_json({
                "error": "banned",
                "reason": banned.get("reason", "Нарушение правил"),
                "message": f"⛔ Вы были забанены по причине: {banned.get('reason', 'Нарушение правил')}"
            }, 403)

        start_param = data.get("start_param", "") or body.get("start_param", "")
        user, is_new = get_or_create_user(tg_user, start_param)

        result = user_public(user)
        result["guest"] = False
        result["is_new"] = is_new
        self._send_json(result)

    def _verify_init_data(self, init_data):
        if not init_data:
            return None
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        data = dict(pairs)
        received_hash = data.pop("hash", None)
        if not BOT_TOKEN:
            return data
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed_hash, received_hash):
            return None
        return data

    def _api_state(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0)
        user = get_user_by_id(user_id)
        if not user:
            return self._send_json({"error": "user_not_found"}, 404)

        now = int(time.time() * 1000)
        last = user.get("last_bonus_at")
        ready = (last is None) or (now - last >= DAILY_BONUS_PERIOD_MS)

        self._send_json({
            "balance": user["balance"],
            "daily_bonus_ready": ready,
            "daily_bonus_next_at": None if ready else last + DAILY_BONUS_PERIOD_MS,
            "is_admin": is_admin(user["tg_id"]),
        })

    def _api_daily_bonus(self):
        body = self._read_json()
        user_id = int(body.get("user_id") or 0)

        with _db_lock:
            db = _load_db()
            user = None
            for u in db["users"].values():
                if u.get("id") == user_id:
                    user = u
                    break

            if not user:
                return self._send_json({"error": "user_not_found"}, 404)

            now = int(time.time() * 1000)
            last = user.get("last_bonus_at")

            if last is not None and now - last < DAILY_BONUS_PERIOD_MS:
                return self._send_json({
                    "granted": False,
                    "next_available_at": last + DAILY_BONUS_PERIOD_MS,
                    "balance": user["balance"],
                })

            user["balance"] += DAILY_BONUS_AMOUNT
            user["last_bonus_at"] = now
            _save_db(db)

            self._send_json({
                "granted": True,
                "balance": user["balance"],
                "amount": DAILY_BONUS_AMOUNT
            })

    def _api_bet(self):
        body = self._read_json()
        user_id = int(body.get("user_id") or 0)
        bet_amount = int(body.get("bet_amount") or 0)
        multiplier = float(body.get("multiplier") or 0)
        result = body.get("result")
        win_amount = int(body.get("win_amount") or 0)
        game = body.get("game", "crash")

        if result not in ("win", "lose") or bet_amount <= 0:
            return self._send_json({"error": "bad_request"}, 400)

        with _db_lock:
            db = _load_db()
            user = None
            for u in db["users"].values():
                if u.get("id") == user_id:
                    user = u
                    break

            if not user:
                return self._send_json({"error": "user_not_found"}, 404)

            net = (win_amount - bet_amount) if result == "win" else -bet_amount
            user["balance"] = max(0, user["balance"] + net)

            now = int(time.time() * 1000)
            db["bets"].append({
                "id": db["_meta"]["bet_id_counter"],
                "user_id": user_id,
                "tg_id": user["tg_id"],
                "game": game,
                "bet_amount": bet_amount,
                "multiplier": multiplier,
                "result": result,
                "win_amount": win_amount,
                "created_at": now,
            })
            db["_meta"]["bet_id_counter"] += 1
            _save_db(db)

            self._send_json({"balance": user["balance"]})

    def _api_history(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0)
        limit = int(qs.get("limit", [30])[0] or 30)

        with _db_lock:
            db = _load_db()
            user_bets = [b for b in db["bets"] if b.get("user_id") == user_id]
            user_bets.sort(key=lambda x: x["created_at"], reverse=True)
            user_bets = user_bets[:limit]

            items = [{
                "game": b["game"],
                "bet_amount": b["bet_amount"],
                "multiplier": b["multiplier"],
                "result": b["result"],
                "win_amount": b["win_amount"],
                "created_at": b["created_at"],
            } for b in user_bets]

            self._send_json({"items": items})

    def _api_referrals(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0)

        with _db_lock:
            db = _load_db()
            friends = []
            total_earned = 0

            for user in db["users"].values():
                if user.get("referred_by") == user_id:
                    lost = sum(b["bet_amount"] for b in db["bets"]
                               if b.get("user_id") == user["id"] and b.get("result") == "lose")
                    earned = round(lost * REFERRAL_CUT, 2)
                    total_earned += earned
                    friends.append({
                        "first_name": user.get("first_name", ""),
                        "last_name": user.get("last_name", ""),
                        "lost_amount": lost,
                        "earned": earned,
                        "joined_at": user.get("created_at"),
                    })

            self._send_json({
                "count": len(friends),
                "total_earned": round(total_earned, 2),
                "friends": friends,
            })

    def _api_leaderboard(self, qs):
        period = qs.get("period", ["day"])[0]
        now = int(time.time() * 1000)
        window = {"day": 86400000, "week": 7 * 86400000, "month": 30 * 86400000}.get(period, 86400000)
        since = now - window

        with _db_lock:
            db = _load_db()
            stats = {}

            for bet in db["bets"]:
                if bet["created_at"] < since:
                    continue
                uid = bet["user_id"]
                if uid not in stats:
                    stats[uid] = 0
                if bet["result"] == "win":
                    stats[uid] += bet["win_amount"] - bet["bet_amount"]
                else:
                    stats[uid] -= bet["bet_amount"]

            items = []
            for uid, net in sorted(stats.items(), key=lambda x: x[1], reverse=True)[:20]:
                if net <= 0:
                    continue
                user = get_user_by_id(uid)
                if user:
                    items.append({
                        "first_name": user.get("first_name", ""),
                        "last_name": user.get("last_name", ""),
                        "net": net,
                    })

            self._send_json({"items": items})

    def _api_stats(self):
        with _db_lock:
            db = _load_db()
            now = int(time.time() * 1000)
            five_min_ago = now - 5 * 60 * 1000
            today_start = now - (now % 86400000)

            active = len(set(b.get("user_id") for b in db["bets"] if b["created_at"] >= five_min_ago))
            won_today = sum(b["win_amount"] for b in db["bets"]
                            if b["result"] == "win" and b["created_at"] >= today_start)

            self._send_json({
                "active_now": active,
                "won_today": won_today,
                "total_players": len(db["users"]),
            })

    def _api_profile_stats(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0)

        with _db_lock:
            db = _load_db()
            user_bets = [b for b in db["bets"] if b.get("user_id") == user_id]

            bets_count = len(user_bets)
            total_won = sum(b["win_amount"] for b in user_bets if b["result"] == "win")
            total_wagered = sum(b["bet_amount"] for b in user_bets)

            self._send_json({
                "bets_count": bets_count,
                "total_won": total_won,
                "total_wagered": total_wagered,
            })

    def _api_withdraw_requests(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0)

        with _db_lock:
            db = _load_db()
            requests = [r for r in db["withdraw_requests"] if r.get("user_id") == user_id]
            requests.sort(key=lambda x: x["created_at"], reverse=True)

            items = [{
                "id": r["id"],
                "amount": r["amount"],
                "status": r["status"],
                "created_at": r["created_at"],
                "processed_at": r.get("processed_at"),
                "reject_reason": r.get("reject_reason"),
            } for r in requests]

            self._send_json({"items": items})

    def _api_withdraw_check(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0)
        amount = int(qs.get("amount", [0])[0] or 0)

        with _db_lock:
            db = _load_db()
            user = None
            for u in db["users"].values():
                if u.get("id") == user_id:
                    user = u
                    break

            if not user:
                return self._send_json({"eligible": False, "reason": "Пользователь не найден"})

            if user["balance"] < amount:
                return self._send_json({"eligible": False, "reason": f"Недостаточно GMP. Доступно: {user['balance']}"})

            if amount < MIN_BALANCE_FOR_WITHDRAW:
                return self._send_json({"eligible": False, "reason": f"Минимальная сумма вывода: {MIN_BALANCE_FOR_WITHDRAW} GMP"})

            week_ago = int(time.time() * 1000) - WITHDRAW_WEEK_MS
            deposits = [d for d in db["deposits"] if d.get("user_id") == user_id and d["processed_at"] >= week_ago]
            total_deposits = sum(d["amount"] for d in deposits)

            if total_deposits < MIN_DEPOSIT_FOR_WITHDRAW:
                return self._send_json({
                    "eligible": False,
                    "reason": f"Для вывода нужен депозит от {MIN_DEPOSIT_FOR_WITHDRAW} GMP за последнюю неделю. Ваш депозит: {total_deposits} GMP"
                })

            self._send_json({"eligible": True, "reason": None})

    def _api_withdraw_create(self):
        body = self._read_json()
        user_id = int(body.get("user_id") or 0)
        amount = int(body.get("amount") or 0)

        with _db_lock:
            db = _load_db()
            user = None
            for u in db["users"].values():
                if u.get("id") == user_id:
                    user = u
                    break

            if not user:
                return self._send_json({"error": "user_not_found"}, 404)

            if user["balance"] < amount:
                return self._send_json({"error": "Недостаточно GMP"}, 400)
            if amount < MIN_BALANCE_FOR_WITHDRAW:
                return self._send_json({"error": f"Минимальная сумма вывода: {MIN_BALANCE_FOR_WITHDRAW} GMP"}, 400)

            week_ago = int(time.time() * 1000) - WITHDRAW_WEEK_MS
            deposits = [d for d in db["deposits"] if d.get("user_id") == user_id and d["processed_at"] >= week_ago]
            total_deposits = sum(d["amount"] for d in deposits)

            if total_deposits < MIN_DEPOSIT_FOR_WITHDRAW:
                return self._send_json({
                    "error": f"Для вывода нужен депозит от {MIN_DEPOSIT_FOR_WITHDRAW} GMP за последнюю неделю"
                }, 400)

            user["balance"] -= amount
            now = int(time.time() * 1000)

            request_id = db["_meta"]["withdraw_id_counter"]
            db["_meta"]["withdraw_id_counter"] += 1

            db["withdraw_requests"].append({
                "id": request_id,
                "user_id": user_id,
                "amount": amount,
                "status": "pending",
                "created_at": now,
            })

            _save_db(db)

            self._send_json({
                "request_id": request_id,
                "amount": amount,
                "status": "pending",
                "created_at": now,
                "balance": user["balance"],
            })

    def _api_withdraw_update_status(self):
        body = self._read_json()
        request_id = int(body.get("request_id", 0))
        status = body.get("status", "")
        reason = body.get("reason", None)

        if not request_id or status not in ("pending", "processing", "approved", "rejected", "paid"):
            return self._send_json({"error": "invalid_data"}, 400)

        with _db_lock:
            db = _load_db()
            for req in db["withdraw_requests"]:
                if req["id"] == request_id:
                    req["status"] = status
                    req["processed_at"] = int(time.time() * 1000)
                    if reason:
                        req["reject_reason"] = reason
                    break

            _save_db(db)
            self._send_json({"success": True})

    def _api_deposit_webhook(self):
        body = self._read_json()
        from_name = body.get("from_name", "")
        amount = int(body.get("amount", 0))
        user_id = body.get("user_id")
        tx_id = body.get("tx_id", "")

        if not from_name or amount <= 0:
            return self._send_json({"error": "invalid_data"}, 400)

        with _db_lock:
            db = _load_db()

            if tx_id:
                for d in db["deposits"]:
                    if d.get("tx_id") == tx_id:
                        return self._send_json({"success": True, "already_processed": True})

            user = None
            if user_id:
                for u in db["users"].values():
                    if u.get("id") == user_id:
                        user = u
                        break
            else:
                for u in db["users"].values():
                    if u.get("first_name") == from_name or u.get("username") == from_name:
                        user = u
                        break

            if not user:
                return self._send_json({"error": "user_not_found"}, 404)

            now = int(time.time() * 1000)
            user["balance"] += amount

            db["deposits"].append({
                "id": db["_meta"]["deposit_id_counter"],
                "user_id": user["id"],
                "amount": amount,
                "from_name": from_name,
                "processed_at": now,
                "tx_id": tx_id or f"dep_{now}_{user['id']}",
                "status": "approved",
            })
            db["_meta"]["deposit_id_counter"] += 1
            _save_db(db)

            self._send_json({"success": True, "new_balance": user["balance"]})

    def _api_crash_state(self, qs):
        user_id = int(qs.get("user_id", [0])[0] or 0) or None
        with _db_lock:
            db = _load_db()
            crash_state["history"] = db["crash"]["history"]

        if user_id:
            user = get_user_by_id(user_id)
            user_id = user["id"] if user else None

        self._send_json(crash_public_state(user_id))

    def _api_crash_bet(self):
        global crash_next_bet_id
        body = self._read_json()
        user_id = int(body.get("user_id") or 0)
        amount = int(body.get("amount") or 0)
        auto_cashout = body.get("auto_cashout")
        auto_cashout = float(auto_cashout) if auto_cashout else None

        if amount < CRASH_MIN_BET:
            return self._send_json({"error": "min_bet", "min_bet": CRASH_MIN_BET}, 400)

        user = get_user_by_id(user_id)
        if not user:
            return self._send_json({"error": "user_not_found"}, 404)

        if user["balance"] < amount:
            return self._send_json({"error": "insufficient_balance"}, 400)

        with crash_lock:
            if crash_state["phase"] != "betting":
                return self._send_json({"error": "betting_closed"}, 400)

            user_bets_count = sum(1 for b in crash_state["bets"].values() if b.get("user_id") == user_id)
            if user_bets_count >= CRASH_MAX_BETS_PER_USER:
                return self._send_json({"error": "max_bets_reached"}, 400)

            with _db_lock:
                db = _load_db()
                key = str(user["tg_id"])
                if key in db["users"]:
                    db["users"][key]["balance"] -= amount
                _save_db(db)
                user["balance"] -= amount

            bet_id = crash_next_bet_id
            crash_next_bet_id += 1

            crash_state["bets"][bet_id] = {
                "user_id": user_id,
                "tg_id": user["tg_id"],
                "amount": amount,
                "auto_cashout": auto_cashout,
                "status": "active",
                "first_name": user.get("first_name", ""),
                "last_name": user.get("last_name", ""),
            }

        self._send_json({"bet_id": bet_id, "balance": user["balance"]})

    def _api_crash_cashout(self):
        body = self._read_json()
        user_id = int(body.get("user_id") or 0)
        bet_id = int(body.get("bet_id") or 0)

        with crash_lock:
            if crash_state["phase"] != "playing":
                return self._send_json({"error": "not_playing"}, 400)

            bet = crash_state["bets"].get(bet_id)
            if not bet or bet["user_id"] != user_id or bet["status"] != "active":
                return self._send_json({"error": "bet_not_found"}, 404)

            multiplier = crash_current_multiplier()
            win_amount = bet["amount"] * multiplier

            bet["status"] = "won"
            bet["win_amount"] = win_amount
            bet["cashout_multiplier"] = multiplier

            with _db_lock:
                db = _load_db()
                key = str(bet["tg_id"])
                if key in db["users"]:
                    db["users"][key]["balance"] += win_amount
                _save_db(db)

            self._send_json({
                "balance": get_user(bet["tg_id"])["balance"],
                "win_amount": win_amount,
                "multiplier": round(multiplier, 2)
            })

    # ==================== АДМИН API ====================

    def _check_admin(self, tg_id):
        if not is_admin(tg_id):
            self._send_json({"error": "admin_required"}, 403)
            return False
        return True

    def _api_admin_users(self):
        # Проверяем админа через query параметр
        tg_id = int(self.path.split("?")[1].split("&")[0].split("=")[1]) if "?" in self.path else 0
        if not self._check_admin(tg_id):
            return

        with _db_lock:
            db = _load_db()
            users = []
            for user in db["users"].values():
                banned = str(user["tg_id"]) in db["banned_users"]
                users.append({
                    "id": user["id"],
                    "tg_id": user["tg_id"],
                    "first_name": user.get("first_name", ""),
                    "last_name": user.get("last_name", ""),
                    "username": user.get("username", ""),
                    "balance": user["balance"],
                    "created_at": user.get("created_at"),
                    "is_banned": banned,
                    "banned_reason": db["banned_users"].get(str(user["tg_id"]), {}).get("reason") if banned else None,
                })
            users.sort(key=lambda x: x["id"])

            # Статистика
            total_bets = len(db["bets"])
            total_deposits = sum(d["amount"] for d in db["deposits"])
            total_withdraws = sum(r["amount"] for r in db["withdraw_requests"] if r["status"] == "approved")

            self._send_json({
                "users": users,
                "stats": {
                    "total_users": len(users),
                    "total_bets": total_bets,
                    "total_deposits": total_deposits,
                    "total_withdraws": total_withdraws,
                }
            })

    def _api_admin_withdraw_requests(self):
        tg_id = int(self.path.split("?")[1].split("&")[0].split("=")[1]) if "?" in self.path else 0
        if not self._check_admin(tg_id):
            return

        with _db_lock:
            db = _load_db()
            requests = []
            for r in db["withdraw_requests"]:
                if r["status"] == "pending":
                    user = get_user_by_id(r["user_id"])
                    requests.append({
                        "id": r["id"],
                        "user_id": r["user_id"],
                        "tg_id": user["tg_id"] if user else None,
                        "first_name": user.get("first_name", "") if user else "",
                        "username": user.get("username", "") if user else "",
                        "amount": r["amount"],
                        "created_at": r["created_at"],
                    })
            requests.sort(key=lambda x: x["created_at"])

            self._send_json({"requests": requests})

    def _api_admin_deposit_requests(self):
        tg_id = int(self.path.split("?")[1].split("&")[0].split("=")[1]) if "?" in self.path else 0
        if not self._check_admin(tg_id):
            return

        with _db_lock:
            db = _load_db()
            deposits = []
            for d in db["deposits"]:
                if d.get("status") == "pending":
                    user = get_user_by_id(d["user_id"])
                    deposits.append({
                        "id": d["id"],
                        "user_id": d["user_id"],
                        "tg_id": user["tg_id"] if user else None,
                        "first_name": user.get("first_name", "") if user else "",
                        "username": user.get("username", "") if user else "",
                        "amount": d["amount"],
                        "from_name": d.get("from_name", ""),
                        "created_at": d["processed_at"],
                    })
            deposits.sort(key=lambda x: x["created_at"])

            self._send_json({"requests": deposits})

    def _api_admin_ban(self):
        body = self._read_json()
        tg_id = int(body.get("tg_id", 0))
        reason = body.get("reason", "Нарушение правил")
        admin_tg_id = int(body.get("admin_tg_id", 0))

        if not self._check_admin(admin_tg_id):
            return

        if not tg_id:
            return self._send_json({"error": "tg_id required"}, 400)

        user = get_user(tg_id)
        if not user:
            return self._send_json({"error": "user_not_found"}, 404)

        ban_user(tg_id, reason, admin_tg_id)
        self._send_json({"success": True, "tg_id": tg_id, "reason": reason})

    def _api_admin_unban(self):
        body = self._read_json()
        tg_id = int(body.get("tg_id", 0))
        admin_tg_id = int(body.get("admin_tg_id", 0))

        if not self._check_admin(admin_tg_id):
            return

        if not tg_id:
            return self._send_json({"error": "tg_id required"}, 400)

        unban_user(tg_id)
        self._send_json({"success": True, "tg_id": tg_id})

    def _api_admin_withdraw_approve(self):
        body = self._read_json()
        request_id = int(body.get("request_id", 0))
        admin_tg_id = int(body.get("admin_tg_id", 0))

        if not self._check_admin(admin_tg_id):
            return

        with _db_lock:
            db = _load_db()
            for r in db["withdraw_requests"]:
                if r["id"] == request_id and r["status"] == "pending":
                    r["status"] = "approved"
                    r["processed_at"] = int(time.time() * 1000)

                    # Уведомляем пользователя
                    user = get_user_by_id(r["user_id"])
                    if user:
                        threading.Thread(target=send_telegram_message, args=(
                            user["tg_id"],
                            f"✅ Ваша заявка на вывод {r['amount']} GMP одобрена!"
                        )).start()
                    break

            _save_db(db)
            self._send_json({"success": True})

    def _api_admin_withdraw_reject(self):
        body = self._read_json()
        request_id = int(body.get("request_id", 0))
        reason = body.get("reason", "Отклонено администратором")
        admin_tg_id = int(body.get("admin_tg_id", 0))

        if not self._check_admin(admin_tg_id):
            return

        with _db_lock:
            db = _load_db()
            for r in db["withdraw_requests"]:
                if r["id"] == request_id and r["status"] == "pending":
                    r["status"] = "rejected"
                    r["reject_reason"] = reason
                    r["processed_at"] = int(time.time() * 1000)

                    # Возвращаем баланс
                    user = get_user_by_id(r["user_id"])
                    if user:
                        key = str(user["tg_id"])
                        if key in db["users"]:
                            db["users"][key]["balance"] += r["amount"]

                        threading.Thread(target=send_telegram_message, args=(
                            user["tg_id"],
                            f"❌ Ваша заявка на вывод {r['amount']} GMP отклонена.\nПричина: {reason}"
                        )).start()
                    break

            _save_db(db)
            self._send_json({"success": True})

    def _api_admin_deposit_approve(self):
        body = self._read_json()
        deposit_id = int(body.get("deposit_id", 0))
        admin_tg_id = int(body.get("admin_tg_id", 0))

        if not self._check_admin(admin_tg_id):
            return

        with _db_lock:
            db = _load_db()
            for d in db["deposits"]:
                if d["id"] == deposit_id and d.get("status") == "pending":
                    d["status"] = "approved"
                    d["processed_at"] = int(time.time() * 1000)

                    # Начисляем баланс
                    user = get_user_by_id(d["user_id"])
                    if user:
                        key = str(user["tg_id"])
                        if key in db["users"]:
                            db["users"][key]["balance"] += d["amount"]

                        threading.Thread(target=send_telegram_message, args=(
                            user["tg_id"],
                            f"✅ Ваш депозит на {d['amount']} GMP подтверждён!"
                        )).start()
                    break

            _save_db(db)
            self._send_json({"success": True})


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def set_webhook():
    webhook_url = f"{WEBAPP_URL}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    try:
        response = requests.post(url, json={"url": webhook_url}, timeout=10)
        result = response.json()
        print(f"[BOT] Webhook установлен: {result}")
        return result
    except Exception as e:
        print(f"[BOT] Ошибка установки webhook: {e}")
        return None


def main():
    _init_db()
    print(f"[GMPLAY] Бот токен: {BOT_TOKEN[:10]}...")
    print(f"[GMPLAY] Сервер порт: {PORT}")
    print(f"[GMPLAY] WebApp URL: {WEBAPP_URL}")

    set_webhook()

    threading.Thread(target=crash_game_loop, daemon=True).start()

    with ReusableTCPServer((HOST, PORT), Handler) as httpd:
        print(f"[GMPLAY] Сервер запущен: http://{HOST}:{PORT}/")
        print(f"[GMPLAY] База данных: {DB_PATH}")
        print(f"[GMPLAY] Webhook: {WEBAPP_URL}/webhook")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[GMPLAY] Остановлено.")


if __name__ == "__main__":
    main()