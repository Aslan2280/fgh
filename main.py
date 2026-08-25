#!/usr/bin/env python3
"""
GMPLAY — Полный бот с WebApp на Flask (без CloudFlare)
"""

import os
import json
import time
import threading
import asyncio
import logging
import hashlib
import hmac
import urllib.parse
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ==================== КОНФИГУРАЦИЯ ====================

BOT_TOKEN = "8986114517:AAHoIHq-Kgk6iYEfxQLWsqN_sMNHc2pRYL8"

# Для BotHost — ваш домен
# Например: https://ваш-бот.bothost.tech
BASE_URL = os.getenv('BASE_URL', 'https://ваш-бот.bothost.tech')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gmplay.json")
STATIC_DIR = BASE_DIR

# Настройки игры
STARTING_BALANCE = 0
DAILY_BONUS_AMOUNT = 2
DAILY_BONUS_PERIOD_MS = 24 * 60 * 60 * 1000
REFERRAL_CUT = 0.01
CRASH_MIN_BET = 1
MIN_BALANCE_FOR_WITHDRAW = 10
MIN_DEPOSIT_FOR_WITHDRAW = 100
WITHDRAW_WEEK_MS = 7 * 24 * 60 * 60 * 1000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== FLASK APP ====================

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
CORS(app)

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
        "admins": [{"tg_id": 6539341659}],
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


def is_admin(tg_id):
    with _db_lock:
        db = _load_db()
        return any(a.get("tg_id") == tg_id for a in db.get("admins", []))


def is_user_banned(tg_id):
    with _db_lock:
        db = _load_db()
        return db["banned_users"].get(str(tg_id))


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
            "balance": STARTING_BALANCE,
            "referred_by": referred_by,
            "last_bonus_at": None,
            "created_at": now,
            "last_deposit_check": now,
        }

        db["users"][key] = user
        _save_db(db)
        return user, True


def ref_code_for(tg_id):
    return "GM" + format(int(tg_id), "x").upper()


def verify_init_data(init_data):
    """Проверяет подпись initData"""
    if not init_data:
        return None

    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    params = dict(pairs)
    received_hash = params.pop("hash", None)

    if not BOT_TOKEN:
        return params
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    return params


# ==================== СТАТИЧЕСКИЕ ФАЙЛЫ (WebApp) ====================

@app.route('/')
def serve_index():
    """Главная страница WebApp"""
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/admin.html')
def serve_admin():
    """Админ-панель"""
    return send_from_directory(STATIC_DIR, 'admin.html')


@app.route('/<path:path>')
def serve_static(path):
    """Статические файлы"""
    if os.path.exists(os.path.join(STATIC_DIR, path)):
        return send_from_directory(STATIC_DIR, path)
    return jsonify({"error": "not_found"}), 404


# ==================== API ROUTES ====================

@app.route('/api/init', methods=['POST', 'OPTIONS'])
def api_init():
    if request.method == 'OPTIONS':
        return jsonify({"ok": True})

    data = request.json or {}
    init_data = data.get("initData", "")

    logger.info(f"API init: {'✅ есть' if init_data else '❌ нет'}")

    if not init_data:
        return jsonify({
            "guest": True,
            "user_id": None,
            "balance": STARTING_BALANCE,
            "first_name": "Гость",
            "last_name": "",
            "ref_code": None,
            "is_admin": False,
            "message": "Открой приложение через Telegram-бота",
        })

    verified = verify_init_data(init_data)
    if verified is None:
        return jsonify({"error": "invalid_init_data"}), 401

    try:
        tg_user = json.loads(verified.get("user", "{}"))
    except Exception:
        tg_user = {}

    if not tg_user.get("id"):
        return jsonify({"error": "no_user_in_init_data"}), 400

    if is_user_banned(tg_user["id"]):
        return jsonify({
            "error": "banned",
            "reason": "Нарушение правил",
            "message": "⛔ Вы были забанены"
        }), 403

    start_param = data.get("start_param", "")
    user, is_new = get_or_create_user(tg_user, start_param)

    result = {
        "guest": False,
        "user_id": user["id"],
        "tg_id": user["tg_id"],
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "username": user.get("username", ""),
        "balance": user["balance"],
        "ref_code": ref_code_for(user["tg_id"]),
        "is_admin": is_admin(user["tg_id"]),
        "is_new": is_new,
    }

    return jsonify(result)


@app.route('/api/state', methods=['GET'])
def api_state():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    now = int(time.time() * 1000)
    last = user.get("last_bonus_at")
    ready = (last is None) or (now - last >= DAILY_BONUS_PERIOD_MS)

    return jsonify({
        "balance": user["balance"],
        "daily_bonus_ready": ready,
        "daily_bonus_next_at": None if ready else last + DAILY_BONUS_PERIOD_MS,
        "is_admin": is_admin(user["tg_id"]),
    })


@app.route('/api/daily-bonus', methods=['POST'])
def api_daily_bonus():
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    with _db_lock:
        db = _load_db()
        user = None
        for u in db["users"].values():
            if u.get("id") == user_id:
                user = u
                break

        if not user:
            return jsonify({"error": "user_not_found"}), 404

        now = int(time.time() * 1000)
        last = user.get("last_bonus_at")

        if last is not None and now - last < DAILY_BONUS_PERIOD_MS:
            return jsonify({
                "granted": False,
                "next_available_at": last + DAILY_BONUS_PERIOD_MS,
                "balance": user["balance"],
            })

        user["balance"] += DAILY_BONUS_AMOUNT
        user["last_bonus_at"] = now
        _save_db(db)

        return jsonify({
            "granted": True,
            "balance": user["balance"],
            "amount": DAILY_BONUS_AMOUNT
        })


@app.route('/api/history', methods=['GET'])
def api_history():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    limit = request.args.get('limit', 30, type=int)

    with _db_lock:
        db = _load_db()
        bets = [b for b in db["bets"] if b.get("user_id") == user_id]
        bets.sort(key=lambda x: x["created_at"], reverse=True)
        bets = bets[:limit]

        return jsonify({
            "items": [{
                "game": b["game"],
                "bet_amount": b["bet_amount"],
                "multiplier": b["multiplier"],
                "result": b["result"],
                "win_amount": b["win_amount"],
                "created_at": b["created_at"],
            } for b in bets]
        })


@app.route('/api/referrals', methods=['GET'])
def api_referrals():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

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

        return jsonify({
            "count": len(friends),
            "total_earned": round(total_earned, 2),
            "friends": friends,
        })


@app.route('/api/leaderboard', methods=['GET'])
def api_leaderboard():
    period = request.args.get('period', 'day')
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

        return jsonify({"items": items})


@app.route('/api/stats', methods=['GET'])
def api_stats():
    with _db_lock:
        db = _load_db()
        now = int(time.time() * 1000)
        five_min_ago = now - 5 * 60 * 1000
        today_start = now - (now % 86400000)

        active = len(set(b.get("user_id") for b in db["bets"] if b["created_at"] >= five_min_ago))
        won_today = sum(b["win_amount"] for b in db["bets"]
                        if b["result"] == "win" and b["created_at"] >= today_start)

        return jsonify({
            "active_now": active,
            "won_today": won_today,
            "total_players": len(db["users"]),
        })


@app.route('/api/profile-stats', methods=['GET'])
def api_profile_stats():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    with _db_lock:
        db = _load_db()
        user_bets = [b for b in db["bets"] if b.get("user_id") == user_id]

        return jsonify({
            "bets_count": len(user_bets),
            "total_won": sum(b["win_amount"] for b in user_bets if b["result"] == "win"),
            "total_wagered": sum(b["bet_amount"] for b in user_bets),
        })


@app.route('/api/withdraw/create', methods=['POST'])
def api_withdraw_create():
    data = request.json or {}
    user_id = data.get("user_id")
    amount = data.get("amount")

    if not user_id or not amount:
        return jsonify({"error": "user_id and amount required"}), 400

    with _db_lock:
        db = _load_db()
        user = None
        for u in db["users"].values():
            if u.get("id") == user_id:
                user = u
                break

        if not user:
            return jsonify({"error": "user_not_found"}), 404

        if user["balance"] < amount:
            return jsonify({"error": "Недостаточно GMP"}), 400

        if amount < MIN_BALANCE_FOR_WITHDRAW:
            return jsonify({"error": f"Минимальная сумма вывода: {MIN_BALANCE_FOR_WITHDRAW} GMP"}), 400

        week_ago = int(time.time() * 1000) - WITHDRAW_WEEK_MS
        deposits = [d for d in db["deposits"] if d.get("user_id") == user_id and d["processed_at"] >= week_ago]
        total_deposits = sum(d["amount"] for d in deposits)

        if total_deposits < MIN_DEPOSIT_FOR_WITHDRAW:
            return jsonify({
                "error": f"Для вывода нужен депозит от {MIN_DEPOSIT_FOR_WITHDRAW} GMP за последнюю неделю"
            }), 400

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

        return jsonify({
            "request_id": request_id,
            "amount": amount,
            "status": "pending",
            "created_at": now,
            "balance": user["balance"],
        })


@app.route('/api/withdraw-requests', methods=['GET'])
def api_withdraw_requests():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    with _db_lock:
        db = _load_db()
        requests = [r for r in db["withdraw_requests"] if r.get("user_id") == user_id]
        requests.sort(key=lambda x: x["created_at"], reverse=True)

        return jsonify({
            "items": [{
                "id": r["id"],
                "amount": r["amount"],
                "status": r["status"],
                "created_at": r["created_at"],
                "processed_at": r.get("processed_at"),
                "reject_reason": r.get("reject_reason"),
            } for r in requests]
        })


@app.route('/api/crash/state', methods=['GET'])
def api_crash_state():
    return jsonify({
        "phase": "betting",
        "round_id": 1,
        "phase_started_at": int(time.time() * 1000),
        "server_now": int(time.time() * 1000),
        "betting_duration": 6000,
        "pause_duration": 2600,
        "multiplier": 1.00,
        "crash_point": None,
        "history": [1.5, 2.3, 1.8, 3.2],
        "my_bets": [],
        "all_bets": [],
        "total_players": 0,
        "total_bets": 0,
    })


@app.route('/api/crash/bet', methods=['POST'])
def api_crash_bet():
    data = request.json or {}
    user_id = data.get("user_id")
    amount = data.get("amount")

    if not user_id or not amount:
        return jsonify({"error": "user_id and amount required"}), 400

    if amount < CRASH_MIN_BET:
        return jsonify({"error": "min_bet", "min_bet": CRASH_MIN_BET}), 400

    with _db_lock:
        db = _load_db()
        user = None
        for u in db["users"].values():
            if u.get("id") == user_id:
                user = u
                break

        if not user:
            return jsonify({"error": "user_not_found"}), 404

        if user["balance"] < amount:
            return jsonify({"error": "insufficient_balance"}), 400

        user["balance"] -= amount
        _save_db(db)

    return jsonify({
        "bet_id": 1,
        "balance": user["balance"],
    })


@app.route('/api/crash/cashout', methods=['POST'])
def api_crash_cashout():
    data = request.json or {}
    user_id = data.get("user_id")

    with _db_lock:
        db = _load_db()
        user = None
        for u in db["users"].values():
            if u.get("id") == user_id:
                user = u
                break

        if not user:
            return jsonify({"error": "user_not_found"}), 404

        win_amount = 50
        user["balance"] += win_amount
        _save_db(db)

    return jsonify({
        "balance": user["balance"],
        "win_amount": win_amount,
        "multiplier": 2.5,
    })


@app.route('/api/admin/users', methods=['GET'])
def api_admin_users():
    tg_id = request.args.get('tg_id', type=int)
    if not tg_id or not is_admin(tg_id):
        return jsonify({"error": "admin_required"}), 403

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

        return jsonify({
            "users": users,
            "stats": {
                "total_users": len(users),
                "total_bets": len(db["bets"]),
                "total_deposits": sum(d["amount"] for d in db["deposits"]),
                "total_withdraws": sum(r["amount"] for r in db["withdraw_requests"] if r["status"] == "approved"),
            }
        })


@app.route('/api/admin/withdraw-requests', methods=['GET'])
def api_admin_withdraw_requests():
    tg_id = request.args.get('tg_id', type=int)
    if not tg_id or not is_admin(tg_id):
        return jsonify({"error": "admin_required"}), 403

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

        return jsonify({"requests": requests})


@app.route('/api/admin/ban', methods=['POST'])
def api_admin_ban():
    data = request.json or {}
    tg_id = data.get("tg_id")
    reason = data.get("reason", "Нарушение правил")
    admin_tg_id = data.get("admin_tg_id")

    if not admin_tg_id or not is_admin(admin_tg_id):
        return jsonify({"error": "admin_required"}), 403

    if not tg_id:
        return jsonify({"error": "tg_id required"}), 400

    user = get_user(tg_id)
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    with _db_lock:
        db = _load_db()
        db["banned_users"][str(tg_id)] = {
            "reason": reason,
            "banned_at": int(time.time() * 1000),
            "banned_by": admin_tg_id,
        }
        _save_db(db)

    return jsonify({"success": True, "tg_id": tg_id})


@app.route('/api/admin/unban', methods=['POST'])
def api_admin_unban():
    data = request.json or {}
    tg_id = data.get("tg_id")
    admin_tg_id = data.get("admin_tg_id")

    if not admin_tg_id or not is_admin(admin_tg_id):
        return jsonify({"error": "admin_required"}), 403

    if not tg_id:
        return jsonify({"error": "tg_id required"}), 400

    with _db_lock:
        db = _load_db()
        db["banned_users"].pop(str(tg_id), None)
        _save_db(db)

    return jsonify({"success": True, "tg_id": tg_id})


@app.route('/api/admin/withdraw/approve', methods=['POST'])
def api_admin_withdraw_approve():
    data = request.json or {}
    request_id = data.get("request_id")
    admin_tg_id = data.get("admin_tg_id")

    if not admin_tg_id or not is_admin(admin_tg_id):
        return jsonify({"error": "admin_required"}), 403

    with _db_lock:
        db = _load_db()
        for r in db["withdraw_requests"]:
            if r["id"] == request_id and r["status"] == "pending":
                r["status"] = "approved"
                r["processed_at"] = int(time.time() * 1000)
                break
        _save_db(db)

    return jsonify({"success": True})


@app.route('/api/admin/withdraw/reject', methods=['POST'])
def api_admin_withdraw_reject():
    data = request.json or {}
    request_id = data.get("request_id")
    reason = data.get("reason", "Отклонено администратором")
    admin_tg_id = data.get("admin_tg_id")

    if not admin_tg_id or not is_admin(admin_tg_id):
        return jsonify({"error": "admin_required"}), 403

    with _db_lock:
        db = _load_db()
        for r in db["withdraw_requests"]:
            if r["id"] == request_id and r["status"] == "pending":
                r["status"] = "rejected"
                r["reject_reason"] = reason
                r["processed_at"] = int(time.time() * 1000)

                user = get_user_by_id(r["user_id"])
                if user:
                    key = str(user["tg_id"])
                    if key in db["users"]:
                        db["users"][key]["balance"] += r["amount"]
                break
        _save_db(db)

    return jsonify({"success": True})


@app.route('/api/admin/deposit/approve', methods=['POST'])
def api_admin_deposit_approve():
    data = request.json or {}
    deposit_id = data.get("deposit_id")
    admin_tg_id = data.get("admin_tg_id")

    if not admin_tg_id or not is_admin(admin_tg_id):
        return jsonify({"error": "admin_required"}), 403

    with _db_lock:
        db = _load_db()
        for d in db["deposits"]:
            if d["id"] == deposit_id and d.get("status") == "pending":
                d["status"] = "approved"
                d["processed_at"] = int(time.time() * 1000)

                user = get_user_by_id(d["user_id"])
                if user:
                    key = str(user["tg_id"])
                    if key in db["users"]:
                        db["users"][key]["balance"] += d["amount"]
                break
        _save_db(db)

    return jsonify({"success": True})


@app.route('/api/admin/deposit-requests', methods=['GET'])
def api_admin_deposit_requests():
    tg_id = request.args.get('tg_id', type=int)
    if not tg_id or not is_admin(tg_id):
        return jsonify({"error": "admin_required"}), 403

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

        return jsonify({"requests": deposits})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


# ==================== БОТ ====================

def run_bot():
    """Запуск бота в отдельном потоке"""
    try:
        from aiogram import Bot, Dispatcher, types
        from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
        from aiogram.filters import Command

        bot = Bot(token=BOT_TOKEN)
        dp = Dispatcher()

        @dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            try:
                if is_user_banned(message.from_user.id):
                    await message.answer(
                        "⛔ <b>Доступ запрещён</b>\n\n"
                        "Вы были забанены. Обратитесь в поддержку.",
                        parse_mode="HTML"
                    )
                    return

                start_param = None
                if " " in message.text:
                    start_param = message.text.split(" ", 1)[1]

                tg_user = {
                    "id": message.from_user.id,
                    "first_name": message.from_user.first_name,
                    "last_name": message.from_user.last_name or "",
                    "username": message.from_user.username or "",
                }

                user, is_new = get_or_create_user(tg_user, start_param)
                logger.info(f"Пользователь {message.from_user.id} запустил бота. Новый: {is_new}")

                webapp_url = BASE_URL
                if user:
                    webapp_url += f"?startapp={ref_code_for(user['tg_id'])}"

                is_admin_user = is_admin(message.from_user.id)

                keyboard = [
                    [InlineKeyboardButton(
                        text="🎮 Играть в GMPLAY",
                        web_app=WebAppInfo(url=webapp_url)
                    )]
                ]

                if is_admin_user:
                    keyboard.append([
                        InlineKeyboardButton(
                            text="⚙️ Админ-панель",
                            web_app=WebAppInfo(url=f"{BASE_URL}/admin.html")
                        )
                    ])

                keyboard.append([
                    InlineKeyboardButton(text="📢 Канал", url="https://t.me/gmplay_news"),
                    InlineKeyboardButton(text="🛟 Поддержка", url="https://t.me/gmp_help")
                ])

                reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

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
            if not is_admin(message.from_user.id):
                await message.answer("⛔ У вас нет доступа к этой команде.")
                return

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="⚙️ Открыть админ-панель",
                    web_app=WebAppInfo(url=f"{BASE_URL}/admin.html")
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
            user = get_user(message.from_user.id)
            if not user:
                await message.answer("❌ Пользователь не найден. Напишите /start")
                return

            await message.answer(
                f"👤 <b>Ваш профиль</b>\n\n"
                f"🆔 ID: <code>{user['tg_id']}</code>\n"
                f"💰 Баланс: <b>{user['balance']} GMP</b>\n"
                f"🔗 Реферальный код: <code>{ref_code_for(user['tg_id'])}</code>\n\n"
                f"Приглашайте друзей и получайте 1% от их проигрышей!",
                parse_mode="HTML"
            )

        @dp.message()
        async def echo(message: types.Message):
            await message.answer(
                "👋 Напишите /start, чтобы начать игру в GMPLAY!\n"
                "Или /help для справки."
            )

        async def start_bot():
            logger.info("🚀 Запуск бота...")
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info("✅ Вебхук удалён")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления вебхука: {e}")
            await dp.start_polling(bot)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_bot())

    except Exception as e:
        logger.error(f"❌ Ошибка в боте: {e}")


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    _init_db()
    logger.info("🚀 Запуск GMPLAY...")
    logger.info(f"📁 База данных: {DB_PATH}")
    logger.info(f"🌐 WebApp URL: {BASE_URL}")

    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Запускаем Flask (API + WebApp)
    port = int(os.getenv('PORT', 3000))
    logger.info(f"✅ Сервер запущен на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
