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
TOKEN = "8478058553:AAGR0eMotTJy5_zM-65bHGGsm2ImcOKKfeE"  # <-- yangi token qo'y
DB_PATH = "media.db"

# Agar majburiy kanal kerak bo'lmasa bo'sh qoldir:
REQUIRED_CHATS = []  # masalan: ["@bypass_bypasss"]


# =======================
# TTS engine
# =======================
tts_engine = pyttsx3.init()
tts_engine.setProperty("rate", 175)

def get_system_voices():
    voices = tts_engine.getProperty("voices") or []
    # har bir voice: id, name, languages, gender (har doim bo'lmaydi)
    result = []
    for v in voices:
        name = getattr(v, "name", "") or ""
        vid = getattr(v, "id", "") or ""
        # gender ko'p systemlarda yo'q bo'ladi
        gender = getattr(v, "gender", "") or ""
        result.append({"id": vid, "name": name, "gender": gender})
    return result

SYSTEM_VOICES = get_system_voices()

def guess_gender(name: str) -> str:
    n = name.lower()
    # juda sodda taxmin
    female_keys = ["female", "woman", "zira", "susan", "hazel", "aria", "eva", "anna", "irina"]
    male_keys = ["male", "man", "david", "mark", "alex", "john", "vladimir", "pavel"]
    if any(k in n for k in female_keys):
        return "female"
    if any(k in n for k in male_keys):
        return "male"
    return "unknown"

def pick_voice_id(prefer: str) -> str:
    """
    prefer: 'male' yoki 'female'
    Systemda mos voice topolmasa, birinchi voice qaytadi.
    """
    if not SYSTEM_VOICES:
        return ""

    # 1) gender/name bo'yicha urinamiz
    for v in SYSTEM_VOICES:
        g = guess_gender(v["name"])
        if prefer == "male" and g == "male":
            return v["id"]
        if prefer == "female" and g == "female":
            return v["id"]

    # 2) topilmasa birinchi voice
    return SYSTEM_VOICES[0]["id"]


def tts_to_wav(text: str, out_path: str, voice_id: str, rate: int):
    if voice_id:
        try:
            tts_engine.setProperty("voice", voice_id)
        except Exception:
            pass
    try:
        tts_engine.setProperty("rate", rate)
    except Exception:
        pass

    tts_engine.save_to_file(text, out_path)
    tts_engine.runAndWait()


# =======================
# FFmpeg
# =======================
def ensure_tmp():
    os.makedirs("tmp", exist_ok=True)

def clean_file(path: str):
    try:
        os.remove(path)
    except Exception:
        pass

def ffmpeg_extract_audio(in_path: str, out_path: str):
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-q:a", "2", out_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


# =======================
# DB
# =======================
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  joined_at INTEGER
);

CREATE TABLE IF NOT EXISTS user_voice (
  user_id INTEGER PRIMARY KEY,
  gender TEXT NOT NULL,
  age TEXT NOT NULL
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()

async def db_add_user(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)",
            (uid, int(time.time()))
        )
        await db.commit()

async def db_set_voice(uid: int, gender: str, age: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_voice(user_id, gender, age) VALUES(?,?,?)",
            (uid, gender, age)
        )
        await db.commit()

async def db_get_voice(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT gender, age FROM user_voice WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        return row  # (gender, age) or None


# =======================
# OBUNA (ixtiyoriy)
# =======================
async def is_subscribed(bot: Bot, user_id: int) -> bool:
    if not REQUIRED_CHATS:
        return True
    for chat in REQUIRED_CHATS:
        try:
            m = await bot.get_chat_member(chat_id=chat, user_id=user_id)
            if m.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True


# =======================
# KEYBOARDS
# =======================
def kb_gender():
    kb = InlineKeyboardBuilder()
    kb.button(text="👨 Erkak", callback_data="g:male")
    kb.button(text="👩 Ayol", callback_data="g:female")
    kb.adjust(2)
    return kb.as_markup()

def kb_age(gender: str):
    kb = InlineKeyboardBuilder()
    if gender == "male":
        kb.button(text="👦 Bola", callback_data="a:male:child")
        kb.button(text="👨 O‘rtacha", callback_data="a:male:adult")
        kb.button(text="👴 Katta", callback_data="a:male:old")
    else:
        kb.button(text="👧 Qizcha", callback_data="a:female:child")
        kb.button(text="👩 O‘rtacha", callback_data="a:female:adult")
        kb.button(text="👵 Katta", callback_data="a:female:old")
    kb.adjust(1, 1, 1)
    return kb.as_markup()

def kb_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎙 Ovoz tanlash")
    kb.button(text="ℹ️ Yordam")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def kb_check():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tekshirish", callback_data="check_sub")
    return kb.as_markup()


# =======================
# TTS PROFILES (rate)
# =======================
# yoshga qarab tezlikni ozgina o'zgartiramiz
RATE_BY_AGE = {
    "child": 195,  # tezroq
    "adult": 175,
    "old": 155,    # sekinroq
}


# =======================
# BOT
# =======================
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: types.Message, bot: Bot):
    uid = message.from_user.id
    await db_add_user(uid)

    if not await is_subscribed(bot, uid):
        await message.answer(
            "🚫 Botdan foydalanish uchun kanalga obuna bo‘ling.\n"
            "Obuna bo‘lgach ✅ Tekshirish bosing.",
            reply_markup=kb_check()
        )
        return

    current = await db_get_voice(uid)
    if not current:
        await message.answer(
            "🎙 Ovoz tanlaymiz.\nAvval jinsni tanla:",
            reply_markup=kb_gender()
        )
    else:
        await message.answer(
            "✅ Bot tayyor!\n"
            "Matn yozsang — tanlangan ovozda audio beradi.\n"
            "Video yuborsang — MP3 qiladi.\n\n"
            "Ovozni o‘zgartirish: 🎙 Ovoz tanlash",
            reply_markup=kb_menu()
        )


@dp.callback_query(F.data == "check_sub")
async def check_sub(call: types.CallbackQuery, bot: Bot):
    uid = call.from_user.id
    if not await is_subscribed(bot, uid):
        await call.answer("Hali obuna emassiz ❌", show_alert=True)
        return
    await call.answer("✅")


@dp.message(F.text == "🎙 Ovoz tanlash")
async def choose_voice(message: types.Message):
    await message.answer("Jinsni tanla:", reply_markup=kb_gender())


@dp.callback_query(F.data.startswith("g:"))
async def pick_gender(call: types.CallbackQuery):
    gender = call.data.split(":", 1)[1]
    await call.message.edit_text("Yosh kategoriyasini tanla:", reply_markup=kb_age(gender))
    await call.answer()


@dp.callback_query(F.data.startswith("a:"))
async def pick_age(call: types.CallbackQuery):
    # a:male:child
    _, gender, age = call.data.split(":")
    await db_set_voice(call.from_user.id, gender, age)
    await call.message.edit_text(
        f"✅ Tanlandi: {('Erkak' if gender=='male' else 'Ayol')} / {age}\n\n"
        "Endi matn yoz — audio qilib beraman.\nVideo yuborsang — MP3.",
    )
    await call.message.answer("Menyu ✅", reply_markup=kb_menu())

    # Agar systemda ovozlar kam bo'lsa ogohlantiramiz
    if len(SYSTEM_VOICES) <= 1:
        await call.message.answer(
            "ℹ️ Senda system voice juda kam ekan.\n"
            "Erkak/ayol farqi kuchli bo‘lmasligi mumkin.\n"
            "Xohlasang keyin real ovoz (Edge TTS) qilib beraman."
        )

    await call.answer("✅")


@dp.message(F.text == "ℹ️ Yordam")
async def help_msg(message: types.Message):
    await message.answer(
        "📌 Qanday ishlaydi:\n"
        "1) 🎙 Ovoz tanlash: Erkak/Ayol + yosh\n"
        "2) Matn yozsang → audio (TTS)\n"
        "3) Video yuborsang → MP3\n\n"
        "⚠️ TTS ovozlar systemga bog‘liq. Real ovoz kerak bo‘lsa ayt."
    )


# ---------------- VIDEO -> MP3 ----------------
@dp.message(F.video)
async def handle_video(message: types.Message, bot: Bot):
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
        await message.answer("❌ Audio ajratib bo‘lmadi. FFmpeg o‘rnatilganini tekshir.")

    clean_file(in_file)
    clean_file(out_file)


# ---------------- TEXT -> TTS ----------------
@dp.message(F.text)
async def handle_text(message: types.Message):
    # menu buyruqlarini chetlab o'tamiz
    if message.text in {"🎙 Ovoz tanlash", "ℹ️ Yordam"}:
        return
    if message.text.startswith("/"):
        return

    txt = message.text.strip()
    if not txt:
        return
    if len(txt) > 600:
        await message.answer("✍️ Matn juda uzun. 600 belgigacha yubor.")
        return

    # link bo'lsa xohlasang o'qib berishi mumkin, hozir o‘tib ketamiz
    if re.search(r"https?://", txt):
        await message.answer("🔗 Link yubording. Matn bo‘lsa o‘qib beraman, video bo‘lsa o‘zi tashla.")
        return

    # user ovoz tanlaganmi?
    pref = await db_get_voice(message.from_user.id)
    if not pref:
        await message.answer("Avval ovoz tanlang:", reply_markup=kb_gender())
        return

    gender, age = pref
    voice_id = pick_voice_id("male" if gender == "male" else "female")
    rate = RATE_BY_AGE.get(age, 175)

    ensure_tmp()
    uid = str(uuid.uuid4())
    wav_path = f"tmp/{uid}.wav"
    mp3_path = f"tmp/{uid}.mp3"

    await message.answer("🔊 Ovoz tayyorlanyapti...")

    # TTS -> wav
    tts_to_wav(txt, wav_path, voice_id=voice_id, rate=rate)
    # wav -> mp3
    ffmpeg_extract_audio(wav_path, mp3_path)

    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
        await message.answer_audio(types.FSInputFile(mp3_path), caption="✅ Ovoz tayyor")
    else:
        await message.answer("❌ TTS xato bo‘ldi. System voice muammo bo‘lishi mumkin.")

    clean_file(wav_path)
    clean_file(mp3_path)


async def main():
    logging.basicConfig(level=logging.INFO)
    await db_init()
    bot = Bot(TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
