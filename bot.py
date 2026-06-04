import asyncio
import json
import os
import random
import time

from exchange import cmd_exchange, cb_exchange, exchange_job

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
    LabeledPrice, PreCheckoutQuery
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
    PreCheckoutQueryHandler,
)

TOKEN          = "8955120596:AAHFuR5Uwjrm1ET5saVhnI0GfyGAwP6jBjg"
OWNER_ID       = 8547898258
DB_FILE        = "data.json"
ADMINS_FILE    = "admins.json"
BLACKLIST_FILE = "blacklist.json"
TREASURY_FILE  = "treasury.json"
DAILY_BONUS    = 10_000
BONUS_COOLDOWN = 86400
TREASURY_UNLOCK_COST = 10_000  # стоимость подключения казны чата
MAX_BALANCE    = 1_000_000_000_000  # лимит баланса: 1 триллион

MINES_MULT  = [1.00,1.30,1.65,2.10,2.70,3.50,4.00,5.00,8.00,10.00,
               12.00,12.00,12.00,12.00,12.00,12.00,12.00,12.00]
MINES_COUNT = 8
GRID_SIZE   = 25

JOKER_MULTS = [1.0, 1.3, 1.8, 2.45, 3.6, 5.2, 7.5, 11.0, 16.0, 24.0, 35.0]


DICE_EXACT_MULT = 5.0   # угадал точное число
DICE_ODD_EVEN_MULT = 1.9  # чётное/нечётное (0 = нечётное)

DONATE_PACKAGES = [
    {"inter": 5_000,     "stars": 1,   "label": "🎁 Пробный"},
    {"inter": 20_000,    "stars": 5,   "label": "🌱 Стартовый"},
    {"inter": 100_000,   "stars": 25,  "label": "⚡ Популярный"},
    {"inter": 200_000,   "stars": 50,  "label": "🔥 Горящий"},
    {"inter": 400_000,   "stars": 100, "label": "💎 Премиум"},
    {"inter": 1_000_000, "stars": 250, "label": "👑 VIP"},
    {"inter": 2_000_000, "stars": 500, "label": "🚀 Легенда"},
]


# ── ПАРСИНГ СУММЫ (слова + числа) ────────────────────────
def parse_amount(text: str, user_balance: int = 0):
    """Преобразует строку в число Inter. Поддерживает слова и числа."""
    import re as _re
    t = text.strip().lower()
    t = t.replace(' ', ' ')
    t = ' '.join(t.split())

    if t in ('лимит', 'макс', 'максимум', 'все', 'всё', 'all', 'max'):
        return user_balance if user_balance > 0 else MAX_BALANCE

    # Порядок важен: длинные паттерны раньше коротких
    multipliers = [
        (r'(?:полмиллиарда?)',                           500_000_000),
        (r'(?:полмиллиона?)',                            500_000),
        (r'(?:милли[ао]рд(?:ов|а)?|млрд|kkk)',          1_000_000_000),
        (r'(?:милли[оа]н(?:ов|а)?|млн|kk)',             1_000_000),
        (r'(?:тысяч(?:и|а|у)?|тыс)',                    1_000),
        (r'(?:[кk])',                                    1_000),
    ]

    num_pat = r'([0-9]+(?:[.,][0-9]+)?)'

    for pattern, mult in multipliers:
        # Формат: "число множитель" — обязательно оборачиваем паттерн в группу
        m = _re.fullmatch(num_pat + r'\s*' + pattern, t)
        if m:
            try:
                return int(float(m.group(1).replace(',', '.')) * mult)
            except Exception:
                return None
        # Просто слово-множитель без числа
        if _re.fullmatch(pattern, t):
            return mult

    # Просто число (допускаем пробелы как разделители: 1 000 000)
    clean = _re.sub(r'\s+', '', t)
    if _re.fullmatch(r'\d+', clean):
        return int(clean)

    return None


# ── DB ───────────────────────────────────────
def load_db():
    if not os.path.exists(DB_FILE): return {}
    with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db, uid, name):
    k = str(uid)
    if k not in db:
        db[k] = {"username": name, "balance": 0, "last_bonus": 0,
                  "mines": None, "joker": None, "uid": uid}
    else:
        db[k]["username"] = name or db[k].get("username", "")
        db[k]["uid"] = uid
        if "mines" not in db[k]: db[k]["mines"] = None
        if "joker" not in db[k]: db[k]["joker"] = None
    return db[k]

def find_by_username(db, username):
    username = username.lstrip("@").lower()
    for k, v in db.items():
        if v.get("username", "").lower() == username:
            return k, v
    return None, None

# ── UTILS ────────────────────────────────────
def fmt(n): return f"{int(n):,}".replace(",", " ")

def link(usr):
    """Упоминание: имя со ссылкой на профиль Telegram."""
    name    = usr.get("username", "")
    uid     = usr.get("uid", 0)
    display = name if name else "игрок"
    return f"[{display}](tg://user?id={uid})"

def can_bonus(u): return time.time() - u["last_bonus"] >= BONUS_COOLDOWN

def time_left(u):
    r = BONUS_COOLDOWN - (time.time() - u["last_bonus"])
    if r <= 0: return "0ч 0м"
    return f"{int(r//3600)}ч {int((r%3600)//60)}м"

def load_admins():
    if not os.path.exists(ADMINS_FILE): return [OWNER_ID]
    with open(ADMINS_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_admins(admins):
    with open(ADMINS_FILE, "w", encoding="utf-8") as f: json.dump(admins, f)

def load_blacklist():
    if not os.path.exists(BLACKLIST_FILE): return []
    with open(BLACKLIST_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_blacklist(bl):
    with open(BLACKLIST_FILE, "w", encoding="utf-8") as f: json.dump(bl, f)

def is_banned(uid):
    return uid in load_blacklist()

def load_treasury():
    if not os.path.exists(TREASURY_FILE): return {}
    with open(TREASURY_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_treasury(t):
    with open(TREASURY_FILE, "w", encoding="utf-8") as f: json.dump(t, f, ensure_ascii=False, indent=2)

def get_chat_treasury(t, chat_id):
    k = str(chat_id)
    if k not in t:
        t[k] = {"balance": 0, "unlocked": False, "vault_access": [], "reward_per_invite": 0}
    if "vault_access" not in t[k]: t[k]["vault_access"] = []
    if "reward_per_invite" not in t[k]: t[k]["reward_per_invite"] = 0
    return t[k]

def is_admin(uid):
    return uid == OWNER_ID or uid in load_admins()

def is_owner(uid):
    return uid == OWNER_ID

# ── ADMIN PANEL ──────────────────────────────
def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💸 Выдать",  callback_data="adm_give_user_prompt"),
            InlineKeyboardButton("💼 Забрать", callback_data="adm_take_user_prompt"),
        ],
        [
            InlineKeyboardButton("🗑 Обнулить", callback_data="adm_zero_prompt"),
            InlineKeyboardButton("🎁 Себе",     callback_data="adm_give_prompt"),
        ],
        [InlineKeyboardButton("📊 Статистика",  callback_data="adm_stats")],
        [InlineKeyboardButton("👥 Администраторы", callback_data="adm_admins_menu")],
        [InlineKeyboardButton("🏦 Казна чата",     callback_data="adm_treasury_menu")],
        [InlineKeyboardButton("🚫 Чёрный список",  callback_data="adm_blacklist_menu")],
    ])

def admins_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Список администраторов", callback_data="adm_admins_list")],
        [InlineKeyboardButton("➕ Выдать админку",         callback_data="adm_grant_prompt")],
        [InlineKeyboardButton("➖ Снять админку",          callback_data="adm_revoke_prompt")],
        [InlineKeyboardButton("‹ Вернуться в меню",        callback_data="adm_back")],
    ])

def blacklist_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Добавить в ЧС",    callback_data="adm_ban_prompt")],
        [InlineKeyboardButton("✅ Убрать из ЧС",      callback_data="adm_unban_prompt")],
        [InlineKeyboardButton("📋 Список ЧС",         callback_data="adm_banlist")],
        [InlineKeyboardButton("‹ Вернуться в меню",   callback_data="adm_back")],
    ])

def admin_main_text(users_count, total_inter):
    return (
        "```\n"
        "╔══════════════════════╗\n"
        "║   👑  INTER  ADMIN   ║\n"
        "╚══════════════════════╝\n"
        "```\n"
        f"👥  Игроков:   `{users_count}`\n"
        f"💰  В обороте: `{fmt(total_inter)} Inter`\n\n"
        "Выбери действие 👇"
    )

async def cmd_admin(update: Update, ctx):
    u = update.effective_user
    if not is_admin(u.id):
        await update.message.reply_text("⛔️ Нет доступа."); return
    db = load_db()
    users_count = len(db)
    total_inter = sum(v.get("balance", 0) for v in db.values())
    await update.message.reply_text(
        admin_main_text(users_count, total_inter),
        parse_mode="Markdown", reply_markup=admin_keyboard()
    )

async def cb_admin(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    u = q.from_user
    if not is_admin(u.id):
        await q.answer("Нет доступа", show_alert=True); return
    # Действия только для владельца
    owner_only = {"adm_admins_menu", "adm_admins_list", "adm_grant_prompt", "adm_revoke_prompt", "adm_blacklist_menu", "adm_ban_prompt", "adm_unban_prompt", "adm_banlist"}
    if q.data in owner_only and not is_owner(u.id):
        await q.answer("⛔️ Только для владельца", show_alert=True); return
    await q.answer()
    data = q.data
    back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_back")]])

    if data == "adm_give_user_prompt":
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║   💸  ВЫДАТЬ INTER   ║\n╚══════════════════════╝\n```\n"
            "Напиши в чат:\n"
            "`дать @username <сумма>`\n\n"
            "📌 Пример: `дать @username 5000`",
            parse_mode="Markdown", reply_markup=back_btn
        )
    elif data == "adm_take_user_prompt":
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║   💼  ЗАБРАТЬ INTER  ║\n╚══════════════════════╝\n```\n"
            "Напиши в чат:\n"
            "`забрать @username <сумма>`\n\n"
            "📌 Пример: `забрать @username 5000`",
            parse_mode="Markdown", reply_markup=back_btn
        )
    elif data == "adm_zero_prompt":
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║   🗑  ОБНУЛИТЬ БАЛ   ║\n╚══════════════════════╝\n```\n"
            "Напиши в чат:\n"
            "`обнулить @username`\n\n"
            "📌 Пример: `обнулить @username`",
            parse_mode="Markdown", reply_markup=back_btn
        )
    elif data == "adm_give_prompt":
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║   🎁  НАЧИСЛИТЬ СЕБЕ ║\n╚══════════════════════╝\n```\n"
            "Напиши в чат:\n"
            "`выдать <сумма>`\n\n"
            "📌 Пример: `выдать 100000`",
            parse_mode="Markdown", reply_markup=back_btn
        )
    elif data == "adm_stats":
        db = load_db()
        users_count = len(db)
        total_inter = sum(v.get("balance", 0) for v in db.values())
        top_all = sorted(db.values(), key=lambda x: x.get("balance", 0), reverse=True)
        top = [v for v in top_all if v.get("balance", 0) > 0][:5]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        top_text = ""
        for i, v in enumerate(top, 0):
            n = v.get("username", "???")
            b = v.get("balance", 0)
            top_text += f"{medals[i]} @{n} — `{fmt(b)}`\n"
        if not top_text:
            top_text = "_пока нет игроков с балансом_\n"
        await q.edit_message_text(
            "```\n"
            "╔══════════════════════╗\n"
            "║   📊  СТАТИСТИКА     ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"👥  Игроков:   `{users_count}`\n"
            f"💰  В обороте: `{fmt(total_inter)} Inter`\n\n"
            "🏆 *Топ игроков:*\n"
            f"{top_text}",
            parse_mode="Markdown", reply_markup=back_btn
        )
    elif data == "adm_admins_menu":
        await q.edit_message_text(
            "```\n"
            "╔══════════════════════╗\n"
            "║   👥  АДМИНИСТРАТОРЫ ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            "Управление администраторами бота.\n"
            "Назначение и снятие — по *Telegram ID* пользователя.\n\n"
            "Выбери действие 👇",
            parse_mode="Markdown", reply_markup=admins_menu_keyboard()
        )
    elif data == "adm_admins_list":
        admins = load_admins()
        extra = [aid for aid in admins if aid != OWNER_ID]
        if extra:
            admins_text = "\n".join([f"  👤 `{aid}`" for aid in extra])
        else:
            admins_text = "  _нет дополнительных администраторов_"
        back_admins = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_admins_menu")]])
        await q.edit_message_text(
            "```\n"
            "╔══════════════════════╗\n"
            "║   📋  СПИСОК АДМИНОВ ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"👑 *Владелец:*\n  `{OWNER_ID}`\n\n"
            f"🛡 *Администраторы:*\n{admins_text}",
            parse_mode="Markdown", reply_markup=back_admins
        )
    elif data == "adm_revoke_prompt":
        back_admins = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_admins_menu")]])
        await q.edit_message_text(
            "```\n"
            "╔══════════════════════╗\n"
            "║   ➖  СНЯТЬ АДМИНКУ  ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            "Напиши в чат:\n"
            "`админ_снять <user_id>`\n\n"
            "📌 Пример: `админ_снять 123456789`",
            parse_mode="Markdown", reply_markup=back_admins
        )
    elif data == "adm_grant_prompt":
        back_admins = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_admins_menu")]])
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║   ➕  ВЫДАТЬ АДМИНКУ ║\n╚══════════════════════╝\n```\n"
            "Напиши в чат:\n"
            "`админ_выдать <user_id>`\n\n"
            "📌 Пример: `админ_выдать 123456789`",
            parse_mode="Markdown", reply_markup=back_btn
        )
    elif data == "adm_blacklist_menu":
        bl = load_blacklist()
        await q.edit_message_text(
            f"🚫 *Чёрный список*\n\n"
            f"Заблокировано: `{len(bl)}` пользователей\n\n"
            "Выбери действие 👇",
            parse_mode="Markdown", reply_markup=blacklist_menu_keyboard()
        )
    elif data == "adm_ban_prompt":
        back_bl = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_blacklist_menu")]])
        await q.edit_message_text(
            "🚫 *Добавить в ЧС*\n\nОтправь ID пользователя командой:\n`чс <ID>`",
            parse_mode="Markdown", reply_markup=back_bl
        )
    elif data == "adm_unban_prompt":
        bl = load_blacklist()
        back_bl = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_blacklist_menu")]])
        if not bl:
            await q.edit_message_text("✅ Чёрный список пуст.", reply_markup=back_bl); return
        ids_text = "\n".join(f"`{i}`" for i in bl)
        await q.edit_message_text(
            f"✅ *Убрать из ЧС*\n\nТекущий список:\n{ids_text}\n\nОтправь ID командой:\n`разчс <ID>`",
            parse_mode="Markdown", reply_markup=back_bl
        )
    elif data == "adm_banlist":
        bl = load_blacklist()
        back_bl = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_blacklist_menu")]])
        if not bl:
            await q.edit_message_text("✅ Чёрный список пуст.", reply_markup=back_bl); return
        ids_text = "\n".join(f"`{i}`" for i in bl)
        await q.edit_message_text(
            f"🚫 *Чёрный список* ({len(bl)} чел.):\n\n{ids_text}",
            parse_mode="Markdown", reply_markup=back_bl
        )
    elif data == "adm_back":
        db = load_db()
        users_count = len(db)
        total_inter = sum(v.get("balance", 0) for v in db.values())
        await q.edit_message_text(
            admin_main_text(users_count, total_inter),
            parse_mode="Markdown", reply_markup=admin_keyboard()
        )
    elif data == "adm_treasury_menu":
        chat_id = q.message.chat_id
        t = load_treasury()
        ct = get_chat_treasury(t, chat_id)
        bal = ct["balance"]
        reward = ct["reward_per_invite"]
        access_list = ct["vault_access"]
        unlocked = ct["unlocked"]
        def _fmt_access(e):
            if isinstance(e, dict):
                name = e.get("username", "?")
                limit = e.get("limit", 0)
                return f"@{name}" + (f" ({fmt(limit)})" if limit > 0 else "")
            return f"@{e}"
        access_str = ", ".join([_fmt_access(a) for a in access_list]) if access_list else "нет"
        status = "✅ Подключена" if unlocked else "🔒 Не подключена"
        text = (
            "```\n"
            "╔══════════════════════╗\n"
            "║   🏦  КАЗНА ЧАТА     ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"💰 Баланс казны: `{fmt(bal)} Inter`\n"
            f"🎁 Награда за приглашение: `{fmt(reward)} Inter`\n"
            f"🔑 Доступ (взять): {access_str}\n\n"
            "Казна — важная часть чата. Тут хранятся все средства для новых пользователей.\n\n"
            "Команды в чате:\n"
            "`казна <сумма>` — пополнить казну\n"
            "`награда <сумма>` — задать награду за приглашение\n"
            "`взять <сумма>` — забрать из казны (спец. доступ)\n\n"
            "Выбери действие 👇"
        )
        kbd_rows = [
            [InlineKeyboardButton("🔑 Выдать доступ «Взять»", callback_data="adm_treasury_grant_prompt")],
            [InlineKeyboardButton("‹ Назад", callback_data="adm_back")],
        ]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kbd_rows))

    elif data == "adm_treasury_grant_prompt":
        back_t = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="adm_treasury_menu")]])
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║  🔑 ДОСТУП К КАЗНЕ  ║\n╚══════════════════════╝\n```\n"
            "Напиши в чат:\n"
            "`казна_доступ @username <сумма>`\n\n"
            "📌 Пример: `казна_доступ @username 5000`\n\n"
            "Только указанный пользователь (и владелец) смогут использовать команду `взять`.\n"
            "Параметр `<сумма>` — максимум, который можно взять за один раз.",
            parse_mode="Markdown", reply_markup=back_t
        )

async def cmd_obnu(update: Update, ctx):
    u = update.effective_user
    if not is_admin(u.id): return
    args = ctx.args
    if not args:
        await update.message.reply_text("Укажи: `обнулить @username`", parse_mode="Markdown"); return
    db = load_db()
    uid_k, usr = find_by_username(db, args[0])
    if not usr:
        await update.message.reply_text(f"❌ Пользователь `{args[0]}` не найден.", parse_mode="Markdown"); return
    old_bal = usr["balance"]
    usr["balance"] = 0
    save_db(db)
    await update.message.reply_text(
        f"✅ Баланс `@{usr['username']}` обнулён.\nБыло: `{fmt(old_bal)} Inter`",
        parse_mode="Markdown"
    )

async def cmd_vydat(update: Update, ctx):
    u = update.effective_user
    if not is_admin(u.id): return
    args = ctx.args
    if not args:
        await update.message.reply_text("Укажи: `выдать <сумма>`\nПример: `выдать лимит` или `выдать 1 миллион`", parse_mode="Markdown"); return
    amount_str = " ".join(args)
    amount = parse_amount(amount_str)
    if not amount or amount < 1:
        await update.message.reply_text("❌ Неверная сумма. Пример: `выдать лимит` / `выдать 5 миллионов`", parse_mode="Markdown"); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    usr["balance"] = min(usr["balance"] + amount, MAX_BALANCE)
    save_db(db)
    await update.message.reply_text(
        f"➕ Начислено: `+{fmt(amount)} Inter`\n"
        f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
        parse_mode="Markdown"
    )

async def cmd_give_user(update: Update, ctx):
    """Выдать Inter игроку по @username (только админ)."""
    u = update.effective_user
    if not is_admin(u.id): return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Укажи: `дать @username <сумма>`\nПример: `дать @username лимит`", parse_mode="Markdown"); return
    amount_str = " ".join(args[1:])
    amount = parse_amount(amount_str)
    if not amount or amount < 1:
        await update.message.reply_text("❌ Неверная сумма. Пример: `дать @username миллион`", parse_mode="Markdown"); return
    db = load_db()
    uid_k, target = find_by_username(db, args[0])
    if not target:
        await update.message.reply_text(f"❌ `{args[0]}` не найден.", parse_mode="Markdown"); return
    target["balance"] = min(target.get("balance", 0) + amount, MAX_BALANCE)
    save_db(db)
    await update.message.reply_text(
        f"✅ Выдано `{fmt(amount)} Inter` → @{target['username']}\n"
        f"💰 Баланс: `{fmt(target['balance'])} Inter`",
        parse_mode="Markdown"
    )

async def cmd_take_user(update: Update, ctx):
    """Забрать Inter у игрока по @username (только админ)."""
    u = update.effective_user
    if not is_admin(u.id): return
    args = ctx.args
    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text("Укажи: `забрать @username <сумма>`", parse_mode="Markdown"); return
    amount = int(args[1])
    db = load_db()
    uid_k, target = find_by_username(db, args[0])
    if not target:
        await update.message.reply_text(f"❌ `{args[0]}` не найден.", parse_mode="Markdown"); return
    current = target.get("balance", 0)
    taken = min(amount, current)
    target["balance"] = current - taken
    save_db(db)
    await update.message.reply_text(
        f"➖ Забрано `{fmt(taken)} Inter` у @{target['username']}\n"
        f"💰 Баланс: `{fmt(target['balance'])} Inter`",
        parse_mode="Markdown"
    )

async def cmd_grant_admin(update: Update, ctx):
    """Выдать права админа по user_id (только владелец)."""
    u = update.effective_user
    if not is_owner(u.id): return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Укажи: `админ_выдать <user_id>`", parse_mode="Markdown"); return
    new_id = int(args[0])
    if new_id == OWNER_ID:
        await update.message.reply_text("Это уже владелец."); return
    admins = load_admins()
    if new_id in admins:
        await update.message.reply_text(f"✅ `{new_id}` уже является админом.", parse_mode="Markdown"); return
    admins.append(new_id)
    save_admins(admins)
    await update.message.reply_text(
        f"```\n╔══════════════════════╗\n║   👑  АДМИН ВЫДАН    ║\n╚══════════════════════╝\n```\n"
        f"🆔 ID: `{new_id}`\n✅ Теперь является администратором.",
        parse_mode="Markdown"
    )

async def cmd_revoke_admin(update: Update, ctx):
    """Снять права админа по user_id (только владелец)."""
    u = update.effective_user
    if not is_owner(u.id): return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Укажи: `админ_снять <user_id>`", parse_mode="Markdown"); return
    rem_id = int(args[0])
    if rem_id == OWNER_ID:
        await update.message.reply_text("❌ Нельзя снять владельца."); return
    admins = load_admins()
    if rem_id not in admins:
        await update.message.reply_text(f"❌ `{rem_id}` не является админом.", parse_mode="Markdown"); return
    admins.remove(rem_id)
    save_admins(admins)
    await update.message.reply_text(
        f"```\n╔══════════════════════╗\n║   ➖  АДМИН СНЯТ     ║\n╚══════════════════════╝\n```\n"
        f"🆔 ID: `{rem_id}`\n✅ Права администратора сняты.",
        parse_mode="Markdown"
    )


async def cmd_transfer(update: Update, ctx):
    """Перевод Inter другому игроку.
    Вариант 1: п @username сумма
    Вариант 2: ответить на сообщение и написать: п сумма
    """
    u = update.effective_user
    args = ctx.args
    msg = update.message
    reply = msg.reply_to_message

    db = load_db()
    sender = get_user(db, u.id, u.username or u.first_name)

    # ── Определяем получателя ─────────────────
    target_username = None
    uid_k = None
    target = None

    if reply:
        # Режим «ответ на сообщение»: п <сумма>
        if not args or not args[0].isdigit():
            await msg.reply_text("💸 При ответе на сообщение укажи сумму: `п 500`", parse_mode="Markdown"); return
        amount_str = args[0]
        target_tg = reply.from_user
        if not target_tg:
            await msg.reply_text("❌ Не удалось определить получателя."); return
        target_uid = target_tg.id
        target_name_tg = target_tg.username or target_tg.first_name or str(target_uid)
        # Убедимся, что получатель есть в БД
        target = get_user(db, target_uid, target_name_tg)
        uid_k = str(target_uid)
    else:
        # Режим «по @username»: п @username <сумма>
        if len(args) < 2:
            await msg.reply_text(
                "💸 Форматы:\n`п @username сумма`\nили ответь на сообщение: `п сумма`",
                parse_mode="Markdown"
            ); return
        target_username = args[0]
        amount_str = args[1]
        uid_k, target = find_by_username(db, target_username)
        if not target:
            await msg.reply_text(f"❌ Игрок `{target_username}` не найден.", parse_mode="Markdown"); return

    if not amount_str.isdigit():
        await msg.reply_text("❌ Сумма должна быть числом."); return
    amount = int(amount_str)
    if amount < 1:
        await msg.reply_text("❌ Минимальная сумма: 1 Inter"); return
    if uid_k == str(u.id):
        await msg.reply_text("❌ Нельзя переводить самому себе."); return
    if sender["balance"] < amount:
        await msg.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(sender['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return

    sender["balance"] -= amount
    target["balance"] = min(target.get("balance", 0) + amount, MAX_BALANCE)
    save_db(db)
    sender_name = u.username or u.first_name or "игрок"
    target_name = target.get("username", "игрок")
    await msg.reply_text(
        f"💸 @{sender_name} → @{target_name} • `{fmt(amount)} Inter`\n"
        f"💰 Твой баланс: `{fmt(sender['balance'])} Inter`",
        parse_mode="Markdown"
    )

# ── /start ───────────────────────────────────

def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💳 Донат"), KeyboardButton("📋 Команды")],
            [KeyboardButton("🎁 Бонус"), KeyboardButton("📡 Связь")],
        ],
        resize_keyboard=True,
        is_persistent=True
    )

async def cmd_chat(update, ctx):
    await update.message.reply_text(
        "💬 *Официальный чат INTER*\n\n"
        "Общайся, играй и соревнуйся с другими игроками! 🎮\n"
        "Участвуй в раздачах и следи за новостями бота 📢\n\n"
        "👥 Присоединяйся: @interfacts_chat",
        parse_mode="Markdown"
    )

async def cmd_support(update, ctx):
    await update.message.reply_text(
        "🆘 *Поддержка INTER*\n\n"
        "Возникли проблемы или вопросы? Мы поможем! 🤝\n"
        "Обращайся по любым вопросам — баги, жалобы, предложения 💡\n\n"
        "✉️ Напиши нам: @interbot_support",
        parse_mode="Markdown"
    )

async def cmd_svyaz(update, ctx):
    await update.message.reply_text(
        "📡 *Связь*\n\n"
        "🆘 *Поддержка*\n"
        "Возникли проблемы или вопросы? Мы поможем! 🤝\n"
        "Баги, жалобы, предложения — пиши нам:\n"
        "✉️ @interbot\\_support\n\n"
        "💬 Чат для игроков\n"
        "Общайся, играй и соревнуйся с другими игроками! 🎮\n"
        "Участвуй в раздачах и следи за новостями бота 📢\n"
        "👥 [interfacts\\_chat](https://t.me/interfacts_chat)",
        parse_mode="Markdown"
    )

async def cmd_start(update: Update, ctx):
    u = update.effective_user
    db = load_db(); get_user(db, u.id, u.username or u.first_name); save_db(db)
    kbd = InlineKeyboardMarkup([[InlineKeyboardButton("💎 Купить Inter", callback_data="donate_menu")]])
    await update.message.reply_text(
        "💎 *INTER Casino*\n\n"
        "Валюта чата — *Inter* 🪙\n\n"
        "🎮 *Игры:*\n"
        "🎰 Слоты  •  📈 Краш  •  💣 Мины  •  🃏 Джокер  •  🎲 Кости\n"
        "⚽ Футбол  •  🏀 Баскетбол  •  🎳 Боулинг\n\n"
        "📋 *Команды:*\n"
        "`б` / `баланс` — баланс\n"
        "`бонус` — ежедневный бонус\n"
        "`слоты <сумма>`\n"
        "`краш <сумма>`\n"
        "`мины <сумма>`\n"
        "`джокер <сумма>` — игра на выживание\n"
        "`кости <1-6> <ставка>` — угадай число\n"
        "`футбол <сумма>` — ×2 за гол\n"
        "`баскетбол <сумма>` — ×1.8 за попадание\n"
        "`боулинг <сумма>` — ×2 за страйк\n"
        "`п @username <сумма>` — перевод\n"
        "`дуэль @username <сумма>` — вызов на дуэль ⚔️\n"
        "`работа` — список работ и доход 💼\n"
        "`биржа` — торговля акциями 📊\n\n"
        "🏦 *Казна чата:*\n"
        "`казна` — информация о казне\n"
        "`казна <сумма>` — пополнить казну\n"
        "`взять <сумма>` — взять из казны _(спец. доступ)_\n\n"
        "Удачи! 🍀",
        parse_mode="Markdown",
        reply_markup=main_reply_keyboard()
    )

# ── БАЛАНС ───────────────────────────────────
async def cmd_balance(update: Update, ctx):
    u = update.effective_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    save_db(db)

    kbd = None; extra = ""
    if can_bonus(usr):
        extra = "\n🎁 *Доступен ежедневный бонус!*"
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎁 Забрать 10 000 Inter", callback_data="claim_bonus")
        ]])

    text = (
        f"{link(usr)}\n"
        f"💰 Баланс: `{fmt(usr['balance'])} Inter`"
        f"{extra}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kbd)

# ── БОНУС ────────────────────────────────────
async def cmd_bonus(update: Update, ctx):
    u = update.effective_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    if not can_bonus(usr):
        await update.message.reply_text(
            f"⏳ *Бонус уже получен!*\n"
            f"💰 Баланс: `{fmt(usr['balance'])} Inter`\n"
            f"🕐 Следующий через: `{time_left(usr)}`",
            parse_mode="Markdown"
        ); save_db(db); return
    usr["balance"] = min(usr["balance"] + DAILY_BONUS, MAX_BALANCE)
    usr["last_bonus"] = time.time()
    save_db(db)
    await update.message.reply_text(
        f"{link(usr)}, вы забрали бонус!\n"
        f"💰 Сумма: `{fmt(DAILY_BONUS)} Inter`\n"
        f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
        parse_mode="Markdown"
    )

async def cb_bonus(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query; await q.answer()
    u = q.from_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    if not can_bonus(usr):
        await q.edit_message_text(
            f"⏳ *Бонус уже получен!*\n"
            f"💰 Баланс: `{fmt(usr['balance'])} Inter`\n"
            f"🕐 Следующий через: `{time_left(usr)}`",
            parse_mode="Markdown"
        ); save_db(db); return
    usr["balance"] = min(usr["balance"] + DAILY_BONUS, MAX_BALANCE)
    usr["last_bonus"] = time.time()
    save_db(db)
    await q.edit_message_text(
        f"{link(usr)}, вы забрали бонус!\n"
        f"💰 Сумма: `{fmt(DAILY_BONUS)} Inter`\n"
        f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
        parse_mode="Markdown"
    )

# ── ДОНАТ ────────────────────────────────────
def donate_keyboard():
    rows = []
    for i, pkg in enumerate(DONATE_PACKAGES):
        rows.append([InlineKeyboardButton(
            f"{pkg['label']}  {fmt(pkg['inter'])} Inter — {pkg['stars']} ⭐",
            callback_data=f"buy_{i}"
        )])
    return InlineKeyboardMarkup(rows)

async def cmd_donate(update: Update, ctx):
    u = update.effective_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    save_db(db)
    await update.message.reply_text(
        "💎 *Купить Inter*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"Твой баланс: `{fmt(usr['balance'])} Inter`\n\n"
        "Выбери пакет — оплата звёздами Telegram ⭐:\n",
        parse_mode="Markdown", reply_markup=donate_keyboard()
    )

async def cb_donate(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    data = q.data

    if data == "donate_menu":
        await q.answer()
        u = q.from_user
        db = load_db()
        usr = get_user(db, u.id, u.username or u.first_name)
        save_db(db)
        await q.edit_message_text(
            "💎 *Купить Inter*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"Твой баланс: `{fmt(usr['balance'])} Inter`\n\n"
            "Выбери пакет — оплата звёздами Telegram ⭐:\n",
            parse_mode="Markdown", reply_markup=donate_keyboard()
        ); return

    if data.startswith("buy_"):
        await q.answer()
        idx = int(data[4:])
        pkg = DONATE_PACKAGES[idx]
        await ctx.bot.send_invoice(
            chat_id=q.from_user.id,
            title=f"{pkg['label']} — {fmt(pkg['inter'])} Inter",
            description=(
                f"Вы получите {fmt(pkg['inter'])} Inter на баланс в INTER Casino.\n"
                f"Оплата: {pkg['stars']} звёзд Telegram ⭐"
            ),
            payload=f"inter_{idx}_{q.from_user.id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{fmt(pkg['inter'])} Inter", amount=pkg["stars"])],
            provider_token="",
        )

async def precheckout(update: Update, ctx):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, ctx):
    u = update.effective_user
    payment = update.message.successful_payment
    parts = payment.invoice_payload.split("_")
    idx = int(parts[1])
    pkg = DONATE_PACKAGES[idx]
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    usr["balance"] = min(usr["balance"] + pkg["inter"], MAX_BALANCE)
    save_db(db)
    # Удаляем сообщение с инвойсом (находится прямо перед сообщением об оплате)
    try:
        await ctx.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id - 1
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ *Оплата прошла!*\n\n"
        f"💎 Пакет: {pkg['label']}\n"
        f"⭐ Звёзд потрачено: `{pkg['stars']}`\n"
        f"➕ Начислено: `{fmt(pkg['inter'])} Inter`\n"
        f"💼 Баланс: `{fmt(usr['balance'])} Inter`\n\n"
        f"Спасибо за поддержку! 🙏",
        parse_mode="Markdown"
    )
    try:
        name = u.username or u.first_name or str(u.id)
        await ctx.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"💸 *НОВЫЙ ДОНАТ!*\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Пользователь: [{name}](tg://user?id={u.id})\n"
                f"🆔 ID: `{u.id}`\n"
                f"📦 Пакет: {pkg['label']}\n"
                f"⭐ Сумма: `{pkg['stars']} звёзд`\n"
                f"💰 Inter выдано: `{fmt(pkg['inter'])}`\n"
                f"💼 Баланс игрока: `{fmt(usr['balance'])} Inter`\n\n"
                f"━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass

# ── СЛОТЫ ────────────────────────────────────
# Telegram slot dice: value 1-64
# 64 = 777 (джекпот 25x)
# Два одинаковых символа = значения: 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22 — проигрыш
# Точные значения двух одинаковых и трёх одинаковых определяются Telegram
# Правило: 64 = джекпот, иначе делим диапазон: ~32% два одинаковых (5x), остальное — слив
# Реальные значения Telegram slot machine:
# 1  = BAR BAR BAR  — не джекпот, просто три BAR
# 64 = 7 7 7        — джекпот
# Два одинаковых: значения, где первые два или последние два совпадают
# Для простоты: 64=jackpot(25x), значения кратные 11 или с совпадением = 5x, остальное = слив

def slots_outcome(val):
    """
    Определяем исход по значению Telegram dice (1-64).
    Telegram slot machine барабаны (каждый 1-4):
    reel1 = ((val-1) // 16) + 1  — не совсем так, но приближённо
    Точная формула: val = (r1-1)*16 + (r2-1)*4 + r3, r1,r2,r3 in 1..4
    """
    v = val - 1
    r1 = v // 16 + 1
    r2 = (v % 16) // 4 + 1
    r3 = v % 4 + 1
    if val == 64:
        return "jackpot"   # 7 7 7
    if r1 == r2 == r3:
        return "triple"    # три одинаковых (не 777)
    if r1 == r2 or r2 == r3 or r1 == r3:
        return "double"    # два одинаковых
    return "lose"

async def cmd_slots(update: Update, ctx):
    u = update.effective_user; args = ctx.args
    if not args:
        await update.message.reply_text("🎰 Укажи ставку: `слоты 500` или `слоты миллион`", parse_mode="Markdown"); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(args), usr["balance"])
    if not bet or bet < 1:
        await update.message.reply_text("❌ Неверная ставка. Пример: `слоты миллион`", parse_mode="Markdown"); return
    if usr["balance"] < bet:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return

    # Списываем ставку сразу
    usr["balance"] -= bet

    # Отправляем Telegram dice 🎰 — анимация встроена
    dice_msg = await update.effective_chat.send_dice(emoji="🎰")
    val = dice_msg.dice.value

    # Ждём пока анимация доиграет (слот-машина крутится ~3.5с)
    await asyncio.sleep(3.5)

    outcome = slots_outcome(val)

    raw_name = usr.get('username', usr.get('name', 'игрок'))
    mention = f"@{raw_name}"

    if outcome == "jackpot":
        win = bet * 25
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        save_db(db)
        await dice_msg.reply_text(
            f"🎰 777 — ДЖЕКПОТ!\n"
            f"🏆 {mention} выиграл {fmt(win)} Inter (×25)\n"
            f"💰 Баланс: {fmt(usr['balance'])} Inter"
        )
    elif outcome == "triple":
        win = int(bet * 1.5)
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        save_db(db)
        await dice_msg.reply_text(
            f"🎰 Три одинаковых!\n"
            f"✅ {mention} выиграл {fmt(win)} Inter (×1.5)\n"
            f"💰 Баланс: {fmt(usr['balance'])} Inter"
        )
    elif outcome == "double":
        win = int(bet * 1.3)
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        save_db(db)
        await dice_msg.reply_text(
            f"🎰 Два одинаковых!\n"
            f"✅ {mention} выиграл {fmt(win)} Inter (×1.3)\n"
            f"💰 Баланс: {fmt(usr['balance'])} Inter"
        )
    else:
        save_db(db)
        await dice_msg.reply_text(
            f"🎰 {mention} проиграл!\n"
            f"❌ Ставка {fmt(bet)} Inter — потеряна\n"
            f"💰 Баланс: {fmt(usr['balance'])} Inter"
        )

# ── КРАШ ─────────────────────────────────────
# ── КРАШ (интерактивный) ─────────────────────────────────────────────────────
# Хранит активные сессии: {user_id: {bet, cap, current, task, msg_id, chat_id, cashed_out}}
CRASH_SESSIONS: dict = {}

def crash_point():
    r = random.random()
    if r < 0.35: return round(random.uniform(1.00, 1.30), 2)
    if r < 0.60: return round(random.uniform(1.30, 2.00), 2)
    if r < 0.85: return round(random.uniform(2.00, 5.00), 2)
    if r < 0.96: return round(random.uniform(5.00, 15.0), 2)
    return round(random.uniform(15.0, 50.0), 2)

def crash_kbd(uid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💸 Забрать выигрыш", callback_data=f"crash_cashout_{uid}")
    ]])

def crash_frame(username, bet, val, cap=None):
    rocket = "🚀"
    bar_len = min(int((val - 1.0) / 0.4), 14)
    bar_str = "▓" * bar_len + "░" * (14 - bar_len)
    lines = [
        f"📈 *Краш* — @{username}",
        f"💰 Ставка: `{fmt(bet)} Inter`",
        f"",
        f"{rocket} `{val:.2f}×`",
        f"`{bar_str}`",
    ]
    if cap is not None:
        lines.append(f"")
        lines.append(f"💥 Краш на `{cap:.2f}×`!")
    return "\n".join(lines)

async def crash_ticker(uid, bot, chat_id, msg_id, bet, cap, username):
    """Тикер: обновляет сообщение каждую секунду, при краше завершает игру."""
    session = CRASH_SESSIONS.get(uid)
    if not session:
        return
    val = 1.00
    step = 0.07
    while uid in CRASH_SESSIONS and not CRASH_SESSIONS[uid].get("cashed_out"):
        val = round(val + step, 2)
        step = round(step * 1.06, 4)  # ускорение
        CRASH_SESSIONS[uid]["current"] = val
        if val >= cap:
            # КРАШ
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=crash_frame(username, bet, cap, cap=cap),
                    parse_mode="Markdown"
                )
            except: pass
            # Ставка уже была списана при старте игры
            db = load_db()
            usr = get_user(db, uid, username)
            await asyncio.sleep(0.3)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=(
                        f"💥 *Краш на `{cap:.2f}×`!*\n"
                        f"@{username} слил `{fmt(bet)}` Inter\n"
                        f"💰 Баланс: `{fmt(usr['balance'])}`"
                    ),
                    parse_mode="Markdown"
                )
            except: pass
            CRASH_SESSIONS.pop(uid, None)
            return
        # Обновляем сообщение
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=crash_frame(username, bet, val),
                parse_mode="Markdown",
                reply_markup=crash_kbd(uid)
            )
        except: pass
        await asyncio.sleep(1)

async def cmd_crash(update: Update, ctx):
    u = update.effective_user; args = ctx.args
    if not args:
        await update.message.reply_text("📈 Укажи ставку: `краш 500` или `краш миллион`", parse_mode="Markdown"); return
    if u.id in CRASH_SESSIONS:
        await update.message.reply_text("⚠️ У тебя уже идёт игра в краш!"); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(args), usr["balance"])
    if not bet or bet < 1:
        await update.message.reply_text("❌ Неверная ставка. Пример: `краш миллион`", parse_mode="Markdown"); return
    if usr["balance"] < bet:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return
    # Списываем ставку сразу при старте игры
    usr["balance"] -= bet
    save_db(db)
    cap = crash_point()
    username = u.username or u.first_name
    msg = await update.message.reply_text(
        crash_frame(username, bet, 1.00),
        parse_mode="Markdown",
        reply_markup=crash_kbd(u.id)
    )
    CRASH_SESSIONS[u.id] = {
        "bet": bet,
        "cap": cap,
        "current": 1.00,
        "cashed_out": False,
        "chat_id": update.effective_chat.id,
        "msg_id": msg.message_id,
        "username": username,
    }
    # Запускаем тикер как фоновую задачу через application (безопасно с concurrent_updates)
    ctx.application.create_task(crash_ticker(
        u.id, ctx.bot, update.effective_chat.id,
        msg.message_id, bet, cap, username
    ))

async def cb_crash(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data  # crash_cashout_{uid}
    try:
        target_uid = int(data.split("_")[-1])
    except:
        return
    if uid != target_uid:
        await q.answer("❌ Это не твоя игра!", show_alert=True); return
    session = CRASH_SESSIONS.get(uid)
    if not session:
        await q.answer("Игра уже завершена.", show_alert=True); return
    if session.get("cashed_out"):
        await q.answer("Уже забрано!", show_alert=True); return

    session["cashed_out"] = True
    val = session["current"]
    bet = session["bet"]
    username = session["username"]
    CRASH_SESSIONS.pop(uid, None)

    db = load_db()
    usr = get_user(db, uid, username)
    # Ставка уже списана при старте — возвращаем полный выигрыш
    win = int(bet * val)
    profit = win - bet
    usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
    save_db(db)

    try:
        await q.edit_message_text(
            f"✅ *Выигрыш!*\n"
            f"@{username} забрал на `{val:.2f}×`\n"
            f"💸 +`{fmt(profit)}` Inter\n"
            f"💰 Баланс: `{fmt(usr['balance'])}`",
            parse_mode="Markdown"
        )
    except: pass

# ── МИНЫ ─────────────────────────────────────
# Визуал:
#   закрытая клетка      = "·"  (нейтральная точка, кликабельна)
#   открытая безопасная  = " "  (пустая, не кликабельна)
#   при раскрытии мина   = "💣"
#   при раскрытии чисто  = " "

def mines_kbd(session, reveal=False):
    mines_set  = set(session["mines"])
    opened_set = set(session["opened"])
    rows = []
    for row in range(5):
        btns = []
        for col in range(5):
            idx = row*5+col
            if idx in opened_set:
                # Уже открыто — пустая клетка
                btns.append(InlineKeyboardButton(" ", callback_data="mn_no"))
            elif reveal:
                # Финальное раскрытие: мина — 💣, безопасно — пусто
                if idx in mines_set:
                    btns.append(InlineKeyboardButton("💣", callback_data="mn_no"))
                else:
                    btns.append(InlineKeyboardButton(" ", callback_data="mn_no"))
            else:
                # Закрытая — вопрос, кликабельная
                btns.append(InlineKeyboardButton("❓", callback_data=f"mn_{idx}"))
        rows.append(btns)

    if not reveal and session.get("active") and session["opened"]:
        step = len(session["opened"])
        mult = MINES_MULT[min(step, len(MINES_MULT)-1)]
        win  = int(session["bet"]*mult)
        rows.append([InlineKeyboardButton(
            f"💰 Забрать {fmt(win)} Inter (×{mult})", callback_data="mn_cash"
        )])
    return InlineKeyboardMarkup(rows)

def mines_text(session, usr, started=False):
    step = len(session["opened"])
    mult = MINES_MULT[min(step, len(MINES_MULT)-1)]
    nxt  = MINES_MULT[min(step+1, len(MINES_MULT)-1)]
    win  = int(session["bet"] * mult)
    name = usr.get("username", "игрок")
    uid  = usr.get("uid", 0)
    mention = f"[{name}](tg://user?id={uid})"
    if started:
        return (
            f"💣 {mention}, вы начали игру в мины!\n"
            f"💰 Ваша ставка — `{fmt(session['bet'])} Inter`\n\n"
            f"Открывайте клетки и не попадите на мину! 🤞"
        )
    if step == 0:
        return (
            f"💣 {mention} • ставка `{fmt(session['bet'])} Inter`\n"
            f"Следующий выигрыш: `{fmt(int(session['bet']*nxt))}` (×{nxt})"
        )
    return (
        f"💣 {mention} • ставка `{fmt(session['bet'])} Inter`\n"
        f"Открыто: {step} | Баланс для вывода: `{fmt(win)} Inter` (×{mult})\n"
        f"Следующий: `{fmt(int(session['bet']*nxt))}` (×{nxt})"
    )

async def cmd_mines(update: Update, ctx):
    u = update.effective_user; args = ctx.args
    if not args:
        await update.message.reply_text("💣 Укажи ставку: `мины 500` или `мины миллион`", parse_mode="Markdown"); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(args), usr["balance"])
    if not bet or bet < 1:
        await update.message.reply_text("❌ Неверная ставка. Пример: `мины миллион`", parse_mode="Markdown"); return
    existing = usr.get("mines")
    if existing and existing.get("active"):
        # Сбрасываем старую игру (ставка уже была списана ранее)
        usr["mines"] = None
    if usr["balance"] < bet:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return
    usr["balance"] -= bet
    usr["mines"] = {
        "bet": bet,
        "mines": random.sample(range(GRID_SIZE), MINES_COUNT),
        "opened": [],
        "active": True,
        "msg_id": None
    }
    save_db(db)
    sent = await update.message.reply_text(
        mines_text(usr["mines"], usr, started=True),
        parse_mode="Markdown",
        reply_markup=mines_kbd(usr["mines"])
    )
    # Сохраняем message_id чтобы старые кнопки не работали
    db2 = load_db()
    usr2 = get_user(db2, u.id, u.username or u.first_name)
    if usr2.get("mines") and usr2["mines"].get("active"):
        usr2["mines"]["msg_id"] = sent.message_id
        save_db(db2)

async def cb_mines(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    u = q.from_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    session = usr.get("mines")

    if not session or not session.get("active"):
        await q.answer("Нет активной игры. Начни: мины <сумма>", show_alert=True); return

    # Защита от нажатия кнопок старых сообщений
    msg_id = session.get("msg_id")
    if msg_id and q.message.message_id != msg_id:
        await q.answer("⚠️ Это старая игра!", show_alert=True); return

    data = q.data
    if data == "mn_no":
        await q.answer(); return

    await q.answer()

    if data == "mn_cash":
        step   = len(session["opened"])
        mult   = MINES_MULT[min(step, len(MINES_MULT)-1)]
        win    = int(session["bet"]*mult)
        profit = win - session["bet"]
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        session["active"] = False
        save_db(db)
        name = usr.get("username", "игрок")
        uid  = usr.get("uid", 0)
        mention = f"[{name}](tg://user?id={uid})"
        await q.edit_message_text(
            f"✅ {mention}, вы забрали выигрыш!\n"
            f"💰 Сумма: `{fmt(win)} Inter` (×{mult})\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown", reply_markup=mines_kbd(session, reveal=True)
        ); return

    if data.startswith("mn_"):
        idx = int(data[3:])
        if idx in session["opened"]: return

        if idx in session["mines"]:
            # МИНА — проигрыш
            session["active"] = False
            save_db(db)
            name = usr.get("username", "игрок")
            uid  = usr.get("uid", 0)
            mention = f"[{name}](tg://user?id={uid})"
            await q.edit_message_text(
                f"💥 {mention}, вы проиграли!\n"
                f"💣 Ставка: `{fmt(session['bet'])} Inter` — потеряна\n"
                f"💰 Баланс: `{fmt(usr.get('balance',0))} Inter`",
                parse_mode="Markdown", reply_markup=mines_kbd(session, reveal=True)
            )
            await q.answer("💥 БУМ! Мина!", show_alert=True)
        else:
            # Безопасно
            session["opened"].append(idx)
            step = len(session["opened"])
            safe_left = GRID_SIZE - MINES_COUNT - step
            if safe_left == 0:
                mult   = MINES_MULT[min(step, len(MINES_MULT)-1)]
                win    = int(session["bet"]*mult)
                profit = win - session["bet"]
                usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
                session["active"] = False
                save_db(db)
                name = usr.get("username", "игрок")
                uid  = usr.get("uid", 0)
                mention = f"[{name}](tg://user?id={uid})"
                await q.edit_message_text(
                    f"🏆 {mention}, вы открыли все клетки!\n"
                    f"💰 Выигрыш: `{fmt(win)} Inter` (×{mult})\n"
                    f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
                    parse_mode="Markdown", reply_markup=mines_kbd(session, reveal=True)
                )
            else:
                save_db(db)
                await q.edit_message_text(
                    mines_text(session, usr),
                    parse_mode="Markdown", reply_markup=mines_kbd(session)
                )
# ── ДЖОКЕР ───────────────────────────────────
# Механика: каждый уровень = строка из 3 ячеек.
# 1 череп + 2 карты. Выбрал карту — строка открывается, переходишь выше.
# Выбрал череп — проигрыш.

def joker_kbd(session, reveal_skull=None):
    """
    Строим клавиатуру снизу вверх (строки в списке — от первого уровня к последнему).
    history[i] = позиция черепа на уровне i (уже пройденном).
    reveal_skull = позиция черепа на ТЕКУЩЕМ уровне при проигрыше.
    """
    rows = []
    # Пройденные уровни
    for skull_pos in session["history"]:
        btns = []
        for c in range(3):
            sym = "☠️" if c == skull_pos else "🃏"
            btns.append(InlineKeyboardButton(sym, callback_data="jk_no"))
        rows.append(btns)

    # Текущий уровень
    if reveal_skull is not None:
        # Раскрываем (проигрыш)
        btns = []
        for c in range(3):
            sym = "☠️" if c == reveal_skull else "🃏"
            btns.append(InlineKeyboardButton(sym, callback_data="jk_no"))
        rows.append(btns)
    elif session.get("active"):
        # Закрытые клетки — вопросительные знаки
        btns = []
        for c in range(3):
            btns.append(InlineKeyboardButton("❓", callback_data=f"jk_{c}"))
        rows.append(btns)

    # Кнопка забрать (только если хотя бы 1 уровень пройден и игра активна)
    if session.get("active") and session["level"] > 0 and reveal_skull is None:
        mult = JOKER_MULTS[min(session["level"], len(JOKER_MULTS)-1)]
        win  = int(session["bet"] * mult)
        rows.append([InlineKeyboardButton(
            f"💸 Забрать выигрыш {fmt(win)} Inter (×{mult})", callback_data="jk_cash"
        )])
    return InlineKeyboardMarkup(rows)

def joker_active_text(session, usr):
    lvl  = session["level"]
    nxt  = JOKER_MULTS[min(lvl+1, len(JOKER_MULTS)-1)]
    win  = int(session["bet"] * nxt)
    cur_mult = JOKER_MULTS[min(lvl, len(JOKER_MULTS)-1)]
    cur_win  = int(session["bet"] * cur_mult)
    text = (
        f"🃏 @{usr.get('username','')} • ставка `{fmt(session['bet'])}` • уровень {lvl}\n"
        f"следующий ×{nxt} = `{fmt(win)}`"
    )
    if lvl > 0:
        text += f" • сейчас `{fmt(cur_win)}`"
    return text

async def cmd_joker(update: Update, ctx):
    u = update.effective_user; args = ctx.args
    if not args:
        await update.message.reply_text("🃏 Укажи ставку: `джокер 500` или `джокер миллион`", parse_mode="Markdown"); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(args), usr["balance"])
    if not bet or bet < 1:
        await update.message.reply_text("❌ Неверная ставка. Пример: `джокер миллион`", parse_mode="Markdown"); return
    existing = usr.get("joker")
    if existing and existing.get("active"):
        # Сбрасываем старую игру (ставка уже была списана ранее)
        usr["joker"] = None
    if usr["balance"] < bet:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return
    usr["balance"] -= bet
    session = {
        "bet": bet,
        "level": 0,
        "active": True,
        "history": [],
        "skull": random.randint(0, 2),
        "msg_id": None
    }
    usr["joker"] = session
    save_db(db)
    sent = await update.message.reply_text(
        joker_active_text(session, usr),
        parse_mode="Markdown",
        reply_markup=joker_kbd(session)
    )
    db2 = load_db()
    usr2 = get_user(db2, u.id, u.username or u.first_name)
    if usr2.get("joker") and usr2["joker"].get("active"):
        usr2["joker"]["msg_id"] = sent.message_id
        save_db(db2)

async def cb_joker(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    u = q.from_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    session = usr.get("joker")

    if not session or not session.get("active"):
        await q.answer("Нет активной игры.", show_alert=True); return

    # Защита от старых кнопок
    msg_id = session.get("msg_id")
    if msg_id and q.message.message_id != msg_id:
        await q.answer("⚠️ Это старая игра!", show_alert=True); return

    if not session or not session.get("active"):
        # Игра уже завершена — тихо игнорируем любые клики
        await q.answer(); return

    data = q.data
    if data == "jk_no":
        await q.answer(); return

    await q.answer()

    if data == "jk_cash":
        lvl  = session["level"]
        mult = JOKER_MULTS[min(lvl, len(JOKER_MULTS)-1)]
        win  = int(session["bet"] * mult)
        usr["balance"] = usr.get("balance", 0) + win
        session["active"] = False
        save_db(db)
        await q.edit_message_text(
            f"🃏 @{usr.get('username','')} забрал `{fmt(win)}` (×{mult}) — уровней {lvl}\n"
            f"💰 `{fmt(usr['balance'])}`",
            parse_mode="Markdown",
            reply_markup=joker_kbd(session)
        ); return

    if data.startswith("jk_"):
        chosen    = int(data[3:])
        skull_pos = session["skull"]

        if chosen == skull_pos:
            # ПРОИГРЫШ — раскрываем текущий уровень
            session["active"] = False
            save_db(db)
            await q.edit_message_text(
                f"🃏 @{usr.get('username','')} слил `{fmt(session['bet'])}` — череп ☠️\n"
                f"💰 `{fmt(usr.get('balance',0))}`",
                parse_mode="Markdown",
                reply_markup=joker_kbd(session, reveal_skull=skull_pos)
            )
            await q.answer("☠️ Череп! Вы проиграли!", show_alert=True)
        else:
            # УСПЕХ — добавляем в историю, переходим на следующий уровень
            session["history"].append(skull_pos)
            session["level"] += 1
            session["skull"] = random.randint(0, 2)   # новый череп для нового уровня
            save_db(db)
            await q.edit_message_text(
                joker_active_text(session, usr),
                parse_mode="Markdown",
                reply_markup=joker_kbd(session)
            )


# ── КОСТИ ────────────────────────────────────
# Команда: кости <число 1-6> <ставка>
# Угадал точно      → ×5
# Угадал +/-1       → ×1.5  (соседнее число по кругу: 1↔2↔3↔4↔5↔6↔1)
# Не угадал         → проигрыш

# Соседние числа — только ±1 (линейно, не по кругу)
DICE_NEIGHBORS = {
    1: {2},
    2: {1, 3},
    3: {2, 4},
    4: {3, 5},
    5: {4, 6},
    6: {5},
}

# ── СПОРТ (футбол / баскетбол / боулинг) ─────
# Telegram dice values per emoji:
#   ⚽  1-5 (miss) / 5 (goal)         — goal = value 5
#   🏀  1-4 (miss) / 4,5 (score)      — score = value 4 or 5
#   🎳  1-5 (spare/less) / 6 (strike) — strike = value 6

SPORT_CFG = {
    "футбол":    {"emoji": "⚽",  "win_values": {5},        "label": "Гол!",     "lose": "Мимо!",   "mult_win": 4.0},
    "баскетбол": {"emoji": "🏀",  "win_values": {4, 5},     "label": "В кольцо!","lose": "Промах!", "mult_win": 4.0},
    "боулинг":   {"emoji": "🎳",  "win_values": {6},        "label": "Страйк!",  "lose": "Не все!", "mult_win": 4.0},
}

async def cmd_sport(update: Update, ctx, sport_key: str):
    u = update.effective_user
    args = ctx.args
    cfg = SPORT_CFG[sport_key]
    if not args:
        await update.message.reply_text(
            f"{cfg['emoji']} Укажи ставку: `{sport_key} 500` или `{sport_key} миллион`", parse_mode="Markdown"
        ); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(args), usr["balance"])
    if not bet or bet < 1:
        await update.message.reply_text("❌ Неверная ставка. Пример: `миллион` / `500`", parse_mode="Markdown"); return
    if usr["balance"] < bet:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return
    usr["balance"] -= bet
    dice_msg = await update.effective_chat.send_dice(emoji=cfg["emoji"])
    val = dice_msg.dice.value
    await asyncio.sleep(3.5)
    if val in cfg["win_values"]:
        win = int(bet * cfg["mult_win"])
        profit = win - bet
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        save_db(db)
        await dice_msg.reply_text(
            f"```\n╔══════════════════════╗\n║  {cfg['emoji']}  {sport_key.upper():<14}║\n╚══════════════════════╝\n```\n"
            f"👤 {link(usr)}\n\n"
            f"💰 Ставка: `{fmt(bet)} Inter`\n\n"
            f"✅ *ВЫИГРЫШ!*\n"
            f"💸 +`{fmt(profit)} Inter` (×{cfg['mult_win']})\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        )
    else:
        save_db(db)
        await dice_msg.reply_text(
            f"```\n╔══════════════════════╗\n║  {cfg['emoji']}  {sport_key.upper():<14}║\n╚══════════════════════╝\n```\n"
            f"👤 {link(usr)}\n\n"
            f"💰 Ставка: `{fmt(bet)} Inter`\n\n"
            f"💀 *ПРОИГРЫШ!*\n"
            f"💸 -`{fmt(bet)} Inter`\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        )

async def cmd_football(update: Update, ctx):
    await cmd_sport(update, ctx, "футбол")

async def cmd_basketball(update: Update, ctx):
    await cmd_sport(update, ctx, "баскетбол")

async def cmd_bowling(update: Update, ctx):
    await cmd_sport(update, ctx, "боулинг")

# ── РОЛИ ─────────────────────────────────────
# Формат: {id: {name, price, emoji, description}}
async def cmd_dice(update: Update, ctx):
    u = update.effective_user
    args = ctx.args

    def usage():
        return update.message.reply_text(
            "🎲 *Кости*\n\n"
            "Команда: `кости 1-6 <ставка>`\n\n"
            "📌 Пример: `кости 4 500`\n\n"
            "💰 *Выплаты:*\n"
            "🎯 Точное попадание → ×5\n"
            "🔁 Соседнее число (±1) → ×3\n"
            "💀 Промахнулся → проигрыш",
            parse_mode="Markdown"
        )

    # Синтаксис: кости <число> <ставка>
    # Диапазон в названии команды: кости 1-6 <ставка> — парсим первый аргумент
    if len(args) < 2:
        await usage(); return

    guess_raw = args[0]
    bet_raw_parts = args[1:]  # всё остальное — ставка (может быть "10 миллионов")

    if not guess_raw.isdigit() or not (1 <= int(guess_raw) <= 6):
        await update.message.reply_text("❌ Укажи число от 1 до 6\nПример: `кости 4 500`", parse_mode="Markdown"); return
    if not bet_raw_parts:
        await usage(); return

    db  = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(bet_raw_parts), usr["balance"])
    if not bet or bet < 1:
        await update.message.reply_text("❌ Неверная ставка. Пример: `кости 4 миллион`", parse_mode="Markdown"); return

    guess = int(guess_raw)

    if usr["balance"] < bet:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return

    # Списываем ставку сразу
    usr["balance"] -= bet

    # Отправляем кубик — Telegram сам выбирает случайное значение
    dice_msg = await update.message.reply_dice(emoji="🎲")
    rolled   = dice_msg.dice.value   # берём реальное значение от Telegram

    await asyncio.sleep(3.5)         # ждём анимацию

    FACES = ["", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]

    if rolled == guess:
        win    = int(bet * 5)
        profit = win - bet
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        save_db(db)
        text = (
            "```\n"
            "╔══════════════════════╗\n"
            "║     🎲  КОСТИ        ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"👤 {link(usr)}\n\n"
            f"🎯 Ставка: `{guess}` на `{fmt(bet)} Inter`\n"
            f"🎲 Выпало: {FACES[rolled]} — `{rolled}`\n\n"
            f"✅ *ТОЧНОЕ ПОПАДАНИЕ!*\n"
            f"💰 Выигрыш: `+{fmt(profit)} Inter` (×5)\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`"
        )
    elif rolled in DICE_NEIGHBORS[guess]:
        win    = int(bet * 3)
        profit = win - bet
        usr["balance"] = min(usr["balance"] + win, MAX_BALANCE)
        save_db(db)
        text = (
            "```\n"
            "╔══════════════════════╗\n"
            "║     🎲  КОСТИ        ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"👤 {link(usr)}\n\n"
            f"🎯 Ставка: `{guess}` на `{fmt(bet)} Inter`\n"
            f"🎲 Выпало: {FACES[rolled]} — `{rolled}`\n\n"
            f"🔁 *СОСЕДНЕЕ ЧИСЛО!*\n"
            f"💰 Выигрыш: `+{fmt(profit)} Inter` (×3)\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`"
        )
    else:
        save_db(db)
        text = (
            "```\n"
            "╔══════════════════════╗\n"
            "║     🎲  КОСТИ        ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"👤 {link(usr)}\n\n"
            f"🎯 Ставка: `{guess}` на `{fmt(bet)} Inter`\n"
            f"🎲 Выпало: {FACES[rolled]} — `{rolled}`\n\n"
            f"💀 *ПРОМАХ!*\n"
            f"💸 Потеряно: `-{fmt(bet)} Inter`\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`"
        )

    await update.message.reply_text(text, parse_mode="Markdown")

# ── КАЗНА ────────────────────────────────────

def treasury_locked_kbd():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔓 Подключить за 10 000 Inter", callback_data="treasury_unlock_inline")
    ]])

async def cmd_treasury_info(update: Update, ctx):
    """Показать информацию о казне чата (команда 'казна' без аргументов)."""
    chat_id = update.effective_chat.id
    t = load_treasury()
    ct = get_chat_treasury(t, chat_id)
    if not ct["unlocked"]:
        await update.message.reply_text(
            "🏦 *Казна чата*\n\n"
            "Казна — важная часть чата. Тут хранятся все средства для новых пользователей.\n\n"
            "🔒 Казна ещё не подключена.\n"
            f"Стоимость подключения: `{fmt(TREASURY_UNLOCK_COST)} Inter`",
            parse_mode="Markdown", reply_markup=treasury_locked_kbd()
        )
        return
    bal = ct["balance"]
    reward = ct["reward_per_invite"]
    access_list = ct["vault_access"]
    def _fmt_access(e):
        if isinstance(e, dict):
            name = e.get("username", "?")
            limit = e.get("limit", 0)
            return f"@{name}" + (f" ({fmt(limit)})" if limit > 0 else "")
        return f"@{e}"
    access_str = ", ".join([_fmt_access(a) for a in access_list]) if access_list else "нет"
    await update.message.reply_text(
        "```\n"
        "╔══════════════════════╗\n"
        "║   🏦  КАЗНА ЧАТА     ║\n"
        "╚══════════════════════╝\n"
        "```\n"
        f"💰 Баланс: `{fmt(bal)} Inter`\n"
        f"🎁 Награда за приглашение: `{fmt(reward)} Inter`\n"
        f"🔑 Доступ «Взять»: {access_str}\n\n"
        "Казна — важная часть чата. Тут хранятся все средства для новых пользователей.\n\n"
        "Команды:\n"
        "`казна <сумма>` — пополнить\n"
        "`награда <сумма>` — задать награду за приглашение _(админ бота или владелец чата)_\n"
        "`взять <сумма>` — забрать _(спец. доступ)_",
        parse_mode="Markdown"
    )

async def cmd_treasury_deposit(update: Update, ctx):
    """Пополнить казну: казна <сумма>."""
    u = update.effective_user
    args = ctx.args
    if not args or not args[0].isdigit():
        await cmd_treasury_info(update, ctx); return
    amount = int(args[0])
    if amount < 1:
        await update.message.reply_text("❌ Минимальная сумма: 1 Inter"); return
    chat_id = update.effective_chat.id
    t = load_treasury()
    ct = get_chat_treasury(t, chat_id)
    if not ct["unlocked"]:
        await update.message.reply_text(
            "🔒 Казна не подключена.\n"
            f"Стоимость подключения: `{fmt(TREASURY_UNLOCK_COST)} Inter`",
            parse_mode="Markdown", reply_markup=treasury_locked_kbd()
        ); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    if usr["balance"] < amount:
        await update.message.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return
    usr["balance"] -= amount
    ct["balance"] += amount
    save_db(db); save_treasury(t)
    await update.message.reply_text(
        f"🏦 {link(usr)} пополнил казну!\n"
        f"➕ `+{fmt(amount)} Inter`\n"
        f"💰 Баланс казны: `{fmt(ct['balance'])} Inter`",
        parse_mode="Markdown"
    )

async def cmd_reward(update: Update, ctx):
    """Установить награду за приглашение: награда <сумма> (владелец бота, админ бота или владелец/админ чата)."""
    u = update.effective_user
    chat_id = update.effective_chat.id

    # Проверяем права: глобальный админ бота ИЛИ администратор/владелец этого чата
    bot_admin = is_admin(u.id)
    chat_admin = False
    if not bot_admin:
        try:
            chat_member = await ctx.bot.get_chat_member(chat_id, u.id)
            chat_admin = chat_member.status in ("administrator", "creator")
        except Exception:
            chat_admin = False

    if not bot_admin and not chat_admin:
        await update.message.reply_text("⛔️ Нет доступа."); return

    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Укажи: `награда <сумма>`", parse_mode="Markdown"); return
    amount = int(args[0])
    t = load_treasury()
    ct = get_chat_treasury(t, chat_id)
    if not ct["unlocked"]:
        await update.message.reply_text(
            "🔒 Казна не подключена.", parse_mode="Markdown",
            reply_markup=treasury_locked_kbd()
        ); return
    ct["reward_per_invite"] = amount
    save_treasury(t)
    await update.message.reply_text(
        f"✅ Награда за приглашение установлена: `{fmt(amount)} Inter`\n"
        f"Все, кто добавил участников в чат, получат эту сумму из казны.",
        parse_mode="Markdown"
    )

async def cmd_take_treasury(update: Update, ctx):
    """Взять из казны: взять <сумма> (только владелец или пользователи с доступом)."""
    u = update.effective_user
    chat_id = update.effective_chat.id
    t = load_treasury()
    ct = get_chat_treasury(t, chat_id)

    username_lower = (u.username or "").lower()
    # vault_access может содержать строки (старый формат) или dict {"username":..,"limit":n}
    def _va_name(entry):
        return (entry["username"] if isinstance(entry, dict) else entry).lower()
    def _va_limit(entry):
        return entry.get("limit", 0) if isinstance(entry, dict) else 0
    access_entry = next((e for e in ct.get("vault_access", []) if _va_name(e) == username_lower), None)
    has_access = is_owner(u.id) or access_entry is not None
    if not has_access:
        await update.message.reply_text("⛔️ Нет доступа к казне."); return
    if not ct["unlocked"]:
        await update.message.reply_text(
            "🔒 Казна не подключена.", reply_markup=treasury_locked_kbd()
        ); return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Укажи: `взять <сумма>`", parse_mode="Markdown"); return
    amount = int(args[0])
    if amount < 1:
        await update.message.reply_text("❌ Минимальная сумма: 1 Inter"); return
    # Проверяем лимит, если задан (только для не-владельца)
    if not is_owner(u.id) and access_entry is not None:
        limit = _va_limit(access_entry)
        if limit > 0 and amount > limit:
            await update.message.reply_text(
                f"⛔️ Превышен лимит! Тебе разрешено взять не более `{fmt(limit)} Inter`.",
                parse_mode="Markdown"
            ); return
    if ct["balance"] < amount:
        await update.message.reply_text(
            f"❌ В казне недостаточно средств!\n💰 Баланс казны: `{fmt(ct['balance'])} Inter`",
            parse_mode="Markdown"
        ); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    ct["balance"] -= amount
    usr["balance"] = min(usr["balance"] + amount, MAX_BALANCE)
    save_db(db); save_treasury(t)
    await update.message.reply_text(
        f"🏦 {link(usr)} забрал из казны: `{fmt(amount)} Inter`\n"
        f"💰 Остаток казны: `{fmt(ct['balance'])} Inter`\n"
        f"💼 Твой баланс: `{fmt(usr['balance'])} Inter`",
        parse_mode="Markdown"
    )

async def cmd_vault_access(update: Update, ctx):
    """Выдать доступ к «взять»: казна_доступ @username [лимит] [chat_id] (только владелец)."""
    u = update.effective_user
    if not is_owner(u.id):
        await update.message.reply_text("⛔️ Только владелец."); return
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Укажи: `казна_доступ @username <сумма>`\n"
            "Если пишешь из личных сообщений — добавь ID чата:\n"
            "`казна_доступ @username <сумма> -100xxxxxxxxxx`",
            parse_mode="Markdown"
        ); return
    target_name = args[0].lstrip("@").lower()
    # Парсим необязательный лимит (второй аргумент, если число > 0)
    limit = 0
    extra_args = args[1:]
    if extra_args and extra_args[0].lstrip("-").isdigit() and not extra_args[0].startswith("-1"):
        try:
            limit = int(extra_args[0])
            extra_args = extra_args[1:]
        except ValueError:
            pass
    # Определяем chat_id: если команда из группы — берём группу,
    # если из лички — берём следующий аргумент (chat_id группы)
    current_chat = update.effective_chat
    if current_chat.type in ("group", "supergroup"):
        chat_id = current_chat.id
    elif extra_args:
        try:
            chat_id = int(extra_args[0])
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный chat_id. Пример: `казна_доступ @username 5000 -1001234567890`",
                parse_mode="Markdown"
            ); return
    else:
        await update.message.reply_text(
            "⚠️ Ты пишешь из личных сообщений.\n"
            "Укажи ID группы последним аргументом:\n"
            "`казна_доступ @username <сумма> -100xxxxxxxxxx`\n\n"
            "Или напиши команду прямо в чате группы.",
            parse_mode="Markdown"
        ); return
    t = load_treasury()
    ct = get_chat_treasury(t, chat_id)
    # Поддержка старого формата (строки) и нового (dict)
    existing_idx = next(
        (i for i, e in enumerate(ct["vault_access"])
         if (e["username"] if isinstance(e, dict) else e).lower() == target_name),
        None
    )
    new_entry = {"username": target_name, "limit": limit}
    if existing_idx is not None:
        ct["vault_access"][existing_idx] = new_entry
        limit_str = f"`{fmt(limit)} Inter`" if limit > 0 else "без ограничений"
        await update.message.reply_text(
            f"🔄 Доступ обновлён: @{target_name}\n"
            f"💰 Новый лимит: {limit_str}",
            parse_mode="Markdown"
        )
    else:
        ct["vault_access"].append(new_entry)
        limit_str = f"`{fmt(limit)} Inter`" if limit > 0 else "без ограничений"
        await update.message.reply_text(
            f"🔑 Доступ к казне выдан: @{target_name}\n"
            f"💰 Лимит: {limit_str}\n"
            f"Может использовать команду `взять`.",
            parse_mode="Markdown"
        )
    save_treasury(t)

async def cb_treasury_unlock_inline(update: Update, ctx):
    """Кнопка подключения казны прямо из чата."""
    q = update.callback_query
    u = q.from_user
    chat_member = await ctx.bot.get_chat_member(q.message.chat_id, u.id)
    is_chat_admin = chat_member.status in ("administrator", "creator")
    if not is_chat_admin and not is_owner(u.id):
        await q.answer("⛔️ Только администратор чата может подключить казну.", show_alert=True); return
    chat_id = q.message.chat_id
    t = load_treasury()
    ct = get_chat_treasury(t, chat_id)
    if ct["unlocked"]:
        await q.answer()
        await q.edit_message_text("✅ Казна уже подключена!", parse_mode="Markdown"); return
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    if usr["balance"] < TREASURY_UNLOCK_COST:
        await q.answer(
            f"❌ Недостаточно Inter! Нужно {fmt(TREASURY_UNLOCK_COST)}, у вас {fmt(usr['balance'])}.",
            show_alert=True
        ); return
    await q.answer()
    usr["balance"] -= TREASURY_UNLOCK_COST
    ct["unlocked"] = True
    save_db(db); save_treasury(t)
    await q.edit_message_text(
        "```\n"
        "╔══════════════════════╗\n"
        "║   🏦  КАЗНА ЧАТА     ║\n"
        "╚══════════════════════╝\n"
        "```\n"
        "✅ Казна подключена!\n\n"
        f"💰 Баланс: `0 Inter`\n"
        f"🎁 Награда за приглашение: `0 Inter`\n\n"
        "Казна — важная часть чата. Тут хранятся все средства для новых пользователей.\n\n"
        "Команды:\n"
        "`казна <сумма>` — пополнить\n"
        "`награда <сумма>` — задать награду _(админ бота или владелец чата)_\n"
        "`взять <сумма>` — забрать _(спец. доступ)_",
        parse_mode="Markdown"
    )

# ── ДУЭЛИ ────────────────────────────────────
# Хранит активные дуэли: {duel_id: {challenger_id, challenger_name, target_username, bet, chat_id, msg_id}}
DUELS: dict = {}
_duel_counter = 0

def next_duel_id():
    global _duel_counter
    _duel_counter += 1
    return str(_duel_counter)

async def cmd_duel(update: Update, ctx):
    u = update.effective_user
    args = ctx.args
    msg = update.message

    if len(args) < 2:
        await msg.reply_text(
            "⚔️ *Дуэль*\n\n"
            "Формат: `дуэль @username <сумма>`\n"
            "📌 Пример: `дуэль @username 5000`",
            parse_mode="Markdown"
        ); return

    target_raw = args[0].lstrip("@")
    bet_raw_parts = args[1:]

    if not bet_raw_parts:
        await msg.reply_text("❌ Укажи ставку. Пример: `дуэль @username миллион`", parse_mode="Markdown"); return

    db = load_db()
    challenger = get_user(db, u.id, u.username or u.first_name)
    bet = parse_amount(" ".join(bet_raw_parts), challenger["balance"])
    if not bet or bet < 1:
        await msg.reply_text("❌ Неверная ставка. Пример: `дуэль @username миллион`", parse_mode="Markdown"); return

    if target_raw.lower() == (u.username or "").lower():
        await msg.reply_text("❌ Нельзя вызвать самого себя на дуэль."); return

    # Проверяем, есть ли цель в БД
    uid_k, target = find_by_username(db, target_raw)
    if not target:
        await msg.reply_text(f"❌ Игрок @{target_raw} не найден в базе.\nЕму нужно хотя бы раз написать боту.", parse_mode="Markdown"); return

    if challenger["balance"] < bet:
        await msg.reply_text(
            f"❌ Недостаточно Inter!\n💰 Баланс: `{fmt(challenger['balance'])} Inter`",
            parse_mode="Markdown"
        ); save_db(db); return

    # Блокируем ставку у вызывающего
    challenger["balance"] -= bet
    save_db(db)

    duel_id = next_duel_id()
    challenger_name = u.username or u.first_name

    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять дуэль", callback_data=f"duel_accept_{duel_id}"),
            InlineKeyboardButton("❌ Отказаться",    callback_data=f"duel_decline_{duel_id}"),
        ]
    ])

    sent = await msg.reply_text(
        "```\n"
        "╔══════════════════════╗\n"
        "║     ⚔️  ДУЭЛЬ        ║\n"
        "╚══════════════════════╝\n"
        "```\n"
        f"🔫 [{challenger_name}](tg://user?id={u.id}) вызывает на дуэль @{target_raw}!\n\n"
        f"💰 Ставка: `{fmt(bet)} Inter`\n"
        f"🏆 Победитель забирает: `{fmt(bet * 2)} Inter`\n\n"
        f"@{target_raw}, принимаешь вызов? 👇",
        parse_mode="Markdown",
        reply_markup=kbd
    )

    DUELS[duel_id] = {
        "challenger_id":   u.id,
        "challenger_name": challenger_name,
        "target_username": target_raw.lower(),
        "target_id":       int(uid_k),
        "bet":             bet,
        "chat_id":         update.effective_chat.id,
        "msg_id":          sent.message_id,
    }

async def cb_duel(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    u = q.from_user
    data = q.data

    parts = data.split("_")
    action   = parts[1]   # accept / decline
    duel_id  = parts[2]

    duel = DUELS.get(duel_id)
    if not duel:
        await q.answer("Дуэль уже завершена или истекла.", show_alert=True); return

    # Только цель может принять/отказаться (и вызывающий может отменить через decline)
    is_target     = (u.username or "").lower() == duel["target_username"] or u.id == duel["target_id"]
    is_challenger = u.id == duel["challenger_id"]

    if not is_target and not is_challenger:
        await q.answer("Эта дуэль не твоя!", show_alert=True); return

    if action == "decline":
        # Возвращаем ставку вызывающему
        db = load_db()
        ch = get_user(db, duel["challenger_id"], duel["challenger_name"])
        ch["balance"] = min(ch["balance"] + duel["bet"], MAX_BALANCE)
        save_db(db)
        DUELS.pop(duel_id, None)
        decliner = u.username or u.first_name
        await q.edit_message_text(
            "```\n"
            "╔══════════════════════╗\n"
            "║     ⚔️  ДУЭЛЬ        ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"❌ @{decliner} отказался от дуэли.\n"
            f"💰 Ставка `{fmt(duel['bet'])} Inter` возвращена [{duel['challenger_name']}](tg://user?id={duel['challenger_id']}).",
            parse_mode="Markdown"
        )
        await q.answer("Дуэль отменена.")
        return

    if action == "accept":
        if not is_target:
            await q.answer("Только вызванный может принять дуэль!", show_alert=True); return

        db = load_db()
        target_usr = get_user(db, u.id, u.username or u.first_name)

        if target_usr["balance"] < duel["bet"]:
            await q.answer(
                f"❌ Недостаточно Inter! Нужно {fmt(duel['bet'])}, у тебя {fmt(target_usr['balance'])}.",
                show_alert=True
            ); return

        # Списываем у принявшего
        target_usr["balance"] -= duel["bet"]

        # Определяем победителя 50/50
        winner_is_challenger = random.choice([True, False])
        prize = duel["bet"] * 2

        ch = get_user(db, duel["challenger_id"], duel["challenger_name"])

        if winner_is_challenger:
            winner_name = duel["challenger_name"]
            winner_id   = duel["challenger_id"]
            loser_name  = u.username or u.first_name
            ch["balance"] = min(ch["balance"] + prize, MAX_BALANCE)
        else:
            winner_name = u.username or u.first_name
            winner_id   = u.id
            loser_name  = duel["challenger_name"]
            target_usr["balance"] = min(target_usr["balance"] + prize, MAX_BALANCE)

        save_db(db)
        DUELS.pop(duel_id, None)

        await q.edit_message_text(
            "```\n"
            "╔══════════════════════╗\n"
            "║     ⚔️  ДУЭЛЬ        ║\n"
            "╚══════════════════════╝\n"
            "```\n"
            f"🔫 [{duel['challenger_name']}](tg://user?id={duel['challenger_id']}) vs "
            f"[{u.username or u.first_name}](tg://user?id={u.id})\n\n"
            f"💰 Ставка: `{fmt(duel['bet'])} Inter` каждый\n\n"
            f"🏆 *Победитель:* [{winner_name}](tg://user?id={winner_id})!\n"
            f"💸 Выигрыш: `+{fmt(prize)} Inter`\n"
            f"💀 Проиграл: @{loser_name}",
            parse_mode="Markdown"
        )
        await q.answer(f"🏆 Победил {winner_name}!", show_alert=True)

# ── РАБОТЫ (вложения) ─────────────────────────
JOBS_FILE = "jobs_owned.json"

JOBS_LIST = [
    {"id": "cleaner",    "name": "Уборщик",       "emoji": "🧹", "price": 50_000,     "income_per_hour": 500,    "description": "Метёт улицы, стабильный доход"},
    {"id": "courier",    "name": "Курьер",         "emoji": "🛵", "price": 150_000,    "income_per_hour": 1_500,  "description": "Развозит заказы по городу"},
    {"id": "barista",    "name": "Бариста",        "emoji": "☕", "price": 300_000,    "income_per_hour": 3_000,  "description": "Варит лучший кофе в городе"},
    {"id": "programmer", "name": "Программист",    "emoji": "💻", "price": 750_000,    "income_per_hour": 8_000,  "description": "Пишет код за большие деньги"},
    {"id": "trader",     "name": "Трейдер",        "emoji": "📈", "price": 2_000_000,  "income_per_hour": 22_000, "description": "Играет на бирже"},
    {"id": "banker",     "name": "Банкир",         "emoji": "🏦", "price": 5_000_000,  "income_per_hour": 60_000, "description": "Управляет финансами"},
    {"id": "tycoon",     "name": "Магнат",         "emoji": "🏙", "price": 15_000_000, "income_per_hour": 200_000,"description": "Владеет бизнес-империей"},
    {"id": "oligarch",   "name": "Олигарх",        "emoji": "🛥", "price": 50_000_000, "income_per_hour": 750_000,"description": "Яхты, виллы, Inter"},
]

def load_jobs_db():
    if not os.path.exists(JOBS_FILE): return {}
    with open(JOBS_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_jobs_db(jdb):
    with open(JOBS_FILE, "w", encoding="utf-8") as f: json.dump(jdb, f, ensure_ascii=False, indent=2)

def get_user_jobs(uid):
    jdb = load_jobs_db()
    k = str(uid)
    return jdb.get(k, {})  # {job_id: last_collected_timestamp}

def collect_income(uid, username):
    """Собирает накопленный доход по всем работам. Возвращает (total_earned, details)."""
    jdb = load_jobs_db()
    k = str(uid)
    user_jobs = jdb.get(k, {})
    if not user_jobs:
        return 0, []

    now = time.time()
    total = 0
    details = []
    job_map = {j["id"]: j for j in JOBS_LIST}

    for job_id, last_ts in user_jobs.items():
        job = job_map.get(job_id)
        if not job: continue
        hours_passed = (now - last_ts) / 3600
        earned = int(hours_passed * job["income_per_hour"])
        if earned > 0:
            total += earned
            details.append((job, earned, hours_passed))
            user_jobs[job_id] = now  # сброс таймера

    jdb[k] = user_jobs
    save_jobs_db(jdb)
    return total, details

def jobs_main_text(usr):
    """Главное меню работ — список купленных + магазин."""
    uid = usr.get("uid", 0)
    user_jobs = get_user_jobs(uid)
    balance = usr.get("balance", 0)
    job_map = {j["id"]: j for j in JOBS_LIST}
    now = time.time()

    lines = [
        "```\n╔══════════════════════╗\n║   💼  МОИ РАБОТЫ     ║\n╚══════════════════════╝\n```\n",
        f"👤 {link(usr)}\n",
        f"💰 Баланс: `{fmt(balance)} Inter`\n",
    ]

    if user_jobs:
        total_per_hour = 0
        total_pending = 0
        lines.append("\n📋 *Твои работы:*\n")
        for job_id, last_ts in user_jobs.items():
            job = job_map.get(job_id)
            if not job: continue
            hours = (now - last_ts) / 3600
            pending = int(hours * job["income_per_hour"])
            total_per_hour += job["income_per_hour"]
            total_pending += pending
            lines.append(f"{job['emoji']} {job['name']} — `{fmt(job['income_per_hour'])}/ч` · накоплено `{fmt(pending)}`\n")
        lines.append(f"\n💸 Итого в час: `{fmt(total_per_hour)}` · к получению: `{fmt(total_pending)} Inter`\n")
    else:
        lines.append("\n_Работ пока нет. Купи первую ниже!_\n")

    return "".join(lines)

def jobs_main_kbd(usr):
    """Кнопки: купленные работы (→ подменю), некупленные (→ подменю покупки), кнопка «Собрать всё»."""
    uid = usr.get("uid", 0)
    user_jobs = get_user_jobs(uid)
    rows = []

    for job in JOBS_LIST:
        if job["id"] in user_jobs:
            label = f"✅ {job['emoji']} {job['name']} — {fmt(job['income_per_hour'])}/ч"
            rows.append([InlineKeyboardButton(label, callback_data=f"job_view_{job['id']}")])
        else:
            label = f"{job['emoji']} {job['name']} — {fmt(job['price'])} Inter"
            rows.append([InlineKeyboardButton(label, callback_data=f"job_buyview_{job['id']}")])

    if user_jobs:
        rows.append([InlineKeyboardButton("💰 Собрать весь доход", callback_data="job_collectall")])

    return InlineKeyboardMarkup(rows)

def job_card_text(usr, job_id):
    """Карточка купленной работы."""
    job = next((j for j in JOBS_LIST if j["id"] == job_id), None)
    if not job: return "Ошибка"
    user_jobs = get_user_jobs(usr.get("uid", 0))
    last_ts = user_jobs.get(job_id, time.time())
    hours = (time.time() - last_ts) / 3600
    pending = int(hours * job["income_per_hour"])
    return (
        f"```\n╔══════════════════════╗\n║  {job['emoji']}  {job['name']:<16}║\n╚══════════════════════╝\n```\n"
        f"👤 {link(usr)}\n\n"
        f"📝 {job['description']}\n\n"
        f"💸 Доход: `{fmt(job['income_per_hour'])} Inter/ч`\n"
        f"⏱ Работает: `{hours:.1f} ч`\n"
        f"💰 Накоплено: `{fmt(pending)} Inter`\n\n"
        f"💼 Баланс: `{fmt(usr.get('balance', 0))} Inter`"
    )

def job_card_kbd(job_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Забрать заработок", callback_data=f"job_collect_{job_id}")],
        [InlineKeyboardButton("🚪 Уволиться",         callback_data=f"job_fire_{job_id}")],
        [InlineKeyboardButton("‹ Назад",              callback_data="job_back")],
    ])

def job_buyview_text(usr, job_id):
    """Карточка работы для покупки."""
    job = next((j for j in JOBS_LIST if j["id"] == job_id), None)
    if not job: return "Ошибка"
    payback_hours = job["price"] / job["income_per_hour"]
    return (
        f"```\n╔══════════════════════╗\n║  {job['emoji']}  {job['name']:<16}║\n╚══════════════════════╝\n```\n"
        f"👤 {link(usr)}\n\n"
        f"📝 {job['description']}\n\n"
        f"💸 Доход: `{fmt(job['income_per_hour'])} Inter/ч`\n"
        f"🏷 Цена: `{fmt(job['price'])} Inter`\n"
        f"⏳ Окупаемость: `{payback_hours:.0f} ч`\n\n"
        f"💼 Твой баланс: `{fmt(usr.get('balance', 0))} Inter`"
    )

def job_buyview_kbd(job_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Купить", callback_data=f"job_buy_{job_id}")],
        [InlineKeyboardButton("‹ Назад",  callback_data="job_back")],
    ])

def jobs_owned_text(usr):
    """Карточка купленных работ — показывается после команды 'работа' если есть работы."""
    uid = usr.get("uid", 0)
    user_jobs = get_user_jobs(uid)
    job_map = {j["id"]: j for j in JOBS_LIST}
    now = time.time()

    total_per_hour = 0
    total_pending = 0
    lines = [
        "```\n╔══════════════════════╗\n║   💼  МОИ РАБОТЫ     ║\n╚══════════════════════╝\n```\n",
        f"👤 {link(usr)}\n",
        f"💰 Баланс: `{fmt(usr.get('balance', 0))} Inter`\n\n",
        "📋 *Твои работы:*\n",
    ]

    for job_id, last_ts in user_jobs.items():
        job = job_map.get(job_id)
        if not job: continue
        hours = (now - last_ts) / 3600
        pending = int(hours * job["income_per_hour"])
        total_per_hour += job["income_per_hour"]
        total_pending += pending
        lines.append(
            f"\n{job['emoji']} *{job['name']}*\n"
            f"  💸 Доход: `{fmt(job['income_per_hour'])} Inter/ч`\n"
            f"  ⏱ Работает: `{hours:.1f} ч`\n"
            f"  💰 Накоплено: `{fmt(pending)} Inter`\n"
        )

    lines.append(
        f"\n━━━━━━━━━━━━━━━━━━━\n"
        f"💸 Суммарно в час: `{fmt(total_per_hour)} Inter`\n"
        f"📦 Всего к получению: `{fmt(total_pending)} Inter`"
    )
    return "".join(lines)

def jobs_owned_kbd(usr):
    """Кнопки для карточки купленных работ."""
    uid = usr.get("uid", 0)
    user_jobs = get_user_jobs(uid)
    job_map = {j["id"]: j for j in JOBS_LIST}
    rows = []

    # Кнопка для каждой купленной работы — уволиться
    for job_id in user_jobs:
        job = job_map.get(job_id)
        if not job: continue
        rows.append([InlineKeyboardButton(
            f"🚪 Уволиться с «{job['emoji']} {job['name']}»",
            callback_data=f"job_fire_{job_id}"
        )])

    # Собрать всё и список работ
    rows.append([InlineKeyboardButton("💰 Собрать весь доход", callback_data="job_collectall")])
    rows.append([InlineKeyboardButton("📋 Список работ",       callback_data="job_shop")])
    return InlineKeyboardMarkup(rows)

async def cmd_work(update: Update, ctx):
    u = update.effective_user
    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    save_db(db)

    user_jobs = get_user_jobs(u.id)

    if user_jobs:
        # Есть купленные работы — показываем сводную карточку
        await update.message.reply_text(
            jobs_owned_text(usr),
            parse_mode="Markdown",
            reply_markup=jobs_owned_kbd(usr)
        )
    else:
        # Нет работ — сразу магазин
        await update.message.reply_text(
            jobs_main_text(usr),
            parse_mode="Markdown",
            reply_markup=jobs_main_kbd(usr)
        )

async def cb_jobs(update: Update, ctx):
    if update.effective_user and is_banned(update.effective_user.id):
        await update.callback_query.answer("⛔ Вы заблокированы.", show_alert=True); return
    q = update.callback_query
    u = q.from_user
    data = q.data
    await q.answer()

    db = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)

    # ── Назад (умный): если есть работы — карточка, иначе магазин ──
    if data == "job_back":
        user_jobs = get_user_jobs(u.id)
        if user_jobs:
            await q.edit_message_text(
                jobs_owned_text(usr),
                parse_mode="Markdown",
                reply_markup=jobs_owned_kbd(usr)
            )
        else:
            await q.edit_message_text(
                jobs_main_text(usr),
                parse_mode="Markdown",
                reply_markup=jobs_main_kbd(usr)
            )
        return

    # ── Список работ (магазин) ──
    if data == "job_shop":
        await q.edit_message_text(
            jobs_main_text(usr),
            parse_mode="Markdown",
            reply_markup=jobs_main_kbd(usr)
        ); return

    # ── Открыть карточку купленной работы ──
    if data.startswith("job_view_"):
        job_id = data[9:]
        await q.edit_message_text(
            job_card_text(usr, job_id),
            parse_mode="Markdown",
            reply_markup=job_card_kbd(job_id)
        ); return

    # ── Открыть карточку для покупки ──
    if data.startswith("job_buyview_"):
        job_id = data[12:]
        await q.edit_message_text(
            job_buyview_text(usr, job_id),
            parse_mode="Markdown",
            reply_markup=job_buyview_kbd(job_id)
        ); return

    # ── Купить работу ──
    if data.startswith("job_buy_"):
        job_id = data[8:]
        job = next((j for j in JOBS_LIST if j["id"] == job_id), None)
        if not job: return

        user_jobs = get_user_jobs(u.id)
        if job_id in user_jobs:
            await q.answer("✅ Эта работа уже куплена!", show_alert=True); return

        if usr["balance"] < job["price"]:
            await q.answer(
                f"❌ Недостаточно Inter!\nНужно: {fmt(job['price'])}\nУ тебя: {fmt(usr['balance'])}",
                show_alert=True
            ); return

        usr["balance"] -= job["price"]
        save_db(db)

        jdb = load_jobs_db()
        k = str(u.id)
        if k not in jdb: jdb[k] = {}
        jdb[k][job_id] = time.time()
        save_jobs_db(jdb)

        await q.answer(f"✅ Теперь ты работаешь: {job['emoji']} {job['name']}!", show_alert=True)
        usr_fresh = get_user(load_db(), u.id, u.username or u.first_name)
        await q.edit_message_text(
            jobs_owned_text(usr_fresh),
            parse_mode="Markdown",
            reply_markup=jobs_owned_kbd(usr_fresh)
        ); return

    # ── Забрать доход по одной работе ──
    if data.startswith("job_collect_"):
        job_id = data[12:]
        job = next((j for j in JOBS_LIST if j["id"] == job_id), None)
        if not job: return

        jdb = load_jobs_db()
        k = str(u.id)
        user_jobs = jdb.get(k, {})
        if job_id not in user_jobs:
            await q.answer("Работа не найдена.", show_alert=True); return

        last_ts = user_jobs[job_id]
        hours = (time.time() - last_ts) / 3600
        earned = int(hours * job["income_per_hour"])

        if earned == 0:
            await q.answer("💤 Ещё ничего не накопилось, приходи позже!", show_alert=True); return

        user_jobs[job_id] = time.time()
        jdb[k] = user_jobs
        save_jobs_db(jdb)

        usr["balance"] = min(usr["balance"] + earned, MAX_BALANCE)
        save_db(db)

        await q.answer(f"💰 Получено +{fmt(earned)} Inter!", show_alert=True)
        usr_fresh = get_user(load_db(), u.id, u.username or u.first_name)
        await q.edit_message_text(
            job_card_text(usr_fresh, job_id),
            parse_mode="Markdown",
            reply_markup=job_card_kbd(job_id)
        ); return

    # ── Собрать весь доход сразу ──
    if data == "job_collectall":
        total, details = collect_income(u.id, u.username or u.first_name)
        if total == 0:
            await q.answer("💤 Ещё ничего не накопилось. Заходи позже!", show_alert=True); return
        usr["balance"] = min(usr["balance"] + total, MAX_BALANCE)
        save_db(db)
        detail_lines = "".join(
            f"{job['emoji']} {job['name']}: `+{fmt(earned)} Inter` ({hours:.1f}ч)\n"
            for job, earned, hours in details
        )
        await q.edit_message_text(
            "```\n╔══════════════════════╗\n║   💰  ДОХОД СОБРАН   ║\n╚══════════════════════╝\n```\n"
            f"👤 {link(usr)}\n\n"
            f"{detail_lines}\n"
            f"✅ Итого: `+{fmt(total)} Inter`\n"
            f"💼 Баланс: `{fmt(usr['balance'])} Inter`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("‹ Мои работы", callback_data="job_back")
            ]])
        ); return

    # ── Уволиться ──
    if data.startswith("job_fire_"):
        job_id = data[9:]
        job = next((j for j in JOBS_LIST if j["id"] == job_id), None)
        if not job: return

        # Сначала автоматически собираем накопленный доход
        jdb = load_jobs_db()
        k = str(u.id)
        user_jobs = jdb.get(k, {})
        earned = 0
        if job_id in user_jobs:
            last_ts = user_jobs[job_id]
            hours = (time.time() - last_ts) / 3600
            earned = int(hours * job["income_per_hour"])
            del user_jobs[job_id]
            jdb[k] = user_jobs
            save_jobs_db(jdb)

        if earned > 0:
            usr["balance"] = min(usr["balance"] + earned, MAX_BALANCE)
        save_db(db)

        earned_line = f"💰 Последняя выплата: `+{fmt(earned)} Inter`\n" if earned > 0 else ""
        # Определяем что показать после увольнения
        remaining_jobs = get_user_jobs(u.id)
        usr_fresh = get_user(load_db(), u.id, u.username or u.first_name)
        fire_kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "‹ Мои работы" if remaining_jobs else "‹ Список работ",
                callback_data="job_back"
            )
        ]])
        await q.edit_message_text(
            f"```\n╔══════════════════════╗\n║   🚪  УВОЛЕН         ║\n╚══════════════════════╝\n```\n"
            f"👤 {link(usr_fresh)}\n\n"
            f"Ты уволился с работы {job['emoji']} *{job['name']}*.\n"
            f"{earned_line}"
            f"💼 Баланс: `{fmt(usr_fresh['balance'])} Inter`",
            parse_mode="Markdown",
            reply_markup=fire_kbd
        ); return

# ── РОУТЕР ────────────────────────────────────

async def cmd_ban(update: Update, ctx):
    u = update.effective_user
    if not is_admin(u.id): return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Укажи: `чс <ID>`", parse_mode="Markdown"); return
    target_id = int(args[0])
    if target_id == OWNER_ID:
        await update.message.reply_text("❌ Нельзя заблокировать владельца."); return
    bl = load_blacklist()
    if target_id in bl:
        await update.message.reply_text(f"⚠️ `{target_id}` уже в ЧС.", parse_mode="Markdown"); return
    bl.append(target_id)
    save_blacklist(bl)
    await update.message.reply_text(f"🚫 `{target_id}` внесён в чёрный список.", parse_mode="Markdown")

async def cmd_unban(update: Update, ctx):
    u = update.effective_user
    if not is_admin(u.id): return
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Укажи: `разчс <ID>`", parse_mode="Markdown"); return
    target_id = int(args[0])
    bl = load_blacklist()
    if target_id not in bl:
        await update.message.reply_text(f"⚠️ `{target_id}` не в ЧС.", parse_mode="Markdown"); return
    bl.remove(target_id)
    save_blacklist(bl)
    await update.message.reply_text(f"✅ `{target_id}` удалён из чёрного списка.", parse_mode="Markdown")

async def cmd_banlist(update: Update, ctx):
    u = update.effective_user
    if not is_admin(u.id): return
    bl = load_blacklist()
    if not bl:
        await update.message.reply_text("✅ Чёрный список пуст."); return
    ids = "\n".join(f"`{i}`" for i in bl)
    await update.message.reply_text(f"🚫 *Чёрный список:*\n{ids}", parse_mode="Markdown")

# Список игровых команд для проверки бана
GAME_COMMANDS = (
    "б", "баланс", "бонус", "слоты", "краш", "мины", "джокер", "кости",
    "дать", "обнулить", "выдать", "п ", "админ", "казна", "футбол",
    "баскетбол", "боулинг", "биржа", "работы", "дуэль", "донат",
    "чс", "разчс", "топ", "награда", "взять", "забрать"
)

async def router(update: Update, ctx):
    if not update.message or not update.message.text: return

    txt = update.message.text.strip()
    tl  = txt.lower()

    # Для забаненных — отвечаем только на игровые команды
    if update.effective_user and is_banned(update.effective_user.id):
        is_game_cmd = any(tl == cmd or tl.startswith(cmd + " ") for cmd in GAME_COMMANDS)
        if is_game_cmd:
            await update.message.reply_text(
                "🚫 Вы в чёрном списке. Обратитесь в поддержку @interbot_support"
            )
        return

    # Кнопки нижней клавиатуры
    _words = tl.strip().split()
    _last = _words[-1] if _words else ""
    if _last == "донат" and len(_words) <= 2:
        await cmd_donate(update, ctx)
    elif _last == "команды" and len(_words) <= 2:
        await cmd_start(update, ctx)
    elif _last == "бонус" and len(_words) <= 2:
        await cmd_bonus(update, ctx)
    elif _last == "связь" and len(_words) <= 2:
        await cmd_svyaz(update, ctx)
    elif tl.startswith("чс "):
        ctx.args = tl.split()[1:]; await cmd_ban(update, ctx)
    elif tl.startswith("разчс "):
        ctx.args = tl.split()[1:]; await cmd_unban(update, ctx)
    elif tl == "чс список":
        await cmd_banlist(update, ctx)
    elif tl in ("б", "баланс"):
        await cmd_balance(update, ctx)
    elif tl == "бонус":
        await cmd_bonus(update, ctx)
    elif tl == "админ":
        await cmd_admin(update, ctx)
    elif tl.startswith("слоты "):
        ctx.args = tl.split()[1:]; await cmd_slots(update, ctx)
    elif tl.startswith("краш "):
        ctx.args = tl.split()[1:]; await cmd_crash(update, ctx)
    elif tl.startswith("мины "):
        ctx.args = tl.split()[1:]; await cmd_mines(update, ctx)
    elif tl.startswith("джокер "):
        ctx.args = tl.split()[1:]; await cmd_joker(update, ctx)
    elif tl.startswith("кости "):
        ctx.args = tl.split()[1:]; await cmd_dice(update, ctx)
    elif tl == "кости":
        await update.message.reply_text("💡 Пример: `кости 4 500`", parse_mode="Markdown")
    elif tl.startswith("дать "):
        ctx.args = txt.split()[1:]; await cmd_give_user(update, ctx)
    elif tl.startswith("обнулить "):
        ctx.args = txt.split()[1:]; await cmd_obnu(update, ctx)
    elif tl.startswith("выдать "):
        ctx.args = tl.split()[1:]; await cmd_vydat(update, ctx)
    elif tl.startswith("п "):
        ctx.args = txt.split()[1:]; await cmd_transfer(update, ctx)
    elif tl.startswith("админ_выдать "):
        ctx.args = tl.split()[1:]; await cmd_grant_admin(update, ctx)
    elif tl.startswith("админ_снять "):
        ctx.args = tl.split()[1:]; await cmd_revoke_admin(update, ctx)
    elif tl.startswith("забрать "):
        ctx.args = txt.split()[1:]; await cmd_take_user(update, ctx)
    elif tl == "казна":
        await cmd_treasury_info(update, ctx)
    elif tl.startswith("казна "):
        ctx.args = tl.split()[1:]; await cmd_treasury_deposit(update, ctx)
    elif tl.startswith("награда "):
        ctx.args = tl.split()[1:]; await cmd_reward(update, ctx)
    elif tl.startswith("взять "):
        ctx.args = tl.split()[1:]; await cmd_take_treasury(update, ctx)
    elif tl.startswith("казна_доступ "):
        ctx.args = txt.split()[1:]; await cmd_vault_access(update, ctx)
    elif tl.startswith("футбол "):
        ctx.args = tl.split()[1:]; await cmd_football(update, ctx)
    elif tl == "футбол":
        await update.message.reply_text("💡 Пример: `футбол 500`\n⚽ Выигрыш ×2 за гол (значение 5)", parse_mode="Markdown")
    elif tl.startswith("баскетбол "):
        ctx.args = tl.split()[1:]; await cmd_basketball(update, ctx)
    elif tl == "баскетбол":
        await update.message.reply_text("💡 Пример: `баскетбол 500`\n🏀 Выигрыш ×1.8 за попадание (значения 4-5)", parse_mode="Markdown")
    elif tl.startswith("боулинг "):
        ctx.args = tl.split()[1:]; await cmd_bowling(update, ctx)
    elif tl == "боулинг":
        await update.message.reply_text("💡 Пример: `боулинг 500`\n🎳 Выигрыш ×2 за страйк (значение 6)", parse_mode="Markdown")
    elif tl.startswith("дуэль "):
        ctx.args = txt.split()[1:]; await cmd_duel(update, ctx)
    elif tl == "дуэль":
        await update.message.reply_text("⚔️ Формат: `дуэль @username <сумма>`\nПример: `дуэль @username 5000`", parse_mode="Markdown")
    elif tl in ("работа", "работы"):
        await cmd_work(update, ctx)
    elif tl == "биржа":
        await cmd_exchange(update, ctx)
    elif tl == "слоты":
        await update.message.reply_text("💡 Пример: `слоты 500`", parse_mode="Markdown")
    elif tl == "краш":
        await update.message.reply_text("💡 Пример: `краш 500 2.5`", parse_mode="Markdown")
    elif tl == "мины":
        await update.message.reply_text("💡 Пример: `мины 500`", parse_mode="Markdown")
    elif tl == "джокер":
        await update.message.reply_text("💡 Пример: `джокер 500`", parse_mode="Markdown")

# ── MAIN ─────────────────────────────────────
def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)   # параллельная обработка апдейтов
        .build()
    )
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("donate", cmd_donate))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(CallbackQueryHandler(cb_bonus,     pattern="^claim_bonus$"))
    app.add_handler(CallbackQueryHandler(cb_admin,     pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(cb_donate,    pattern="^(donate_menu|buy_)"))
    app.add_handler(CallbackQueryHandler(cb_mines,     pattern="^mn_"))
    app.add_handler(CallbackQueryHandler(cb_crash,     pattern="^crash_cashout_"))
    app.add_handler(CallbackQueryHandler(cb_joker,     pattern="^jk_"))
    app.add_handler(CallbackQueryHandler(cb_treasury_unlock_inline, pattern="^treasury_unlock_inline$"))
    app.add_handler(CallbackQueryHandler(cb_duel,      pattern="^duel_"))
    app.add_handler(CallbackQueryHandler(cb_jobs,      pattern="^job_"))
    app.add_handler(CallbackQueryHandler(cb_exchange,  pattern="^ex_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
    app.job_queue.run_repeating(exchange_job, interval=3600, first=10)
    print("✅ InterBot запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
