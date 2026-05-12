import os
import json
import shutil
import random
import asyncio
import pandas as pd
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from matplotlib import font_manager
from groq import Groq

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
ARABIC_FONT_FILE = "NotoNaskhArabic-Regular.ttf"

AI_COOLDOWN_SECONDS = 12
LAST_AI_REQUESTS = {}

os.makedirs(USERS_DIR, exist_ok=True)

token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")

groq_key = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=groq_key) if groq_key else None
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")


def get_user_words_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_words.csv")


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения JSON: {e}")


def load_reminders():
    return load_json_file(REMINDERS_FILE, {})


def save_reminders(data):
    save_json_file(REMINDERS_FILE, data)


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

    df["learned"] = df["learned"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["last_review"] = pd.to_datetime(df["last_review"], errors="coerce")
    df["interval"] = pd.to_numeric(df["interval"], errors="coerce").fillna(1).astype(int)

    return df


def save_user_words(user_id, df):
    df.to_csv(get_user_words_file(user_id), index=False, encoding="utf-8-sig")


def get_arabic_font_path():
    if os.path.exists(ARABIC_FONT_FILE):
        return ARABIC_FONT_FILE
    return font_manager.findfont("DejaVu Sans")


def get_russian_font_path():
    return font_manager.findfont("DejaVu Sans")


def fit_text(draw, text, max_width, start_size, min_size=40, font_path=None):
    if font_path is None:
        font_path = get_russian_font_path()

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
    try:
        if os.path.exists(TEMPLATE_FILE):
            img = Image.open(TEMPLATE_FILE).convert("RGB")
        else:
            img = Image.new("RGB", (1280, 720), "#f6ead7")

        draw = ImageDraw.Draw(img)
        width, height = img.size

        arabic_display = get_display(arabic_reshaper.reshape(str(arabic_word)))

        arabic_font = fit_text(
            draw,
            arabic_display,
            int(width * 0.70),
            190,
            90,
            font_path=get_arabic_font_path(),
        )

        draw_centered_text(
            draw,
            arabic_display,
            int(height * 0.39),
            arabic_font,
            width,
            "#064d36",
        )

        if russian_word is not None:
            russian_text = str(russian_word)

            russian_font = fit_text(
                draw,
                russian_text,
                int(width * 0.65),
                78,
                45,
                font_path=get_russian_font_path(),
            )

            draw_centered_text(
                draw,
                russian_text,
                int(height * 0.76),
                russian_font,
                width,
                "#064d36",
            )

        bio = BytesIO()
        bio.name = "word_card.png"
        img.save(bio, "PNG")
        bio.seek(0)

        return bio

    except Exception as e:
        print(f"Ошибка создания карточки: {e}")

        img = Image.new("RGB", (1280, 720), "#f6ead7")
        draw = ImageDraw.Draw(img)
        width, height = img.size

        fallback_font = ImageFont.truetype(get_russian_font_path(), 70)
        text = f"{arabic_word}\n{russian_word or ''}"

        draw.text(
            (width / 2 - 250, height / 2 - 80),
            text,
            font=fallback_font,
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
    if not groq_client:
        return "🤖 ИИ-учитель пока не подключён.\n\nНужно добавить GROQ_API_KEY в переменные окружения."

    def run():
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты языковой помощник по кораническому арабскому. "
                        "Отвечай кратко, понятно, на русском языке."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.4,
            max_tokens=500,
        )

        return response.choices[0].message.content or "Не получилось получить ответ."

    try:
        return await asyncio.to_thread(run)

    except Exception as e:
        error_text = str(e)

        if "rate_limit" in error_text.lower() or "429" in error_text:
            return "🤖 Сейчас лимит бесплатных запросов Groq временно исчерпан.\n\nПопробуй ещё раз позже."

        return f"Ошибка ИИ: {e}"


async def send_long_message(bot, chat_id, text):
    for i in range(0, len(text), 3500):
        await bot.send_message(chat_id=chat_id, text=text[i:i + 3500])


async def safe_query_answer(query):
    try:
        await query.answer()
    except Exception as e:
        error_text = str(e)
        if "Query is too old" in error_text or "query id is invalid" in error_text:
            return
        print(f"Ошибка query.answer: {e}")


async def safe_delete_message(message):
    try:
        await message.delete()
    except Exception:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    load_user_words(user_id)

    await update.message.reply_text(
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Корана.\n\n"
        "📖 «Новое слово» — изучение слов по частоте употребления в Коране.\n"
        "📊 «Прогресс» — посмотреть статистику.\n"
        "✅ «Выученные» — список изученных слов.\n\n"
        "🤖 Также можно просто написать вопрос в чат — ИИ-учитель ответит по арабскому языку.",
        reply_markup=main_menu(),
    )


async def send_quiz_offer(user_id, chat_id, bot):
    df = load_user_words(user_id)
    learned_count = int(df["learned"].sum())

    if learned_count < 5:
        return

    options = [5]

    if learned_count >= 10:
        options.append(10)

    if learned_count >= 20:
        options.append(20)

    if learned_count not in options:
        options.append(learned_count)

    buttons = [
        [InlineKeyboardButton(f"📋 Проверить {count} слов", callback_data=f"quiz_start_{count}")]
        for count in options
    ]

    await bot.send_message(
        chat_id=chat_id,
        text=f"📋 Ты уже выучил {learned_count} слов.\n\nХочешь проверить знания?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def send_new_word(user_id, chat_id, bot, context=None):
    df = load_user_words(user_id)
    today = datetime.now().replace(microsecond=0)

    due = df[
        (df["learned"]) &
        (df["last_review"] + pd.to_timedelta(df["interval"], unit="D") <= today)
    ]

    new_words = df[~df["learned"]]
    pool = pd.concat([sort_by_quran_frequency(due), sort_by_quran_frequency(new_words)])

    if pool.empty:
        await bot.send_message(chat_id=chat_id, text="🎉 Все слова на сегодня пройдены!")
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


async def start_quiz(user_id, chat_id, bot, context, count):
    df = load_user_words(user_id)
    learned_words = df[df["learned"]]

    if len(learned_words) < 5:
        await bot.send_message(
            chat_id=chat_id,
            text="📋 Проверка осуществляется только по уже выученным словам.\n\nСначала выучи хотя бы 5 слов.",
        )
        return

    learned_words = sort_by_quran_frequency(learned_words)
    count = min(count, len(learned_words))

    quiz_words = learned_words.head(count).sample(frac=1).to_dict("records")

    context.user_data["quiz_session"] = {
        "words": quiz_words,
        "current": 0,
        "correct": 0,
        "wrong": [],
        "total": count,
        "current_options": [],
    }

    await send_next_quiz_question(user_id, chat_id, bot, context)


async def send_next_quiz_question(user_id, chat_id, bot, context):
    session = context.user_data.get("quiz_session")

    if not session:
        return

    if session["current"] >= session["total"]:
        total = session["total"]
        correct = session["correct"]
        wrong = session["wrong"]

        text = f"📋 Проверка завершена!\n\n✅ Правильных ответов: {correct} из {total}"

        if wrong:
            text += "\n\n❌ Ошибки:\n"
            for item in wrong:
                text += f"{item['arabic']} — {item['russian']}\n"
        else:
            text += "\n\n🎉 Отлично! Ошибок нет."

        context.user_data.pop("quiz_session", None)
        await send_long_message(bot, chat_id, text)
        return

    df = load_user_words(user_id)
    session = context.user_data["quiz_session"]
    question = session["words"][session["current"]]

    correct_answer = str(question["слово"])
    arabic_word = str(question["كلمة"])

    wrong_pool = (
        df[df["слово"].astype(str) != correct_answer]["слово"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    wrong_answers = random.sample(wrong_pool, min(3, len(wrong_pool)))

    options = wrong_answers + [correct_answer]
    random.shuffle(options)

    session["current_options"] = options
    context.user_data["quiz_session"] = session

    buttons = [
        [InlineKeyboardButton(option, callback_data=f"quiz_answer_{i}")]
        for i, option in enumerate(options)
    ]

    card = create_word_card(arabic_word, None)

    await bot.send_photo(
        chat_id=chat_id,
        photo=card,
        caption=f"📋 Вопрос {session['current'] + 1} из {session['total']}\nВыбери правильный перевод:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_quiz_answer(user_id, chat_id, selected_index, query, context):
    session = context.user_data.get("quiz_session")

    if not session:
        await context.bot.send_message(chat_id=chat_id, text="Проверка устарела. Начни заново.")
        return

    try:
        selected = session["current_options"][int(selected_index)]
    except Exception:
        selected = ""

    question = session["words"][session["current"]]
    correct_answer = str(question["слово"])

    if selected == correct_answer:
        session["correct"] += 1
    else:
        session["wrong"].append({
            "arabic": str(question["كلمة"]),
            "russian": str(question["слово"]),
            "selected": selected,
        })

    session["current"] += 1
    context.user_data["quiz_session"] = session

    await safe_delete_message(query.message)
    await send_next_quiz_question(user_id, chat_id, context.bot, context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_query_answer(query)

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    today = datetime.now().replace(microsecond=0)

    df = load_user_words(user_id)

    try:
        if data.startswith("quiz_start_"):
            count = int(data.replace("quiz_start_", "", 1))
            await safe_delete_message(query.message)
            await start_quiz(user_id, chat_id, context.bot, context, count)

        elif data.startswith("quiz_answer_"):
            selected_index = data.replace("quiz_answer_", "", 1)
            await handle_quiz_answer(user_id, chat_id, selected_index, query, context)

        elif data == "cmd_reminders":
            reminders = load_reminders()
            enabled = str(user_id) in reminders

            if enabled:
                buttons = [[InlineKeyboardButton("🔕 Выключить напоминания", callback_data="reminder_off")]]
                text = "🔔 Напоминания включены."
            else:
                buttons = [[InlineKeyboardButton("🔔 Включить напоминания", callback_data="reminder_on")]]
                text = "🔔 Можно включить напоминание. Сейчас оно сохраняется в настройках."

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )

        elif data == "reminder_on":
            reminders = load_reminders()
            reminders[str(user_id)] = chat_id
            save_reminders(reminders)
            await context.bot.send_message(chat_id=chat_id, text="🔔 Напоминания включены.")

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

        elif data.startswith("learned_") or data.startswith("remember_") or data.startswith("forgot_"):
            idx = int(data.split("_")[1])

            if idx not in df.index:
                await context.bot.send_message(chat_id=chat_id, text="Ошибка: слово не найдено.")
                return

            old_learned_count = int(df["learned"].sum())

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
            await safe_delete_message(query.message)

            new_learned_count = int(df["learned"].sum())

            if old_learned_count < 5 <= new_learned_count:
                await send_quiz_offer(user_id, chat_id, context.bot)

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
                    f"📊 Ваш прогресс\n\n"
                    f"✅ Выучено: {learned} из {total}\n"
                    f"📖 Осталось: {remaining}\n"
                    f"📈 Прогресс: {percent}%"
                ),
            )

            if learned >= 5:
                await send_quiz_offer(user_id, chat_id, context.bot)

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
            context.user_data.pop("quiz_session", None)
            await context.bot.send_message(chat_id=chat_id, text="🔄 Прогресс сброшен.")

    except Exception as e:
        print(f"Ошибка в button_handler: {e}")
        await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка. Попробуй нажать кнопку ещё раз.")


async def ai_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now().timestamp()

    last_time = LAST_AI_REQUESTS.get(user_id, 0)
    if now - last_time < AI_COOLDOWN_SECONDS:
        await update.message.reply_text("⏳ Подожди немного перед следующим вопросом к ИИ.")
        return

    LAST_AI_REQUESTS[user_id] = now

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


app = ApplicationBuilder().token(token).job_queue(None).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("word", daily_word))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_text_handler))
app.add_error_handler(error_handler)

app.run_polling()