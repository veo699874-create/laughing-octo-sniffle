"""
╔══════════════════════════════════════╗
║  БИРЖА — модуль для InterBot v17     ║
║  Файл: exchange.py                   ║
╚══════════════════════════════════════╝

КАК ПОДКЛЮЧИТЬ (в bot.py):
  1. from exchange import (
         cmd_exchange, cb_exchange,
         exchange_job                  # планировщик обновления цен
     )
  2. В main(), перед app.run_polling():
         job_queue = app.job_queue
         job_queue.run_repeating(exchange_job, interval=3600, first=10)
  3. Добавить хендлеры:
         app.add_handler(CallbackQueryHandler(cb_exchange, pattern="^ex_"))
  4. В router(), добавить ветку:
         elif tl == "биржа":
             await cmd_exchange(update, ctx)
"""

import json, os, time, random, math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# ── Файлы данных ──────────────────────────────────────────────────────────────
EXCHANGE_FILE  = "exchange.json"   # цены + история
PORTFOLIO_FILE = "portfolio.json"  # портфели игроков

# ── Компании ──────────────────────────────────────────────────────────────────
STOCKS = [
    {"id": "gazprom",   "name": "Газпром",      "ticker": "GAZP", "emoji": "⛽",
     "base_price": 90000,    "volatility": 0.06},
    {"id": "sber",      "name": "Сбербанк",     "ticker": "SBER", "emoji": "🏦",
     "base_price": 145000,   "volatility": 0.05},
    {"id": "lukoil",    "name": "Лукойл",       "ticker": "LKOH", "emoji": "🛢",
     "base_price": 3400000,  "volatility": 0.07},
    {"id": "yandex",    "name": "Яндекс",       "ticker": "YDEX", "emoji": "🔴",
     "base_price": 1950000,  "volatility": 0.09},
    {"id": "norilsk",   "name": "Норникель",    "ticker": "GMKN", "emoji": "⚙️",
     "base_price": 7250000,  "volatility": 0.06},
    {"id": "rosneft",   "name": "Роснефть",     "ticker": "ROSN", "emoji": "🏭",
     "base_price": 265000,   "volatility": 0.07},
    {"id": "vk",        "name": "ВКонтакте",    "ticker": "VKCO", "emoji": "💙",
     "base_price": 210000,   "volatility": 0.11},
    {"id": "magnit",    "name": "Магнит",       "ticker": "MGNT", "emoji": "🛒",
     "base_price": 2800000,  "volatility": 0.06},
    {"id": "aeroflot",  "name": "Аэрофлот",     "ticker": "AFLT", "emoji": "✈️",
     "base_price": 34000,    "volatility": 0.10},
    {"id": "tinkoff",   "name": "Т-Банк",       "ticker": "TBNK", "emoji": "💳",
     "base_price": 1400000,  "volatility": 0.08},
]

STOCK_MAP = {s["id"]: s for s in STOCKS}
HISTORY_LEN = 24   # часов истории

# ── БД биржи ─────────────────────────────────────────────────────────────────
def load_exchange():
    if not os.path.exists(EXCHANGE_FILE):
        return _init_exchange()
    with open(EXCHANGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_exchange(ex):
    with open(EXCHANGE_FILE, "w", encoding="utf-8") as f:
        json.dump(ex, f, ensure_ascii=False, indent=2)

def _init_exchange():
    ex = {"prices": {}, "history": {}, "last_update": 0}
    for s in STOCKS:
        # небольшой разброс от базовой цены при старте
        price = s["base_price"] * random.uniform(0.92, 1.08)
        ex["prices"][s["id"]]  = round(price, 2)
        ex["history"][s["id"]] = [round(price, 2)]
    ex["last_update"] = time.time()
    save_exchange(ex)
    return ex

def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return {}
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_portfolio(pf):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=2)

def get_user_portfolio(uid):
    pf = load_portfolio()
    k  = str(uid)
    if k not in pf:
        pf[k] = {}  # {stock_id: {"qty": int, "avg_buy": float}}
    return pf[k]

# ── Обновление цен (раз в час) ────────────────────────────────────────────────
def _new_price(old_price: float, volatility: float, base: float) -> float:
    """
    Цена идёт случайным блужданием с небольшим притяжением к базе
    (mean-reversion), чтобы не уходила в ∞ или 0.
    """
    drift      = 0.002 * (base - old_price) / base   # возврат к базе ~0.2%
    shock      = random.gauss(0, volatility)
    new_price  = old_price * (1 + drift + shock)
    # Ограничиваем диапазон: от 20% до 500% базовой цены
    new_price  = max(base * 0.20, min(base * 5.0, new_price))
    return round(new_price, 2)

def update_prices():
    """Вызывается планировщиком раз в час."""
    ex = load_exchange()
    for s in STOCKS:
        sid  = s["id"]
        old  = ex["prices"][sid]
        new  = _new_price(old, s["volatility"], s["base_price"])
        ex["prices"][sid] = new
        hist = ex["history"].setdefault(sid, [])
        hist.append(new)
        if len(hist) > HISTORY_LEN:
            ex["history"][sid] = hist[-HISTORY_LEN:]
    ex["last_update"] = time.time()
    save_exchange(ex)

async def exchange_job(context: ContextTypes.DEFAULT_TYPE):
    """Job для PTB job_queue."""
    update_prices()

# ── Утилиты отображения ───────────────────────────────────────────────────────
def fmt(n):
    return f"{int(n):,}".replace(",", " ")

def fmt_price(p):
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    return f"{p:.2f}"

def price_arrow(history: list) -> str:
    if len(history) < 2:
        return "➡️"
    delta = history[-1] - history[-2]
    if delta > 0:   return "📈"
    if delta < 0:   return "📉"
    return "➡️"

def pct_change(history: list) -> str:
    if len(history) < 2:
        return "0.00%"
    old = history[-2]
    if old == 0:
        return "—"
    pct = (history[-1] - old) / old * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"

def mini_chart(history: list, width: int = 10) -> str:
    """ASCII spark-line из блоков."""
    h = history[-width:] if len(history) >= width else history
    if len(h) < 2:
        return "░" * width
    lo, hi = min(h), max(h)
    if hi == lo:
        return "▄" * len(h)
    bars = " ▁▂▃▄▅▆▇█"
    result = ""
    for v in h:
        idx = int((v - lo) / (hi - lo) * (len(bars) - 1))
        result += bars[idx]
    return result

def time_to_next_update(last_update: float) -> str:
    remaining = 3600 - (time.time() - last_update)
    if remaining <= 0:
        return "обновляется..."
    m = int(remaining // 60)
    s = int(remaining % 60)
    return f"{m}м {s}с"

# ── Тексты и клавиатуры ───────────────────────────────────────────────────────

def main_market_text(ex: dict) -> str:
    lines = [
        "```\n╔══════════════════════════╗\n║   📊  БИРЖА  INTER       ║\n╚══════════════════════════╝\n```\n",
        f"🕐 Обновление через: `{time_to_next_update(ex['last_update'])}`\n\n",
    ]
    for s in STOCKS:
        sid   = s["id"]
        hist  = ex["history"].get(sid, [ex["prices"][sid]])
        price = ex["prices"][sid]
        pct   = pct_change(hist)
        is_up = len(hist) < 2 or hist[-1] >= hist[-2]
        arrow = "▲" if is_up else "▼"
        lines.append(
            f"`{arrow} {s['ticker']:<4}` {s['emoji']}  "
            f"`{fmt_price(price):>10}`  "
            f"`{pct:>8}`\n"
        )
    return "".join(lines)

def main_market_kbd() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(STOCKS), 2):
        row = []
        for s in STOCKS[i:i+2]:
            row.append(InlineKeyboardButton(
                f"{s['emoji']} {s['ticker']}",
                callback_data=f"ex_stock_{s['id']}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("💼 Мой портфель", callback_data="ex_portfolio")])
    rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="ex_refresh")])
    return InlineKeyboardMarkup(rows)

def stock_detail_text(sid: str, ex: dict, uid: int) -> str:
    s     = STOCK_MAP[sid]
    hist  = ex["history"].get(sid, [ex["prices"][sid]])
    price = ex["prices"][sid]
    pct   = pct_change(hist)
    chart = mini_chart(hist, HISTORY_LEN)
    is_up = len(hist) < 2 or hist[-1] >= hist[-2]
    trend = "📈" if is_up else "📉"

    # позиция пользователя
    pf    = get_user_portfolio(uid)
    pos   = pf.get(sid)
    pos_text = ""
    if pos and pos["qty"] > 0:
        qty      = pos["qty"]
        avg_buy  = pos["avg_buy"]
        invested = avg_buy * qty
        current  = price * qty
        pnl      = current - invested
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_pct  = pnl / invested * 100 if invested else 0
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        pos_text = (
            f"\n💼 *Ваша позиция*\n"
            f"├ Кол-во:  `{fmt(qty)} шт`\n"
            f"├ Вложено: `{fmt(int(invested))} Inter`\n"
            f"├ Сейчас:  `{fmt(int(current))} Inter`\n"
            f"└ П/У: {pnl_icon} `{pnl_sign}{fmt(int(pnl))} Inter` (`{pnl_sign}{pnl_pct:.1f}%`)\n"
        )

    return (
        f"{s['emoji']} *{s['name']}* · `{s['ticker']}`\n\n"
        f"💰 Цена:  `{fmt_price(price)} Inter`\n"
        f"{trend} За час: `{pct}`\n\n"
        f"📈 *График ({len(hist)} ч)*\n"
        f"`{chart}`\n"
        f"{pos_text}"
    )

def stock_detail_kbd(sid: str, uid: int) -> InlineKeyboardMarkup:
    pf  = get_user_portfolio(uid)
    pos = pf.get(sid)
    qty = pos["qty"] if pos else 0

    buy_row  = [
        InlineKeyboardButton("Купить 1",   callback_data=f"ex_buy_{sid}_1"),
        InlineKeyboardButton("Купить 10",  callback_data=f"ex_buy_{sid}_10"),
        InlineKeyboardButton("Купить 100", callback_data=f"ex_buy_{sid}_100"),
    ]
    sell_row = []
    if qty > 0:
        sell_row = [
            InlineKeyboardButton("Продать 1",   callback_data=f"ex_sell_{sid}_1"),
            InlineKeyboardButton("Продать 10",  callback_data=f"ex_sell_{sid}_10"),
            InlineKeyboardButton("Продать все", callback_data=f"ex_sell_{sid}_all"),
        ]

    rows = [buy_row]
    if sell_row:
        rows.append(sell_row)
    rows.append([InlineKeyboardButton("‹ Назад к бирже", callback_data="ex_back")])
    return InlineKeyboardMarkup(rows)

def portfolio_text(uid: int, ex: dict, usr_balance: int) -> str:
    pf = get_user_portfolio(uid)
    if not pf or all(v["qty"] == 0 for v in pf.values()):
        return (
            "```\n╔══════════════════════════╗\n║   💼  МОЙ ПОРТФЕЛЬ       ║\n╚══════════════════════════╝\n```\n"
            "_Портфель пуст. Купи акции на бирже!_"
        )

    total_invested = 0
    total_current  = 0
    lines = [
        "```\n╔══════════════════════════╗\n║   💼  МОЙ ПОРТФЕЛЬ       ║\n╚══════════════════════════╝\n```\n"
    ]

    for s in STOCKS:
        sid = s["id"]
        pos = pf.get(sid)
        if not pos or pos["qty"] == 0:
            continue
        qty      = pos["qty"]
        avg_buy  = pos["avg_buy"]
        price    = ex["prices"][sid]
        invested = avg_buy * qty
        current  = price * qty
        pnl      = current - invested
        pnl_sign = "+" if pnl >= 0 else ""
        total_invested += invested
        total_current  += current
        lines.append(
            f"{s['emoji']} *{s['ticker']}* — `{fmt(qty)} шт`\n"
            f"   Ср. цена `{fmt_price(avg_buy)}` · сейчас `{fmt_price(price)}`\n"
            f"   П/У: `{pnl_sign}{fmt(int(pnl))} Inter`\n\n"
        )

    total_pnl  = total_current - total_invested
    total_sign = "+" if total_pnl >= 0 else ""
    total_pct  = total_pnl / total_invested * 100 if total_invested else 0
    lines.append(
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Вложено:  `{fmt(int(total_invested))} Inter`\n"
        f"💹 Текущая:  `{fmt(int(total_current))} Inter`\n"
        f"📊 Итог П/У: `{total_sign}{fmt(int(total_pnl))} Inter` ({total_sign}{total_pct:.1f}%)\n"
        f"💰 Баланс:   `{fmt(usr_balance)} Inter`"
    )
    return "".join(lines)

def portfolio_kbd() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("‹ К бирже", callback_data="ex_back")
    ]])

# ── Команда /биржа (через router) ─────────────────────────────────────────────
async def cmd_exchange(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ex = load_exchange()
    await update.message.reply_text(
        main_market_text(ex),
        parse_mode="Markdown",
        reply_markup=main_market_kbd()
    )

# ── Callback handler ──────────────────────────────────────────────────────────
async def cb_exchange(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Паттерны:
      ex_back
      ex_refresh
      ex_portfolio
      ex_stock_{sid}
      ex_buy_{sid}_{qty|all}
      ex_sell_{sid}_{qty|all}
    """
    q   = update.callback_query
    u   = q.from_user
    dat = q.data
    await q.answer()

    # Импортируем из основного модуля (bot.py) нужные функции.
    # Чтобы не создавать circular import — делаем ленивый импорт здесь.
    try:
        from bot import load_db, save_db, get_user
    except ImportError:
        await q.answer("Ошибка импорта bot.py", show_alert=True)
        return

    db  = load_db()
    usr = get_user(db, u.id, u.username or u.first_name)
    ex  = load_exchange()

    # ── Назад / Обновить ─────────────────────────────────────────────────────
    if dat in ("ex_back", "ex_refresh"):
        await q.edit_message_text(
            main_market_text(ex),
            parse_mode="Markdown",
            reply_markup=main_market_kbd()
        )
        return

    # ── Портфель ─────────────────────────────────────────────────────────────
    if dat == "ex_portfolio":
        await q.edit_message_text(
            portfolio_text(u.id, ex, usr["balance"]),
            parse_mode="Markdown",
            reply_markup=portfolio_kbd()
        )
        return

    # ── Карточка акции ────────────────────────────────────────────────────────
    if dat.startswith("ex_stock_"):
        sid = dat[9:]
        if sid not in STOCK_MAP:
            await q.answer("Неизвестная акция", show_alert=True)
            return
        await q.edit_message_text(
            stock_detail_text(sid, ex, u.id),
            parse_mode="Markdown",
            reply_markup=stock_detail_kbd(sid, u.id)
        )
        return

    # ── Покупка ───────────────────────────────────────────────────────────────
    if dat.startswith("ex_buy_"):
        parts = dat.split("_")   # ["ex", "buy", sid, qty]
        sid   = parts[2]
        qty_s = parts[3]

        if sid not in STOCK_MAP:
            await q.answer("Неизвестная акция", show_alert=True)
            return

        price = ex["prices"][sid]
        qty   = int(qty_s)
        cost  = round(price * qty)

        if usr["balance"] < cost:
            await q.answer(
                f"❌ Не хватает Inter!\nНужно: {fmt(cost)}\nЕсть: {fmt(usr['balance'])}",
                show_alert=True
            )
            return

        # Списываем деньги
        usr["balance"] -= cost
        save_db(db)

        # Обновляем портфель
        pf  = load_portfolio()
        k   = str(u.id)
        if k not in pf:
            pf[k] = {}
        pos = pf[k].get(sid, {"qty": 0, "avg_buy": 0.0})
        old_qty   = pos["qty"]
        old_avg   = pos["avg_buy"]
        new_qty   = old_qty + qty
        new_avg   = (old_avg * old_qty + price * qty) / new_qty
        pf[k][sid] = {"qty": new_qty, "avg_buy": round(new_avg, 4)}
        save_portfolio(pf)

        s = STOCK_MAP[sid]
        await q.answer(
            f"✅ Куплено {qty} шт. {s['ticker']}\n"
            f"Потрачено: {fmt(cost)} Inter\n"
            f"Баланс: {fmt(usr['balance'])} Inter",
            show_alert=True
        )
        # Обновляем карточку
        db_fresh  = load_db()
        usr_fresh = get_user(db_fresh, u.id, u.username or u.first_name)
        await q.edit_message_text(
            stock_detail_text(sid, ex, u.id),
            parse_mode="Markdown",
            reply_markup=stock_detail_kbd(sid, u.id)
        )
        return

    # ── Продажа ───────────────────────────────────────────────────────────────
    if dat.startswith("ex_sell_"):
        parts = dat.split("_")   # ["ex", "sell", sid, qty|all]
        sid   = parts[2]
        qty_s = parts[3]

        if sid not in STOCK_MAP:
            await q.answer("Неизвестная акция", show_alert=True)
            return

        pf  = load_portfolio()
        k   = str(u.id)
        pos = (pf.get(k) or {}).get(sid)
        if not pos or pos["qty"] == 0:
            await q.answer("У тебя нет этих акций!", show_alert=True)
            return

        qty = pos["qty"] if qty_s == "all" else int(qty_s)
        qty = min(qty, pos["qty"])

        price   = ex["prices"][sid]
        revenue = round(price * qty)

        # Зачисляем деньги
        from bot import MAX_BALANCE
        usr["balance"] = min(usr["balance"] + revenue, MAX_BALANCE)
        save_db(db)

        # Обновляем портфель
        new_qty = pos["qty"] - qty
        if new_qty == 0:
            del pf[k][sid]
        else:
            pf[k][sid]["qty"] = new_qty
        save_portfolio(pf)

        s = STOCK_MAP[sid]
        await q.answer(
            f"✅ Продано {qty} шт. {s['ticker']}\n"
            f"Получено: {fmt(revenue)} Inter\n"
            f"Баланс: {fmt(usr['balance'])} Inter",
            show_alert=True
        )
        await q.edit_message_text(
            stock_detail_text(sid, ex, u.id),
            parse_mode="Markdown",
            reply_markup=stock_detail_kbd(sid, u.id)
        )
        return
