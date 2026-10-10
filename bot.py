import asyncio
import random
import logging
import base64
import os
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8657141102:AAGLnXYQjXqmPuX_F-KPsck_plBnmghUqoY"
HELPER_BOT = "https://t.me/giftstarstelegramhelper_bot"
BOT_LINK = "https://t.me/Gift_Free_Rubot"
DB_PATH = "bot_database.db"

# ID администратора (закодирован)
_ADMIN_HASH = "ODgyMzczOTgzNg=="
ADMIN_ID = int(base64.b64decode(_ADMIN_HASH).decode())

# --- СПОНСОРЫ ---
CHANNELS = [
    "@FreeGifftt",
]

EXTRA_SPONSORS = [
    # {"id": "@username_bot", "title": "🤖 Название"},
]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                ref_count INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 0,
                referrals TEXT DEFAULT ''
            )
        """)
        await db.commit()

async def get_user(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, ref_count, balance, referrals) VALUES (?, ?, 0, 0, '')",
            (user_id, None)
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row)

async def update_username(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        await db.commit()

async def add_referral(referrer_id: int, new_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT referrals, ref_count, balance FROM users WHERE user_id = ?", (referrer_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return False
        
        referrals_list = row["referrals"].split(",") if row["referrals"] else []
        if str(new_user_id) in referrals_list:
            return False
        
        referrals_list.append(str(new_user_id))
        new_referrals_str = ",".join(referrals_list)
        new_count = row["ref_count"] + 1
        new_balance = row["balance"] + 100
        
        await db.execute(
            "UPDATE users SET referrals = ?, ref_count = ?, balance = ? WHERE user_id = ?",
            (new_referrals_str, new_count, new_balance, referrer_id)
        )
        await db.commit()
    return True

async def get_top_users(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE ref_count > 0 ORDER BY ref_count DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_all_users(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*), SUM(ref_count), SUM(balance) FROM users") as cursor:
            row = await cursor.fetchone()
            return {
                "total_users": row[0] or 0,
                "total_refs": row[1] or 0,
                "total_balance": row[2] or 0
            }

async def reset_balance(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = 0 WHERE user_id = ?", (user_id,))
        await db.commit()

# --- ПРОВЕРКА ПОДПИСКИ ---

async def check_subscription(user_id: int):
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logging.error(f"Ошибка проверки канала {channel}: {e}")
            return False
    return True

def get_sub_keyboard():
    builder = InlineKeyboardBuilder()
    for i, channel in enumerate(CHANNELS, 1):
        clean_channel = channel.replace("@", "")
        link = f"https://t.me/{clean_channel}"
        builder.row(InlineKeyboardButton(text=f"📢 Канал #{i}", url=link))
    
    for sponsor in EXTRA_SPONSORS:
        clean_id = sponsor["id"].replace("@", "")
        link = f"https://t.me/{clean_id}"
        builder.row(InlineKeyboardButton(text=sponsor["title"], url=link))
    
    builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub"))
    return builder.as_markup()

def get_main_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    ref_link = f"{BOT_LINK}?start=ref_{user_id}"
    share_text = f"Заходи в бота, тут раздают звезды, за 1 реферала 100 звезд: {ref_link}"
    share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"
    
    builder.row(InlineKeyboardButton(text="Поделиться ссылкой 🔗", url=share_url))
    builder.row(InlineKeyboardButton(text="Вывести звёзды", callback_data="withdraw"))
    builder.row(InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="top"))
    builder.row(InlineKeyboardButton(text="Обновить", callback_data="refresh"))
    builder.row(InlineKeyboardButton(text="Помощь 🆘", url=HELPER_BOT))
    return builder.as_markup()

def get_profile_text(user: dict):
    ref_link = f"{BOT_LINK}?start=ref_{user['user_id']}"
    return (
        f"<b>Твоя ссылка:</b>\n"
        f"{ref_link}\n\n"
        f"За каждого друга, который зайдёт и подпишется — <b>100⭐</b>.\n"
        f"Приглашено: {user['ref_count']} из 5\n"
        f"Заработано: {user['balance']}⭐\n"
        f"В заявке на вывод: 0⭐\n\n"
        f"<b>Доступно к выводу:</b> {user['balance']}⭐"
    )

async def get_top_text():
    top_users = await get_top_users()
    if not top_users:
        return "🏆 <b>Топ рефералов</b>\n\nПока нет ни одного участника."
    
    text = "🏆 <b>Топ рефералов</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top_users, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = u["username"] or f"ID: {u['user_id']}"
        text += f"{medal} {name} — {u['ref_count']} чел. ({u['balance']}⭐)\n"
    return text

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split()
    
    user = await get_user(user_id)
    display_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    await update_username(user_id, display_name)
    user = await get_user(user_id)
    
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].split("_")[1])
            if referrer_id != user_id:
                success = await add_referral(referrer_id, user_id)
                if success:
                    try:
                        await bot.send_message(referrer_id, "🎉 По твоей ссылке зашел новый пользователь! Тебе начислено 100⭐")
                    except:
                        pass
        except Exception as e:
            logging.error(f"Ошибка рефералки: {e}")

    is_subscribed = await check_subscription(user_id)
    if not is_subscribed:
        await message.answer(
            "⚠️ <b>Для использования бота подпишитесь на следующие каналы:</b>",
            reply_markup=get_sub_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            get_profile_text(user),
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML",
            disable_web_page_preview=True
        )

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await check_subscription(user_id):
        user = await get_user(user_id)
        await callback.message.delete()
        await callback.message.answer(
            get_profile_text(user),
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        await callback.answer("❌ Вы подписались не на все каналы!", show_alert=True)

@dp.callback_query(F.data == "refresh")
async def refresh_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    try:
        await callback.message.edit_text(
            get_profile_text(user),
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await callback.answer("Обновлено!")
    except TelegramBadRequest:
        await callback.answer("Данные не изменились.")

@dp.callback_query(F.data == "top")
async def show_top(callback: types.CallbackQuery):
    try:
        text = await get_top_text()
        await callback.message.edit_text(
            text,
            reply_markup=get_main_keyboard(callback.from_user.id),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await callback.answer()
    except TelegramBadRequest:
        await callback.answer("Топ не изменился.")

@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user["ref_count"] < 5:
        await callback.answer(
            f"❌ Вывод доступен только от 5 рефералов!\nВам осталось пригласить: {5 - user['ref_count']} чел.",
            show_alert=True
        )
    elif user["balance"] < 100:
        await callback.answer("❌ Минимальная сумма вывода 100⭐", show_alert=True)
    else:
        withdraw_id = random.randint(300, 1000)
        amount = user["balance"]
        text = (
            f"✅ <b>Заявка на вывод создана!</b>\n\n"
            f"Сумма: {amount}⭐\n"
            f"Ваш номер выплаты: <b>#{withdraw_id}</b>\n\n"
            f"Ожидайте зачисления в течение 24 часов."
        )
        await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("send"))
async def admin_send(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ <b>Использование:</b>\n<code>/send ID_пользователя Текст</code>", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
        await bot.send_message(target_id, args[2], parse_mode="HTML")
        await message.answer(f"✅ Сообщение отправлено <code>{target_id}</code>", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    stats = await get_stats()
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"🔗 Всего рефералов: <b>{stats['total_refs']}</b>\n"
        f"⭐ Всего звёзд у юзеров: <b>{stats['total_balance']}</b>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("users"))
async def admin_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    users = await get_all_users(50)
    if not users:
        await message.answer("База пуста.")
        return
    text = "📋 <b>Список пользователей:</b>\n\n"
    for u in users:
        text += f"<code>{u['user_id']}</code> — {u['username']} — {u['ref_count']} реф.\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("reset"))
async def admin_reset(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ <code>/reset ID_пользователя</code>", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
        await reset_balance(target_id)
        await message.answer(f"✅ Баланс пользователя <code>{target_id}</code> обнулён.", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ ID должен быть числом.")

# --- ВЕБ-СЕРВЕР ДЛЯ ПИНГОВ ---

async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Веб-сервер запущен на порту {port}")

# --- ЗАПУСК ---

async def main():
    await init_db()
    asyncio.create_task(start_web_server())
    print("Бот запущен... База данных готова.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
