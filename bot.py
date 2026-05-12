import os
import json
import shutil
import random
import asyncio
import pandas as pd
from datetime import datetime, time
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
REMINDERS_FILE = "reminders.json"

os.makedirs(USERS_DIR, exist_ok=True)

token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")

gemini_key = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_key) if gemini_key else None
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


def get_user_words_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_words.csv")


def get_user_settings_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_settings.json")


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_user_settings(user_id):
    return load_json_file(get_user_settings_file(user_id), {"level": "all"})


def save_user_settings(user_id, settings):
    save_json_file(get_user_settings_file(user_id), settings)


def get_level_column(df):
    for col in ["раздел", "тема", "сура", "level"]:
        if col in df.columns:
            return col
    return None


def get_frequency_column(df):
    for col in ["частота", "frequency", "freq", "count", "количество"]:
        if col in df.columns:
            return col
    return None


def sort_by_quran_frequency(df):
    freq_col = get_frequency_column(df)

    if freq_col:
        temp = df.copy()
        temp[freq_col] = pd.to_numeric(temp[freq_col], errors="coerce").fillna(0)
        return temp.sort_values(by=freq_col, ascending=False)

    return df.sort_index()


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

    if get_level_column(df) is None:
        df["раздел"] = "Общий словарь"

    df["learned"] = df["learned"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["last_review"] = pd.to_datetime(df["last_review"], errors="coerce")
    df["interval"] = pd.to_numeric(df["interval"], errors="coerce").fillna(1).astype(int)

    return df


def save_user_words(user_id, df):
    df.to_csv(get_user_words_file(user_id), index=False, encoding="utf-8-sig")


def get_filtered_df(user_id):
    df = load_user_words(user_id)
    settings = load_user_settings(user_id)
    selected_level = settings.get("level", "all")
    level_col = get_level_column(df)

    if selected_level != "all" and level_col:
        df = df[df[level_col].astype(str) == str(selected_level)]

    return df


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


def draw_centered_text(draw, text, y, font, width, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    draw.text(
        ((width - text_width) / 2, y - text_height / 2),
        text,
        font=font,
        fill=fill,
    )


def create_word_card(arabic_word, russian_word=None):
    if os.path.exists(TEMPLATE_FILE):
        img = Image.open(TEMPLATE_FILE).convert("RGB")
    else:
        img = Image.new("RGB", (1280, 720), "#f6ead7")

    draw = ImageDraw.Draw(img)
    width, height = img.size

    arabic_word = get_display(arabic_reshaper.reshape(str(arabic_word)))

    arabic_font = fit_text(draw, arabic_word, max_width=int(width * 0.70), start_size=175, min_size=90)
    draw_centered_text(draw, arabic_word, int(height * 0.39), arabic_font, width, "#064d36")

    if russian_word is not None:
        russian_word = str(russian_word)
        russian_font = fit_text(draw, russian_word, max_width=int(width * 0.65), start_size=78, min_size=45)
        draw_centered_text(draw, russian_word, int(height * 0.76), russian_font, width, "#064d36")

    bio = BytesIO()
    bio.name = "word_card.png"
    img.save(bio, "PNG")
    bio.seek(0)

    return bio


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Новое слово", callback_data="cmd_word")],
        [InlineKeyboardButton("📋 Проверка", callback_data="cmd_quiz")],
        [InlineKeyboardButton("📚 Разделы", callback_data="cmd_levels")],
        [InlineKeyboardButton("🤖 Учитель", callback_data="cmd_teacher")],
        [InlineKeyboardButton("🔔 Напоминания", callback_data="cmd_reminders")],
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
        "Всегда начинай ответ словами: Ассаламу алейкум! "
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
                "Попробуй ещё раз позже."
            )

        return f"Ошибка ИИ: {e}"


async def send_long_message(bot, chat_id, text):
    for i in range(0, len(text), 3500):
        await bot.send_message(chat_id=chat_id, text=text[i:i + 3500])


def load_reminders():
    return load_json_file(REMINDERS_FILE, {})


def save_reminders(data):
    save_json_file(REMINDERS_FILE, data)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    reminders = load_reminders()

    for user_id, chat_id in reminders.items():
        try:
            df = load_user_words(int(user_id))
            learned = int(df["learned"].sum())
            total = len(df)
            remaining = total - learned

            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    "🔔 Напоминание на сегодня\n\n"
                    "Пора повторить слова Корана 📖\n"
                    f"✅ Выучено: {learned} из {total}\n"
                    f"📖 Осталось: {remaining}\n\n"
                    "Нажми /word или кнопку «Новое слово»."
                )
            )
        except Exception as e:
            print(f"Ошибка напоминания для {user_id}: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    load_user_words(user_id)

    await update.message.reply_text(
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Корана.\n\n"
        "📖 «Новое слово» — изучение слов по частоте употребления в Коране.\n"
        "📋 «Проверка» — тест по уже выученным словам.\n"
        "📚 «Разделы» — выбор уровня или темы.\n"
        "🔔 «Напоминания» — ежедневное напоминание.\n"
        "🤖 Можно написать вопрос в чат — ИИ-учитель ответит по арабскому языку.",
        reply_markup=main_menu(),
    )


async def send_new_word(user_id, chat_id, bot, context=None):
    df = get_filtered_df(user_id)
    today = datetime.now().replace(microsecond=0)

    due = df[
        (df["learned"]) &
        (df["last_review"] + pd.to_timedelta(df["interval"], unit="D") <= today)
    ]

    new_words = df[~df["learned"]]

    due = sort_by_quran_frequency(due)
    new_words = sort_by_quran_frequency(new_words)

    pool = pd.concat([due, new_words])

    if pool.empty:
        await bot.send_message(chat_id=chat_id, text="🎉 В этом разделе все слова на сегодня пройдены!")
        return

    word = pool.iloc[0]
    idx = word.name

    if context:
        context.user_data["last_word"] = {
            "arabic": str(word["كلمة"]),
            "russian": str(word["слово"]),
            "idx": int(idx),
        }

    buttons = [
        [InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{idx}")],
        [InlineKeyboardButton("❌ Не помню", callback_data=f"forgot_{idx}")],
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


async def send_quiz(user_id, chat_id, bot, context):
    df = get_filtered_df(user_id)
    learned_words = df[df["learned"]]

    if learned_words.empty:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "📋 Проверка осуществляется только по уже выученным словам.\n\n"
                "Сначала выучи хотя бы одно слово, затем возвращайся к проверке."
            )
        )
        return

    learned_words = sort_by_quran_frequency(learned_words)
    question = learned_words.sample(1).iloc[0]

    correct_idx = question.name
    correct_answer = str(question["слово"])

    all_wrong = df[df.index != correct_idx]["слово"].dropna().astype(str).unique().tolist()
    wrong_answers = random.sample(all_wrong, min(3, len(all_wrong)))

    options = wrong_answers + [correct_answer]
    random.shuffle(options)

    context.user_data["quiz_answer"] = {
        "correct": correct_answer,
        "arabic": str(question["كلمة"]),
        "idx": int(correct_idx),
    }

    buttons = []
    for option in options:
        buttons.append([InlineKeyboardButton(option, callback_data=f"quiz_answer_{option}")])

    card = create_word_card(question["كلمة"], None)

    await bot.send_photo(
        chat_id=chat_id,
        photo=card,
        caption="📋 Выбери правильный перевод:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_levels(user_id, chat_id, bot):
    df = load_user_words(user_id)
    level_col = get_level_column(df)
    settings = load_user_settings(user_id)
    current = settings.get("level", "all")

    levels = sorted(df[level_col].dropna().astype(str).unique().tolist()) if level_col else ["Общий словарь"]

    buttons = [[InlineKeyboardButton("🌍 Все слова", callback_data="level_all")]]

    for level in levels[:50]:
        title = f"✅ {level}" if current == level else str(level)
        buttons.append([InlineKeyboardButton(title, callback_data=f"level_{level}")])

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "📚 Выбери раздел для изучения.\n\n"
            "Если в words.csv нет отдельной колонки «раздел», бот использует общий словарь."
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    today = datetime.now().replace(microsecond=0)

    df = load_user_words(user_id)

    if data == "cmd_quiz":
        await send_quiz(user_id, chat_id, context.bot, context)

    elif data.startswith("quiz_answer_"):
        selected = data.replace("quiz_answer_", "", 1)
        quiz = context.user_data.get("quiz_answer")

        if not quiz:
            await context.bot.send_message(chat_id=chat_id, text="Тест устарел. Нажми «Проверка» ещё раз.")
            return

        if selected == quiz["correct"]:
            await context.bot.send_message(chat_id=chat_id, text="✅ Верно!")
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Неверно.\n\nПравильный ответ: {quiz['arabic']} — {quiz['correct']}"
            )

        await send_quiz(user_id, chat_id, context.bot, context)

    elif data == "cmd_levels":
        await show_levels(user_id, chat_id, context.bot)

    elif data.startswith("level_"):
        level = data.replace("level_", "", 1)
        settings = load_user_settings(user_id)
        settings["level"] = "all" if level == "all" else level
        save_user_settings(user_id, settings)

        text = "🌍 Выбран режим: все слова" if level == "all" else f"📚 Выбран раздел: {level}"
        await context.bot.send_message(chat_id=chat_id, text=text)

    elif data == "cmd_reminders":
        reminders = load_reminders()
        enabled = str(user_id) in reminders

        if enabled:
            buttons = [[InlineKeyboardButton("🔕 Выключить напоминания", callback_data="reminder_off")]]
            text = "🔔 Ежедневные напоминания уже включены."
        else:
            buttons = [[InlineKeyboardButton("🔔 Включить напоминания", callback_data="reminder_on")]]
            text = "🔔 Можно включить ежедневное напоминание о повторении слов."

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "reminder_on":
        reminders = load_reminders()
        reminders[str(user_id)] = chat_id
        save_reminders(reminders)

        await context.bot.send_message(
            chat_id=chat_id,
            text="🔔 Напоминания включены. Бот будет писать тебе каждый день."
        )

    elif data == "reminder_off":
        reminders = load_reminders()
        reminders.pop(str(user_id), None)
        save_reminders(reminders)

        await context.bot.send_message(chat_id=chat_id, text="🔕 Напоминания выключены.")

    elif data.startswith("explain_"):
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
                "Ассаламу алейкум! 🤖\n\n"
                "Я ИИ-учитель по кораническому арабскому языку.\n\n"
                "Ты можешь написать вопрос прямо в чат бота.\n\n"
                "Примеры:\n"
                "• Объясни слово رَحْمَةٌ\n"
                "• Какой корень у слова كِتَابٌ?\n"
                "• Как запомнить слово صَبْرٌ?\n"
                "• Чем отличаются نُورٌ и هُدًى?\n\n"
                "Я отвечаю только как помощник по языку, не как муфтий."
            ),
        )

    elif data.startswith("learned_") or data.startswith("remember_") or data.startswith("forgot_"):
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

        elif data.startswith("forgot_"):
            df.at[idx, "learned"] = False
            df.at[idx, "last_review"] = pd.NaT
            df.at[idx, "interval"] = 1

        save_user_words(user_id, df)

        try:
            await query.message.delete()
        except Exception:
            pass

        await send_new_word(user_id, chat_id, context.bot, context)

    elif data == "cmd_word":
        await send_new_word(user_id, chat_id, context.bot, context)

    elif data == "cmd_progress":
        settings = load_user_settings(user_id)
        level = settings.get("level", "all")

        filtered_df = get_filtered_df(user_id)
        learned = int(filtered_df["learned"].sum())
        total = len(filtered_df)
        remaining = total - learned
        percent = int((learned / total) * 100) if total > 0 else 0

        level_text = "Все слова" if level == "all" else level

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📊 Ваш прогресс\n\n"
                f"📚 Раздел: {level_text}\n"
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

if app.job_queue:
    app.job_queue.run_daily(reminder_job, time=time(hour=9, minute=0))

app.run_polling()