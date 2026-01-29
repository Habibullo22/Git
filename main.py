import asyncio
import logging
import os
import re
import subprocess
import uuid
import time

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import pyttsx3


# =======================
# SOZLAMALAR
# =======================
TOKEN = "8478058553:AAGR0eMotTJy5_zM-65bHGGsm2ImcOKKfeE"
ADMINS = {5815294733}

REQUIRED_CHATS = ["@bypass_bypasss"]  # 1ta kanal bo'lsin

DB_PATH = "media.db"


# =======================
# DB
# =======================
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  joined_at INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "tts_enabled": "1",
    "video_enabled": "1",
}

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))
        await db.commit()

async def db_add_user(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)", (uid, int(time.time())))
        await db.commit()

async def db_users_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        (cnt,) = await cur.fetchone()
        return int(cnt)

async def db_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return await cur.fetchall()

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else "0"

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))
        await db.commit()


# =======================
# YORDAMCHI
# =======================
def is_admin(uid: int) -> bool:
    return uid in ADMINS

def ensure_tmp():
    os.makedirs("tmp", exist_ok=True)

def clean_file(path: str):
    try:
        os.remove(path)
    except Exception:
        pass

async def is_subscribed(bot: Bot, user_id: int) -> bool:
    for chat in REQUIRED_CHATS:
        try:
            m = await bot.get_chat_member(chat_id=chat, user_id=user_id)
            if m.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True

def ffmpeg_extract_audio(in_path: str, out_path: str):
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-q:a", "2", out_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


# =======================
# TTS
# =======================
tts_engine = pyttsx3.init()
tts_engine.setProperty("rate", 175)

def tts_to_wav(text: str, out_path: str):
    tts_engine.save_to_file(text, out_path)
    tts_engine.runAndWait()


# =======================
# KEYBOARDS
# =======================
def kb_check():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tekshirish", callback_data="check_sub")
    return kb.as_markup()

def kb_user():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎥 Video → MP3")
    kb.button(text="📝 Matn → Ovoz")
    kb.button(text="ℹ️ Yordam")
    kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)

def kb_admin():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🛠 Admin panel")
    kb.button(text="ℹ️ Yordam")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def kb_admin_panel(tts_on: bool, video_on: bool):
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🔊 TTS: {'ON' if tts_on else 'OFF'}", callback_data="toggle_tts")
    kb.button(text=f"🎥 Video→MP3: {'ON' if video_on else 'OFF'}", callback_data="toggle_video")
    kb.button(text="👥 Statistika", callback_data="stats")
    kb.button(text="📢 Broadcast", callback_data="bc_help")
    kb.adjust(2, 2)
    return kb.as_markup()


# =======================
# DISPATCHER
# =======================
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: types.Message, bot: Bot):
    uid = message.from_user.id
    await db_add_user(uid)

    if not await is_subscribed(bot, uid):
        await message.answer(
            "🚫 Botdan foydalanish uchun kanalga obuna bo‘ling:\n"
            f"• {REQUIRED_CHATS[0]}\n\n"
            "Obuna bo‘lgach ✅ Tekshirish bosing.",
            reply_markup=kb_check()
        )
        return

    if is_admin(uid):
        await message.answer("✅ Admin menyu", reply_markup=kb_admin())
    else:
        await message.answer("✅ Menyu", reply_markup=kb_user())


@dp.callback_query(F.data == "check_sub")
async def check_sub(call: types.CallbackQuery, bot: Bot):
    uid = call.from_user.id
    if not await is_subscribed(bot, uid):
        await call.answer("Hali obuna emassiz ❌", show_alert=True)
        return

    if is_admin(uid):
        await call.message.answer("✅ Admin menyu", reply_markup=kb_admin())
    else:
        await call.message.answer("✅ Menyu", reply_markup=kb_user())
    await call.answer()


# ---------------- USER MENU ----------------
@dp.message(F.text == "ℹ️ Yordam")
async def help_msg(message: types.Message):
    if is_admin(message.from_user.id):
        await message.answer(
            "Admin:\n"
            "🛠 Admin panel — sozlamalar/statistika/broadcast\n"
            "Broadcast: /bc matn\n\n"
            "User:\n"
            "🎥 video yuborsa MP3 beradi\n"
            "📝 matn yuborsa ovoz beradi"
        )
    else:
        await message.answer("🎥 Video yuboring → MP3\n📝 Matn yuboring → ovoz (TTS)")

@dp.message(F.text == "🛠 Admin panel")
async def open_admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    tts_on = (await get_setting("tts_enabled")) == "1"
    video_on = (await get_setting("video_enabled")) == "1"
    await message.answer("🛠 Admin panel:", reply_markup=kb_admin_panel(tts_on, video_on))

@dp.callback_query(F.data.in_({"toggle_tts", "toggle_video", "stats", "bc_help"}))
async def admin_panel_actions(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q ❌", show_alert=True)
        return

    if call.data == "toggle_tts":
        cur = await get_setting("tts_enabled")
        await set_setting("tts_enabled", "0" if cur == "1" else "1")

    elif call.data == "toggle_video":
        cur = await get_setting("video_enabled")
        await set_setting("video_enabled", "0" if cur == "1" else "1")

    elif call.data == "stats":
        cnt = await db_users_count()
        await call.message.answer(f"👥 Userlar soni: {cnt}")

    elif call.data == "bc_help":
        await call.message.answer("📢 Broadcast qilish:\n`/bc Matn` deb yuboring.", parse_mode="Markdown")

    tts_on = (await get_setting("tts_enabled")) == "1"
    video_on = (await get_setting("video_enabled")) == "1"
    await call.message.edit_reply_markup(reply_markup=kb_admin_panel(tts_on, video_on))
    await call.answer("✅")


# ---------------- BROADCAST ----------------
@dp.message(F.text.regexp(r"^/bc\s+"))
async def bc_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text[4:].strip()

    users = await db_all_users()
    ok = fail = 0
    for (uid,) in users:
        try:
            await message.bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"✅ Yuborildi: {ok}\n❌ Xato: {fail}")


# ---------------- VIDEO -> MP3 ----------------
@dp.message(F.video)
async def handle_video(message: types.Message, bot: Bot):
    if (await get_setting("video_enabled")) != "1":
        await message.answer("🎥 Video→MP3 hozir OFF (admin o‘chirgan).")
        return

    ensure_tmp()
    uid = str(uuid.uuid4())
    in_file = f"tmp/{uid}.mp4"
    out_file = f"tmp/{uid}.mp3"

    await message.answer("⏳ Videodan audio ajratilyapti...")

    f = await bot.get_file(message.video.file_id)
    await bot.download_file(f.file_path, destination=in_file)

    ffmpeg_extract_audio(in_file, out_file)

    if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
        await message.answer_audio(types.FSInputFile(out_file), caption="✅ MP3 tayyor")
    else:
        await message.answer("❌ Audio ajratib bo‘lmadi. Video formatini tekshiring.")

    clean_file(in_file)
    clean_file(out_file)


# ---------------- TEXT -> TTS ----------------
@dp.message(F.text)
async def handle_text(message: types.Message):
    # menu tugmalari, admin panel va broadcast buyruqlarini chetlab o'tamiz
    if message.text in {"🎥 Video → MP3", "📝 Matn → Ovoz", "ℹ️ Yordam", "🛠 Admin panel"}:
        return
    if message.text.startswith("/"):
        return

    if (await get_setting("tts_enabled")) != "1":
        return  # tts o'chirilgan bo'lsa jim turadi

    txt = message.text.strip()
    if not txt:
        return

    # link bo'lsa hozircha TTS qilmaymiz (xohlasang linkni ham o'qib beradi)
    if re.search(r"https?://", txt):
        await message.answer("🔗 Link ko‘rdim. Videoni o‘zing yuborsang MP3 qilib beraman ✅")
        return

    if len(txt) > 500:
        await message.answer("✍️ Matn juda uzun. 500 belgigacha yubor.")
        return

    ensure_tmp()
    uid = str(uuid.uuid4())
    wav_path = f"tmp/{uid}.wav"
    mp3_path = f"tmp/{uid}.mp3"

    await message.answer("🔊 Matndan ovoz tayyorlanyapti...")

    tts_to_wav(txt, wav_path)
    ffmpeg_extract_audio(wav_path, mp3_path)

    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
        await message.answer_audio(types.FSInputFile(mp3_path), caption="✅ Ovoz tayyor (TTS)")
    else:
        await message.answer("❌ TTS xato. Serverda audio engine muammo bo‘lishi mumkin.")

    clean_file(wav_path)
    clean_file(mp3_path)


async def main():
    logging.basicConfig(level=logging.INFO)
    await db_init()
    bot = Bot(TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
