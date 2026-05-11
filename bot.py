import os
import pandas as pd
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes


WORDS_FILE = "words.csv"

# --- Загрузка словаря ---
if not os.path.exists(WORDS_FILE):
    df = pd.DataFrame(columns=["كلمة", "слово", "learned", "last_review", "interval"])
    df.to_csv(WORDS_FILE, index=False, encoding="utf-8-sig")
else:
    df = pd.read_csv(WORDS_FILE, encoding="utf-8-sig")
    df["learned"] = df["learned"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["last_review"] = pd.to_datetime(df["last_review"], errors="coerce")

    if "interval" not in df.columns:
        df["interval"] = 1

    df["interval"] = pd.to_numeric(df["interval"], errors="coerce").fillna(1).astype(int)


# --- Проверка BOT_TOKEN ---
token = os.environ.get("BOT_TOKEN")

if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")


# --- Загрузка шрифта без падения ---
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
    greeting = (
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Священного Корана.\n\n"
        "Нажмите кнопку «Новое слово», чтобы начать обучение."
    )

    await update.message.reply_text(greeting, reply_markup=main_menu())


# --- Отправка нового слова ---
async def send_new_word(chat_id, bot):
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

    buttons = [
        [InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{word.name}")]
    ]

    if word["learned"]:
        buttons.append(
            [InlineKeyboardButton("💡 Помню", callback_data=f"remember_{word.name}")]
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
    await send_new_word(update.effective_chat.id, context.bot)


# --- Кнопки ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id
    today = datetime.now().replace(microsecond=0)

    if data.startswith("learned_") or data.startswith("remember_"):
        idx = int(data.split("_")[1])

        if data.startswith("learned_"):
            df.at[idx, "learned"] = True
            df.at[idx, "last_review"] = today
            df.at[idx, "interval"] = 1

        elif data.startswith("remember_"):
            old_interval = int(df.at[idx, "interval"])
            df.at[idx, "last_review"] = today
            df.at[idx, "interval"] = min(old_interval * 2, 30)

        df.to_csv(WORDS_FILE, index=False, encoding="utf-8-sig")

        try:
            await query.message.delete()
        except Exception:
            pass

        await send_new_word(chat_id, context.bot)

    elif data == "cmd_word":
        await send_new_word(chat_id, context.bot)

    elif data == "cmd_progress":
        learned = int(df["learned"].sum())
        total = len(df)
        remaining = total - learned
        percent = int((learned / total) * 100) if total > 0 else 0

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📊 Вы выучили {learned} слов из {total}\n\n"
                f"📖 Осталось: {remaining}\n"
                f"✅ Прогресс: {percent}%"
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
            text = "✅ Выученные слова:\n\n"

            for _, r in learned_words.iterrows():
                text += f"{r['слово']} — {r['كلمة']}\n"

            await context.bot.send_message(
                chat_id=chat_id,
                text=text[:4000]
            )

    elif data == "cmd_reset":
        df["learned"] = False
        df["last_review"] = pd.NaT
        df["interval"] = 1

        df.to_csv(WORDS_FILE, index=False, encoding="utf-8-sig")

        await context.bot.send_message(
            chat_id=chat_id,
            text="🔄 Все слова сброшены. Можно начать заново!"
        )


# --- Запуск ---
app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("word", daily_word))
app.add_handler(CallbackQueryHandler(button_handler))

app.run_polling()