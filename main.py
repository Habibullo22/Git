import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


# =======================
# SOZLAMALAR
# =======================
TOKEN = "8478058553:AAGR0eMotTJy5_zM-65bHGGsm2ImcOKKfeE"
ADMINS = {5815294733}  # <-- o'zingni Telegram ID

# Majburiy obuna (2 kanal + 1 guruh)
REQUIRED_CHATS = [
    "@your_channel_1",
    "@your_channel_2",
    "@your_group_or_channel",
]

DB_PATH = "kino.db"


# =======================
# DATABASE
# =======================
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    ref_by INTEGER,
    phone TEXT,
    joined_at INTEGER
);

CREATE TABLE IF NOT EXISTS movies (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    file_id TEXT NOT NULL,
    added_by INTEGER,
    added_at INTEGER
);

CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.execute("INSERT OR IGNORE INTO stats(key, value) VALUES('movies_count', 0)")
        await db.execute("INSERT OR IGNORE INTO stats(key, value) VALUES('users_count', 0)")
        await db.commit()

async def db_add_user(user_id: int, ref_by: Optional[int], joined_at: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        exists = await cur.fetchone()
        if exists:
            return False
        await db.execute(
            "INSERT INTO users(user_id, ref_by, joined_at) VALUES(?,?,?)",
            (user_id, ref_by, joined_at)
        )
        await db.execute("UPDATE stats SET value=value+1 WHERE key='users_count'")
        await db.commit()
        return True

async def db_set_phone(user_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
        await db.commit()

async def db_get_phone(user_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT phone FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None

async def db_add_movie(code: str, title: str, file_id: str, added_by: int, added_at: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO movies(code, title, file_id, added_by, added_at) VALUES(?,?,?,?,?)",
            (code, title, file_id, added_by, added_at)
        )
        await db.execute("UPDATE stats SET value=value+1 WHERE key='movies_count'")
        await db.commit()

async def db_get_movie(code: str) -> Optional[Tuple[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title, file_id FROM movies WHERE code=?", (code,))
        row = await cur.fetchone()
        return (row[0], row[1]) if row else None

async def db_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, value FROM stats")
        rows = await cur.fetchall()
        return {k: v for k, v in rows}

async def db_set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))
        await db.commit()

async def db_get_setting(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


# =======================
# OBUNA TEKSHIRISH
# =======================
async def is_subscribed(bot: Bot, user_id: int) -> bool:
    for chat in REQUIRED_CHATS:
        try:
            member = await bot.get_chat_member(chat_id=chat, user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True


# =======================
# KEYBOARDLAR
# =======================
def kb_check_sub():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tekshirish", callback_data="check_sub")
    return kb.as_markup()

def kb_user():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Kino olish")
    kb.button(text="📝 Kino so‘rash (zayavka)")
    kb.button(text="🔗 Referal")
    kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)

def kb_admin():
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Kino qo‘shish")
    kb.button(text="📢 Broadcast")
    kb.button(text="🧾 Zayavka kanal sozlash")
    kb.button(text="📊 Statistika")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)

def kb_request_contact():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📱 Kontakt ulashish", request_contact=True)
    return kb.as_markup(resize_keyboard=True, one_time_keyboard=True)

def kb_admin_request_buttons(req_id: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"req_ok:{req_id}")
    kb.button(text="❌ Rad etish", callback_data=f"req_no:{req_id}")
    kb.adjust(2)
    return kb.as_markup()


# =======================
# STATE (oddiy)
# =======================
@dataclass
class AdminAddMovieFlow:
    step: int = 0
    code: str = ""
    title: str = ""
    file_id: str = ""

ADMIN_FLOW = {}       # admin user_id -> AdminAddMovieFlow
ADMIN_MODE = {}       # admin user_id -> "set_request_channel" | None


# =======================
# BOT
# =======================
dp = Dispatcher()

def is_admin(uid: int) -> bool:
    return uid in ADMINS


@dp.message(CommandStart())
async def start(message: types.Message, bot: Bot):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    ref_by = None

    if len(args) > 1:
        m = re.match(r"ref_(\d+)", args[1].strip())
        if m:
            ref_by = int(m.group(1))
            if ref_by == user_id:
                ref_by = None

    await db_add_user(user_id=user_id, ref_by=ref_by, joined_at=int(message.date.timestamp()))

    # 1) obuna
    if not await is_subscribed(bot, user_id):
        await message.answer(
            "🚫 Botdan foydalanish uchun obuna bo‘ling:\n"
            + "\n".join([f"• {c}" for c in REQUIRED_CHATS]) +
            "\n\nObuna bo‘lgach ✅ Tekshirish bosing.",
            reply_markup=kb_check_sub()
        )
        return

    # 2) kontakt
    phone = await db_get_phone(user_id)
    if not phone:
        await message.answer(
            "📱 Davom etish uchun kontaktni ulashing (telefon raqam).",
            reply_markup=kb_request_contact()
        )
        return

    # 3) menyu (admin panel faqat admin!)
    if is_admin(user_id):
        await message.answer("Admin panel ✅", reply_markup=kb_admin())
    else:
        await message.answer("Xush kelibsiz ✅", reply_markup=kb_user())


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(call: types.CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    if not await is_subscribed(bot, user_id):
        await call.answer("Hali obuna emassiz ❌", show_alert=True)
        return

    phone = await db_get_phone(user_id)
    if not phone:
        await call.message.answer("✅ Obuna tasdiqlandi.\nEndi kontaktni ulashing:", reply_markup=kb_request_contact())
        await call.answer()
        return

    if is_admin(user_id):
        await call.message.answer("Admin panel ✅", reply_markup=kb_admin())
    else:
        await call.message.answer("Menyu ✅", reply_markup=kb_user())

    await call.answer()


@dp.message(F.contact)
async def got_contact(message: types.Message, bot: Bot):
    user_id = message.from_user.id

    if message.contact.user_id and message.contact.user_id != user_id:
        await message.answer("❌ Faqat o‘zingizning kontaktingizni ulashing.")
        return

    await db_set_phone(user_id, message.contact.phone_number)

    if not await is_subscribed(bot, user_id):
        await message.answer(
            "🚫 Avval obuna bo‘ling:\n"
            + "\n".join([f"• {c}" for c in REQUIRED_CHATS]) +
            "\n\n✅ Tekshirish bosing.",
            reply_markup=kb_check_sub()
        )
        return

    if is_admin(user_id):
        await message.answer("Kontakt qabul qilindi ✅\nAdmin panel:", reply_markup=kb_admin())
    else:
        await message.answer("Kontakt qabul qilindi ✅\nMenyu:", reply_markup=kb_user())


# =======================
# USER: REFERAL + KINO
# =======================
@dp.message(F.text == "🔗 Referal")
async def referral(message: types.Message):
    uid = message.from_user.id
    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{uid}"
    await message.answer(f"🔗 Referal linkingiz:\n{link}")

@dp.message(F.text == "🎬 Kino olish")
async def get_movie_prompt(message: types.Message):
    await message.answer("🎬 Kino kodini yuboring.\nMasalan: 102 yoki A12")

@dp.message(F.text)
async def movie_code_handler(message: types.Message):
    txt = message.text.strip()
    if txt in {"🎬 Kino olish", "📝 Kino so‘rash (zayavka)", "🔗 Referal"}:
        return
    if len(txt) > 25:
        return
    movie = await db_get_movie(txt)
    if not movie:
        return
    title, file_id = movie
    await message.answer_video(video=file_id, caption=f"🎬 {title}\n🔑 Kod: {txt}")


# =======================
# USER: ZAYAVKA (SO'ROV)
# =======================
@dp.message(F.text == "📝 Kino so‘rash (zayavka)")
async def request_movie(message: types.Message):
    await message.answer("📝 Qaysi kinoni so‘rayapsiz?\nNomini yozing (masalan: 'Spiderman 2')")

@dp.message(F.text.regexp(r"^So'rov:\s*"))
async def ignore_custom(message: types.Message):
    return

@dp.message(F.text)
async def handle_request_text(message: types.Message):
    # bu yerda user kino nomini yozadi (zayavka)
    if message.text in {"📝 Kino so‘rash (zayavka)", "🎬 Kino olish", "🔗 Referal"}:
        return
    if is_admin(message.from_user.id):
        return  # admin yozsa boshqa handlerlar bor

    text = message.text.strip()
    if len(text) < 2 or len(text) > 80:
        return

    req_channel = await db_get_setting("request_channel")
    if not req_channel:
        await message.answer("⚠️ Hozir zayavka kanali sozlanmagan. Keyinroq urinib ko‘ring.")
        return

    req_id = f"{message.from_user.id}_{int(message.date.timestamp())}"
    phone = await db_get_phone(message.from_user.id) or "yo‘q"
    u = message.from_user

    msg = (
        "🧾 Yangi kino zayavka!\n\n"
        f"👤 User: {u.full_name} (@{u.username})\n"
        f"🆔 ID: {u.id}\n"
        f"📞 Telefon: {phone}\n"
        f"🎬 So‘rov: {text}\n"
        f"🧩 ReqID: {req_id}"
    )

    try:
        await message.bot.send_message(
            chat_id=req_channel,
            text=msg,
            reply_markup=kb_admin_request_buttons(req_id)
        )
        await message.answer("✅ Zayavkangiz yuborildi. Admin ko‘rib chiqadi.")
    except Exception:
        await message.answer("❌ Zayavka yuborilmadi. Botni zayavka kanaliga admin qilib qo‘ying.")


@dp.callback_query(F.data.startswith("req_ok:"))
async def req_approve(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q ❌", show_alert=True)
        return
    req_id = call.data.split(":", 1)[1]
    user_id = int(req_id.split("_", 1)[0])
    await call.answer("Tasdiqlandi ✅")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply("✅ Admin: Tasdiqlandi")

    try:
        await call.bot.send_message(user_id, "✅ Zayavkangiz qabul qilindi. Kino tez orada qo‘shiladi.")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("req_no:"))
async def req_reject(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q ❌", show_alert=True)
        return
    req_id = call.data.split(":", 1)[1]
    user_id = int(req_id.split("_", 1)[0])
    await call.answer("Rad etildi ❌")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply("❌ Admin: Rad etildi")

    try:
        await call.bot.send_message(user_id, "❌ Zayavkangiz rad etildi (hozircha mavjud emas).")
    except Exception:
        pass


# =======================
# ADMIN PANEL (faqat admin)
# =======================
@dp.message(F.text == "📊 Statistika")
async def stats(message: types.Message):
    s = await db_stats()
    await message.answer(
        f"📊 Statistika:\n"
        f"👤 Userlar: {s.get('users_count', 0)}\n"
        f"🎬 Kinolar: {s.get('movies_count', 0)}"
    )

@dp.message(F.text == "➕ Kino qo‘shish")
async def admin_add_movie_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    ADMIN_FLOW[message.from_user.id] = AdminAddMovieFlow(step=1)
    await message.answer("➕ Kino qo‘shish:\n1/3) Kino kodini yuboring (masalan: 102 yoki A12).")

@dp.message(F.text == "📢 Broadcast")
async def admin_broadcast_hint(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("📢 Broadcast:\n`/bc Matn` ko‘rinishida yuboring.", parse_mode="Markdown")

@dp.message(F.text.regexp(r"^/bc\s+"))
async def admin_bc_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text[4:].strip()
    if not text:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        users = await cur.fetchall()

    ok, fail = 0, 0
    for (uid,) in users:
        try:
            await message.bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1

    await message.answer(f"✅ Yuborildi: {ok}\n❌ Xato: {fail}")

@dp.message(F.text == "🧾 Zayavka kanal sozlash")
async def set_request_channel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    ADMIN_MODE[message.from_user.id] = "set_request_channel"
    await message.answer(
        "🧾 Zayavka kanal sozlash:\n"
        "Kanal/guruh ID yoki @username yuboring.\n"
        "Masalan: `-1001234567890` yoki `@my_requests_channel`",
        parse_mode="Markdown"
    )

@dp.message()
async def admin_flow_handler(message: types.Message):
    uid = message.from_user.id
    if not is_admin(uid):
        return

    # admin mode: set request channel
    if ADMIN_MODE.get(uid) == "set_request_channel":
        val = message.text.strip()
        if not (val.startswith("@") or re.match(r"^-100\d{5,}$", val)):
            await message.answer("❌ Noto‘g‘ri format. @username yoki -100... yuboring.")
            return
        await db_set_setting("request_channel", val)
        ADMIN_MODE.pop(uid, None)
        await message.answer(f"✅ Zayavka kanali saqlandi: {val}")
        return

    # add movie flow
    if uid not in ADMIN_FLOW:
        return
    flow = ADMIN_FLOW[uid]

    if flow.step == 1:
        code = message.text.strip()
        if not re.match(r"^[A-Za-z0-9_-]{1,20}$", code):
            await message.answer("❌ Kod noto‘g‘ri. Faqat harf/son. Qayta yuboring.")
            return
        flow.code = code
        flow.step = 2
        ADMIN_FLOW[uid] = flow
        await message.answer("2/3) Kino nomini yuboring.")
        return

    if flow.step == 2:
        title = message.text.strip()
        if len(title) < 2:
            await message.answer("❌ Nom juda qisqa. Qayta yuboring.")
            return
        flow.title = title
        flow.step = 3
        ADMIN_FLOW[uid] = flow
        await message.answer("3/3) Endi kinoni video qilib yuboring (Telegram video).")
        return

    if flow.step == 3:
        if not message.video:
            await message.answer("❌ Video yuboring (Telegram video). Qayta urinib ko‘ring.")
            return
        flow.file_id = message.video.file_id
        await db_add_movie(
            code=flow.code,
            title=flow.title,
            file_id=flow.file_id,
            added_by=uid,
            added_at=int(message.date.timestamp())
        )
        ADMIN_FLOW.pop(uid, None)
        await message.answer(f"✅ Kino qo‘shildi!\n🔑 Kod: {flow.code}\n🎬 Nomi: {flow.title}")


async def main():
    logging.basicConfig(level=logging.INFO)
    await db_init()
    bot = Bot(TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
