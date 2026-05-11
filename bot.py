import os
import shutil
import pandas as pd
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes


WORDS_FILE = "words.csv"
USERS_DIR = "users"

os.makedirs(USERS_DIR, exist_ok=True)


# --- Проверка BOT_TOKEN ---
token = os.environ.get("BOT_TOKEN")

if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")


# --- Файл пользователя ---
def get_user_words_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_words.csv")


# --- Получить словарь конкретного пользователя ---
def load_user_words(user_id):
    user_file = get_user_words_file(user_id)

    if not os.path.exists(WORDS_FILE):
        df = pd.DataFrame(columns=["كلمة", "слово", "learned", "last_review", "interval"])
        df.to_csv(WORDS_FILE, index=False, encoding="utf-8-sig")

    if not os.path.exists(user_file):
        shutil.copy(WORDS_FILE, user_file)

    df = pd.read_csv(user_file, encoding="utf-8-sig")

    if "learned" not in df.columns:
        df["learned"] = False

    if "last_review" not in df.columns:
        df["last_review"] = pd.NaT

    if "interval" not in df.columns:
        df["interval"] = 1

    df["learned"] = df["learned"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["last_review"] = pd.to_datetime(df["last_review"], errors="coerce")
    df["interval"] = pd.to_numeric(df["interval"], errors="coerce").fillna(1).astype(int)

    return df


# --- Сохранить словарь пользователя ---
def save_user_words(user_id, df):
    user_file = get_user_words_file(user_id)
    df.to_csv(user_file, index=False, encoding="utf-8-sig")


# --- Загрузка шрифта ---
def load_font(size):
    font_paths = [
        "fonts/DejaVuSans.ttf",
        "fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue

    return ImageFont.load_default()


# --- Генерация картинки слова ---
def create_word_card(arabic_word, russian_word):
    width, height = 1280, 720

    arabic_word = str(arabic_word)
    russian_word = str(russian_word)

    img = Image.new("RGB", (width, height), "#f6ead7")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (70, 50, width - 70, height - 50),
        radius=55,
        outline="#c89b3c",
        width=8,
        fill="#fbf4e8"
    )

    draw.rounded_rectangle(
        (95, 75, width - 95, height - 75),
        radius=45,
        outline="#e6c98c",
        width=3
    )

    draw.line((410, 460, 595, 460), fill="#c89b3c", width=3)
    draw.line((685, 460, 870, 460), fill="#c89b3c", width=3)

    draw.polygon(
        [(640, 445), (655, 460), (640, 475), (625, 460)],
        fill="#c89b3c"
    )

    arabic_font = load_font(125)
    russian_font = load_font(68)

    arabic_bbox = draw.textbbox((0, 0), arabic_word, font=arabic_font)
    arabic_width = arabic_bbox[2] - arabic_bbox[0]

    draw.text(
        ((width - arabic_width) / 2, 205),
        arabic_word,
        font=arabic_font,
        fill="#064d36"
    )

    russian_bbox = draw.textbbox((0, 0), russian_word, font=russian_font)
    russian_width = russian_bbox[2] - russian_bbox[0]

    draw.text(
        ((width - russian_width) / 2, 500),
        russian_word,
        font=russian_font,
        fill="#064d36"
    )

    bio = BytesIO()
    bio.name = "word_card.png"
    img.save(bio, "PNG")
    bio.seek(0)

    return bio


# --- Главное меню ---
def main_menu():
    buttons = [
        [InlineKeyboardButton("📖 Новое слово", callback_data="cmd_word")],
        [InlineKeyboardButton("📊 Прогресс", callback_data="cmd_progress")],
        [InlineKeyboardButton("✅ Выученные", callback_data="cmd_learned")],
        [InlineKeyboardButton("🔄 Начать заново", callback_data="cmd_reset")],
    ]

    return InlineKeyboardMarkup(buttons)


# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    load_user_words(user_id)

    greeting = (
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Священного Корана.\n\n"
        "Нажмите кнопку «Новое слово», чтобы начать обучение."
    )

    await update.message.reply_text(greeting, reply_markup=main_menu())


# --- Отправка нового слова ---
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

    buttons = [
        [InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{idx}")]
    ]

    if word["learned"]:
        buttons.append(
            [InlineKeyboardButton("💡 Помню", callback_data=f"remember_{idx}")]
        )

    markup = InlineKeyboardMarkup(buttons)

    card = create_word_card(word["كلمة"], word["слово"])

    await bot.send_photo(
        chat_id=chat_id,
        photo=card,
        reply_markup=markup
    )


# --- /word ---
async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    await send_new_word(user_id, chat_id, context.bot)


# --- Кнопки ---
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

        elif data.startswith("remember_"):
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
            await context.bot.send_message(
                chat_id=chat_id,
                text="Вы пока не выучили ни одного слова."
            )
        else:
            text = "✅ Ваши выученные слова:\n\n"

            for _, r in learned_words.iterrows():
                text += f"{r['слово']} — {r['كلمة']}\n"

            for i in range(0, len(text), 3500):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text[i:i + 3500]
                )

    elif data == "cmd_reset":
        df["learned"] = False
        df["last_review"] = pd.NaT
        df["interval"] = 1

        save_user_words(user_id, df)

        await context.bot.send_message(
            chat_id=chat_id,
            text="🔄 Ваш прогресс сброшен. Можно начать заново!"
        )


# --- Обработчик ошибок ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Ошибка: {context.error}")


# --- Запуск ---
app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("word", daily_word))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_error_handler(error_handler)

app.run_polling()