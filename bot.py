import os
import shutil
import asyncio
import pandas as pd
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from matplotlib import font_manager
from google import genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

WORDS_FILE = "words.csv"
USERS_DIR = "users"
TEMPLATE_FILE = "card_template.PNG"

os.makedirs(USERS_DIR, exist_ok=True)

token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")

gemini_key = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_key) if gemini_key else None
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


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

        if bbox[2] - bbox[0] <= max_width:
            return font

        size -= 5

    return ImageFont.truetype(font_path, min_size)


def create_word_card(arabic_word, russian_word):
    if os.path.exists(TEMPLATE_FILE):
        img = Image.open(TEMPLATE_FILE).convert("RGB")
    else:
        img = Image.new("RGB", (1280, 720), "#f6ead7")

    draw = ImageDraw.Draw(img)
    width, height = img.size

    arabic_word = get_display(arabic_reshaper.reshape(str(arabic_word)))
    russian_word = str(russian_word)

    arabic_font = fit_text(draw, arabic_word, max_width=int(width * 0.70), start_size=175, min_size=90)
    russian_font = fit_text(draw, russian_word, max_width=int(width * 0.65), start_size=78, min_size=45)

    arabic_y = int(height * 0.39)
    russian_y = int(height * 0.76)

    arabic_bbox = draw.textbbox((0, 0), arabic_word, font=arabic_font)
    arabic_width = arabic_bbox[2] - arabic_bbox[0]
    arabic_height = arabic_bbox[3] - arabic_bbox[1]

    draw.text(
        ((width - arabic_width) / 2, arabic_y - arabic_height / 2),
        arabic_word,
        font=arabic_font,
        fill="#064d36",
    )

    russian_bbox = draw.textbbox((0, 0), russian_word, font=russian_font)
    russian_width = russian_bbox[2] - russian_bbox[0]
    russian_height = russian_bbox[3] - russian_bbox[1]

    draw.text(
        ((width - russian_width) / 2, russian_y - russian_height / 2),
        russian_word,
        font=russian_font,
        fill="#064d36",
    )

    bio = BytesIO()
    bio.name = "word_card.png"
    img.save(bio, "PNG")
    bio.seek(0)

    return bio


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Новое слово", callback_data="cmd_word")],
        [InlineKeyboardButton("🤖 Учитель", callback_data="cmd_teacher")],
        [InlineKeyboardButton("📊 Прогресс", callback_data="cmd_progress")],
        [InlineKeyboardButton("✅ Выученные", callback_data="cmd_learned")],
        [InlineKeyboardButton("🔄 Начать заново", callback_data="cmd_reset")],
    ])


def build_teacher_prompt(user_question, last_word=None):
    word_context = ""

    if last_word:
        word_context = (
            f"\nТекущее слово пользователя:\n"
            f"Арабский: {last_word.get('arabic')}\n"
            f"Перевод: {last_word.get('russian')}\n"
        )

    return (
        "Ты помощник по изучению коранического арабского языка. "
        "Отвечай на русском языке. Объясняй кратко, просто и понятно. "
        "Ты не муфтий и не даёшь фетвы. Не делай богословских постановлений. "
        "Фокусируйся только на языке: значение слова, корень, форма, перевод, запоминание. "
        "Ответ должен быть не длиннее 6-8 предложений. "
        "Если вопрос религиозно-правовой, мягко скажи обратиться к знающему человеку.\n"
        f"{word_context}\n"
        f"Вопрос пользователя: {user_question}"
    )


async def ask_ai(prompt):
    if not gemini_client:
        return (
            "🤖 ИИ-учитель пока не подключён.\n\n"
            "Нужно добавить GEMINI_API_KEY в переменные окружения."
        )

    def run():
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return response.text or "Не получилось получить ответ."

    try:
        return await asyncio.to_thread(run)

    except Exception as e:
        error_text = str(e)

        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            return (
                "🤖 Сейчас лимит бесплатных запросов Gemini исчерпан.\n\n"
                "Попробуй ещё раз позже. Обычно лимит обновляется автоматически.\n\n"
                "Можно также проверить лимиты в Google AI Studio: ai.dev/rate-limit"
            )

        return f"Ошибка ИИ: {e}"


async def send_long_message(bot, chat_id, text):
    for i in range(0, len(text), 3500):
        await bot.send_message(chat_id=chat_id, text=text[i:i + 3500])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    load_user_words(user_id)

    await update.message.reply_text(
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Корана.\n\n"
        "📖 Нажмите «Новое слово», чтобы учить слова.\n"
        "🤖 Нажмите «Учитель» или просто напишите вопрос в чат — ИИ поможет с арабским словом, корнем, переводом и запоминанием.",
        reply_markup=main_menu(),
    )


async def send_new_word(user_id, chat_id, bot, context=None):
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

    if context:
        context.user_data["last_word"] = {
            "arabic": str(word["كلمة"]),
            "russian": str(word["слово"]),
            "idx": int(idx),
        }

    buttons = [
        [InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{idx}")],
        [InlineKeyboardButton("🤖 Объяснить слово", callback_data=f"explain_{idx}")],
    ]

    if word["learned"]:
        buttons.append([InlineKeyboardButton("💡 Помню", callback_data=f"remember_{idx}")])

    card = create_word_card(word["كلمة"], word["слово"])

    await bot.send_photo(
        chat_id=chat_id,
        photo=card,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_new_word(
        update.effective_user.id,
        update.effective_chat.id,
        context.bot,
        context,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    today = datetime.now().replace(microsecond=0)

    df = load_user_words(user_id)

    if data.startswith("explain_"):
        idx = int(data.split("_")[1])

        if idx not in df.index:
            await context.bot.send_message(chat_id=chat_id, text="Ошибка: слово не найдено.")
            return

        word = df.loc[idx]

        context.user_data["last_word"] = {
            "arabic": str(word["كلمة"]),
            "russian": str(word["слово"]),
            "idx": int(idx),
        }

        await context.bot.send_message(chat_id=chat_id, text="🤖 Объясняю слово...")

        prompt = build_teacher_prompt(
            f"Объясни слово {word['كلمة']} — {word['слово']}. "
            "Дай значение, простое объяснение, возможный корень если знаешь, и короткую ассоциацию для запоминания.",
            context.user_data["last_word"],
        )

        answer = await ask_ai(prompt)
        await send_long_message(context.bot, chat_id, answer)

    elif data == "cmd_teacher":
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🤖 ИИ-учитель готов помочь.\n\n"
                "Ты можешь написать вопрос прямо в чат бота.\n\n"
                "Примеры:\n"
                "• Объясни слово رَحْمَةٌ\n"
                "• Какой корень у слова كِتَابٌ?\n"
                "• Как запомнить слово صَبْرٌ?\n"
                "• Чем отличаются نُورٌ и هُدًى?\n\n"
                "ИИ отвечает только как помощник по языку, не как муфтий."
            ),
        )

    elif data.startswith("learned_") or data.startswith("remember_"):
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

        await send_new_word(user_id, chat_id, context.bot, context)

    elif data == "cmd_word":
        await send_new_word(user_id, chat_id, context.bot, context)

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
            ),
        )

    elif data == "cmd_learned":
        learned_words = df[df["learned"]]

        if learned_words.empty:
            await context.bot.send_message(chat_id=chat_id, text="Вы пока не выучили ни одного слова.")
        else:
            text = "✅ Ваши выученные слова:\n\n"

            for _, r in learned_words.iterrows():
                text += f"{r['слово']} — {r['كلمة']}\n"

            await send_long_message(context.bot, chat_id, text)

    elif data == "cmd_reset":
        df["learned"] = False
        df["last_review"] = pd.NaT
        df["interval"] = 1

        save_user_words(user_id, df)

        await context.bot.send_message(chat_id=chat_id, text="🔄 Прогресс сброшен.")


async def ai_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()

    if not user_text:
        return

    last_word = context.user_data.get("last_word")

    await update.message.reply_text("🤖 Думаю над ответом...")

    prompt = build_teacher_prompt(user_text, last_word)
    answer = await ask_ai(prompt)

    await send_long_message(context.bot, update.effective_chat.id, answer)


async def error_handler(update, context):
    print(f"Ошибка: {context.error}")


app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("word", daily_word))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_text_handler))
app.add_error_handler(error_handler)

app.run_polling()