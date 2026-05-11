import os
import shutil
import pandas as pd
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from matplotlib import font_manager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes


WORDS_FILE = "words.csv"
USERS_DIR = "users"
TEMPLATE_FILE = "card_template.png"

os.makedirs(USERS_DIR, exist_ok=True)

token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")


def get_user_words_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_words.csv")


def load_user_words(user_id):
    user_file = get_user_words_file(user_id)

    if not os.path.exists(WORDS_FILE):
        df = pd.DataFrame(columns=["كلمة", "слово", "learned", "last_review", "interval"])
        df.to_csv(WORDS_FILE, index=False, encoding="utf-8-sig")

    if not os.path.exists(user_file):
        shutil.copy(WORDS_FILE, user_file)

    df = pd.read_csv(user_file, encoding="utf-8-sig")

    for col, default in {
        "learned": False,
        "last_review": pd.NaT,
        "interval": 1,
    }.items():
        if col not in df.columns:
            df[col] = default

    df["learned"] = df["learned"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["last_review"] = pd.to_datetime(df["last_review"], errors="coerce")
    df["interval"] = pd.to_numeric(df["interval"], errors="coerce").fillna(1).astype(int)

    return df


def save_user_words(user_id, df):
    df.to_csv(get_user_words_file(user_id), index=False, encoding="utf-8-sig")


def fit_text(draw, text, max_width, start_size, min_size=40):
    font_path = font_manager.findfont("DejaVu Sans")
    size = start_size

    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]

        if text_width <= max_width:
            return font

        size -= 5

    return ImageFont.truetype(font_path, min_size)


def create_word_card(arabic_word, russian_word):
    if not os.path.exists(TEMPLATE_FILE):
        raise FileNotFoundError("Не найден файл card_template.png")

    img = Image.open(TEMPLATE_FILE).convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size

    arabic_word = get_display(arabic_reshaper.reshape(str(arabic_word)))
    russian_word = str(russian_word)

    arabic_font = fit_text(draw, arabic_word, max_width=850, start_size=175, min_size=90)
    russian_font = fit_text(draw, russian_word, max_width=700, start_size=78, min_size=45)

    arabic_y = int(height * 0.39)
    russian_y = int(height * 0.76)

    arabic_bbox = draw.textbbox((0, 0), arabic_word, font=arabic_font)
    arabic_width = arabic_bbox[2] - arabic_bbox[0]
    arabic_height = arabic_bbox[3] - arabic_bbox[1]

    draw.text(
        ((width - arabic_width) / 2, arabic_y - arabic_height / 2),
        arabic_word,
        font=arabic_font,
        fill="#064d36"
    )

    russian_bbox = draw.textbbox((0, 0), russian_word, font=russian_font)
    russian_width = russian_bbox[2] - russian_bbox[0]
    russian_height = russian_bbox[3] - russian_bbox[1]

    draw.text(
        ((width - russian_width) / 2, russian_y - russian_height / 2),
        russian_word,
        font=russian_font,
        fill="#064d36"
    )

    bio = BytesIO()
    bio.name = "word_card.png"
    img.save(bio, "PNG")
    bio.seek(0)

    return bio


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Новое слово", callback_data="cmd_word")],
        [InlineKeyboardButton("📊 Прогресс", callback_data="cmd_progress")],
        [InlineKeyboardButton("✅ Выученные", callback_data="cmd_learned")],
        [InlineKeyboardButton("🔄 Начать заново", callback_data="cmd_reset")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    load_user_words(user_id)

    await update.message.reply_text(
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Корана.\n\n"
        "Нажмите «Новое слово».",
        reply_markup=main_menu()
    )


async def send_new_word(user_id, chat_id, bot):
    df = load_user_words(user_id)
    today = datetime.now().replace(microsecond=0)

    due = df[
        (df["learned"]) &
        (df["last_review"] + pd.to_timedelta(df["interval"], unit="D") <= today)
    ]

    new_words = df[~df["learned"]]
    pool = pd.concat([due, new_words])

    if pool.empty:
        await bot.send_message(chat_id=chat_id, text="🎉 Все слова выучены!")
        return

    word = pool.sample(1).iloc[0]
    idx = word.name

    buttons = [[InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{idx}")]]
    if word["learned"]:
        buttons.append([InlineKeyboardButton("💡 Помню", callback_data=f"remember_{idx}")])

    card = create_word_card(word["كلمة"], word["слово"])

    await bot.send_photo(
        chat_id=chat_id,
        photo=card,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_new_word(
        update.effective_user.id,
        update.effective_chat.id,
        context.bot
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    today = datetime.now().replace(microsecond=0)

    df = load_user_words(user_id)

    if data.startswith("learned_") or data.startswith("remember_"):
        idx = int(data.split("_")[1])

        if idx not in df.index:
            await context.bot.send_message(chat_id=chat_id, text="Ошибка: слово не найдено.")
            return

        if data.startswith("learned_"):
            df.at[idx, "learned"] = True
            df.at[idx, "last_review"] = today
            df.at[idx, "interval"] = 1
        else:
            old_interval = int(df.at[idx, "interval"])
            df.at[idx, "last_review"] = today
            df.at[idx, "interval"] = min(old_interval * 2, 30)

        save_user_words(user_id, df)

        try:
            await query.message.delete()
        except Exception:
            pass

        await send_new_word(user_id, chat_id, context.bot)

    elif data == "cmd_word":
        await send_new_word(user_id, chat_id, context.bot)

    elif data == "cmd_progress":
        learned = int(df["learned"].sum())
        total = len(df)
        remaining = total - learned
        percent = int((learned / total) * 100) if total > 0 else 0

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📊 Ваш прогресс:\n\n"
                f"✅ Выучено: {learned} из {total}\n"
                f"📖 Осталось: {remaining}\n"
                f"📈 Прогресс: {percent}%"
            )
        )

    elif data == "cmd_learned":
        learned_words = df[df["learned"]]

        if learned_words.empty:
            await context.bot.send_message(chat_id=chat_id, text="Вы пока не выучили ни одного слова.")
        else:
            text = "✅ Ваши выученные слова:\n\n"
            for _, r in learned_words.iterrows():
                text += f"{r['слово']} — {r['كلمة']}\n"

            for i in range(0, len(text), 3500):
                await context.bot.send_message(chat_id=chat_id, text=text[i:i + 3500])

    elif data == "cmd_reset":
        df["learned"] = False
        df["last_review"] = pd.NaT
        df["interval"] = 1
        save_user_words(user_id, df)

        await context.bot.send_message(chat_id=chat_id, text="🔄 Прогресс сброшен.")


async def error_handler(update, context):
    print(f"Ошибка: {context.error}")


app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("word", daily_word))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_error_handler(error_handler)

app.run_polling()