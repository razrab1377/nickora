import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "market.db")

if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN environment variable before starting the bot.")

logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp = Dispatcher()

CURRENCIES = {"TON", "RUB"}
KINDS = {"Обычный Telegram", "Коллекционный Fragment"}
STATUSES = {"pending", "approved", "rejected", "sold", "hidden"}

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT
        )""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            handle TEXT NOT NULL,
            kind TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            message TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")

def upsert_user(user):
    with db() as con:
        con.execute("""
        INSERT INTO users(user_id, username, first_name, joined_at)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
        first_name=excluded.first_name
        """, (user.id, user.username or "", user.first_name or "",
              datetime.now(timezone.utc).isoformat()))

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔎 Каталог"), KeyboardButton(text="➕ Продать юзер")],
            [KeyboardButton(text="👤 Мои объявления"), KeyboardButton(text="📌 Как это работает")],
            [KeyboardButton(text="🆘 Поддержка")]
        ],
        resize_keyboard=True
    )

def listing_card(row):
    return (
        f"@{row['handle']}\\n"
        f"Тип: {row['kind']}\\n"
        f"Цена: {row['price']:g} {row['currency']}\\n"
        f"Продавец: ID {row['seller_id']}\\n"
        f"Описание: {row['description'] or 'не указано'}\\n"
        f"Номер объявления: #{row['id']}"
    )

def listing_actions(listing_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="Связаться / оставить заявку", callback_data=f"buy:{listing_id}")
    kb.button(text="Пожаловаться", callback_data=f"report:{listing_id}")
    kb.adjust(1)
    return kb.as_markup()

@dp.message(CommandStart())
async def start(message: Message):
    upsert_user(message.from_user)
    await message.answer(
        "Добро пожаловать в Username Market. Здесь можно размещать и искать "
        "обычные Telegram-юзернеймы и коллекционные имена Fragment.\\n\\n"
        "Важно: бот — доска объявлений, он не проверяет право собственности, "
        "не принимает оплату и не выступает гарантом сделки. Проверяйте передачу "
        "имени самостоятельно и не отправляйте деньги незнакомцам заранее.",
        reply_markup=main_menu()
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Команды:\\n/start — меню\\n/catalog — каталог\\n/my — мои объявления\\n"
        "/cancel — отменить текущий ввод\\n/admin — модерация (для администраторов)"
    )

@dp.message(Command("cancel"))
async def cancel(message: Message):
    await message.answer("Действие отменено.", reply_markup=main_menu())

@dp.message(F.text == "📌 Как это работает")
async def how(message: Message):
    await message.answer(
        "1. Продавец добавляет юзернейм, тип, цену и описание.\\n"
        "2. Объявление попадает на проверку администратору.\\n"
        "3. После одобрения оно появляется в каталоге.\\n"
        "4. Покупатель оставляет заявку, а стороны обсуждают сделку напрямую.\\n\\n"
        "TON и рубли указываются только как валюта цены. Бот не принимает и "
        "не хранит платежи. Для коллекционных имён сверяйте статус и передачу "
        "на официальном Fragment; для обычных имён проверяйте передачу в Telegram."
    )

@dp.message(F.text == "🆘 Поддержка")
async def support(message: Message):
    await message.answer(
        "По вопросам модерации укажите номер объявления и подробно опишите проблему. "
        "Администратор: " + (", ".join(map(str, sorted(ADMIN_IDS))) if ADMIN_IDS else "не настроен")
    )

# Simple finite-state flow stored in memory for this starter version.
user_states = {}

@dp.message(F.text == "➕ Продать юзер")
async def add_listing_start(message: Message):
    user_states[message.from_user.id] = {"step": "handle"}
    await message.answer("Пришли юзернейм без @ (например, darkatt). Не отправляй пароль или код входа.")

@dp.message(F.text == "🔎 Каталог")
@dp.message(Command("catalog"))
async def catalog(message: Message):
    with db() as con:
        rows = con.execute(
            "SELECT * FROM listings WHERE status='approved' ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await message.answer("Пока нет одобренных объявлений.")
        return
    for row in rows:
        await message.answer(listing_card(row), reply_markup=listing_actions(row["id"]))

@dp.message(F.text == "👤 Мои объявления")
@dp.message(Command("my"))
async def my_listings(message: Message):
    with db() as con:
        rows = con.execute(
            "SELECT * FROM listings WHERE seller_id=? ORDER BY id DESC",
            (message.from_user.id,)
        ).fetchall()
    if not rows:
        await message.answer("У тебя пока нет объявлений.")
        return
    for row in rows:
        await message.answer(
            listing_card(row) + f"\\nСтатус: {row['status']}"
        )

@dp.message(F.text)
async def listing_wizard(message: Message):
    uid = message.from_user.id
    state = user_states.get(uid)
    if not state:
        return
    value = message.text.strip()

    if state["step"] == "handle":
        handle = value.lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", handle):
            await message.answer("Формат не подходит. Используй 5–32 символа: латинские буквы, цифры и _.")
            return
        state["handle"] = handle
        state["step"] = "kind"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Обычный Telegram", callback_data="kind:ordinary")],
            [InlineKeyboardButton(text="Коллекционный Fragment", callback_data="kind:fragment")]
        ])
        await message.answer("Выбери тип юзернейма:", reply_markup=kb)
        return

    if state["step"] == "price":
        try:
            price = float(value.replace(",", "."))
            if price <= 0 or price > 1_000_000_000:
                raise ValueError()
        except ValueError:
            await message.answer("Введи положительную цену числом.")
            return
        state["price"] = price
        state["step"] = "currency"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="TON", callback_data="currency:TON"),
             InlineKeyboardButton(text="RUB", callback_data="currency:RUB")]
        ])
        await message.answer("Выбери валюту:", reply_markup=kb)
        return

    if state["step"] == "description":
        state["description"] = "" if value == "-" else value[:500]
        data = state.copy()
        with db() as con:
            cur = con.execute("""
                INSERT INTO listings(seller_id,handle,kind,price,currency,description,status,created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (uid, data["handle"], data["kind"], data["price"],
                  data["currency"], data["description"], "pending",
                  datetime.now(timezone.utc).isoformat()))
            listing_id = cur.lastrowid
        user_states.pop(uid, None)
        await message.answer(f"Объявление #{listing_id} отправлено на модерацию.", reply_markup=main_menu())
        for admin_id in ADMIN_IDS:
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Одобрить", callback_data=f"mod:approve:{listing_id}"),
                    InlineKeyboardButton(text="Отклонить", callback_data=f"mod:reject:{listing_id}")
                ]])
                await bot.send_message(admin_id, f"Новое объявление:\\n{listing_card({**data, 'id': listing_id, 'seller_id': uid})}", reply_markup=kb)
            except Exception:
                logging.exception("Could not notify admin")

@dp.callback_query(F.data.startswith("kind:"))
async def choose_kind(call: CallbackQuery):
    state = user_states.get(call.from_user.id)
    if not state:
        await call.answer("Сессия истекла. Начни заново.", show_alert=True)
        return
    state["kind"] = "Обычный Telegram" if call.data.endswith("ordinary") else "Коллекционный Fragment"
    state["step"] = "price"
    await call.message.answer("Укажи цену числом, например 25.")
    await call.answer()

@dp.callback_query(F.data.startswith("currency:"))
async def choose_currency(call: CallbackQuery):
    state = user_states.get(call.from_user.id)
    if not state:
        await call.answer("Сессия истекла. Начни заново.", show_alert=True)
        return
    state["currency"] = call.data.split(":")[1]
    state["step"] = "description"
    await call.message.answer("Добавь описание (или отправь «-», если без описания).")
    await call.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy_request(call: CallbackQuery):
    listing_id = int(call.data.split(":")[1])
    with db() as con:
        row = con.execute("SELECT * FROM listings WHERE id=? AND status='approved'", (listing_id,)).fetchone()
        if not row:
            await call.answer("Объявление недоступно.", show_alert=True)
            return
        if row["seller_id"] == call.from_user.id:
            await call.answer("Это твоё объявление.", show_alert=True)
            return
        con.execute(
            "INSERT INTO requests(listing_id,buyer_id,message,created_at) VALUES(?,?,?,?)",
            (listing_id, call.from_user.id, "", datetime.now(timezone.utc).isoformat())
        )
    buyer = call.from_user.username or str(call.from_user.id)
    try:
        await bot.send_message(
            row["seller_id"],
            f"Новая заявка на @{row['handle']} от @{buyer} (ID {call.from_user.id}). "
            f"Ответь покупателю самостоятельно через Telegram."
        )
    except Exception:
        pass
    await call.answer("Заявка отправлена продавцу.", show_alert=True)

@dp.callback_query(F.data.startswith("report:"))
async def report_listing(call: CallbackQuery):
    listing_id = int(call.data.split(":")[1])
    with db() as con:
        con.execute(
            "INSERT INTO reports(listing_id,reporter_id,reason,created_at) VALUES(?,?,?,?)",
            (listing_id, call.from_user.id, "Пожаловаться на объявление", datetime.now(timezone.utc).isoformat())
        )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"Жалоба на объявление #{listing_id}, пользователь ID {call.from_user.id}.")
        except Exception:
            pass
    await call.answer("Жалоба отправлена модераторам.", show_alert=True)

@dp.callback_query(F.data.startswith("mod:"))
async def moderate(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Нет доступа.", show_alert=True)
        return
    _, action, raw_id = call.data.split(":")
    listing_id = int(raw_id)
    status = "approved" if action == "approve" else "rejected"
    with db() as con:
        row = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not row:
            await call.answer("Объявление не найдено.", show_alert=True)
            return
        con.execute("UPDATE listings SET status=? WHERE id=?", (status, listing_id))
    try:
        await bot.send_message(row["seller_id"], f"Объявление #{listing_id}: {status}.")
    except Exception:
        pass
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Готово.")

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return
    with db() as con:
        pending = con.execute("SELECT COUNT(*) n FROM listings WHERE status='pending'").fetchone()["n"]
        reports = con.execute("SELECT COUNT(*) n FROM reports").fetchone()["n"]
    await message.answer(f"Админ-панель\\nОжидают проверки: {pending}\\nЖалоб всего: {reports}")

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
