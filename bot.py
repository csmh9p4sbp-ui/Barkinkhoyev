import os
import json
import shutil
import random
import asyncio
import hashlib
import logging
from datetime import datetime
from io import BytesIO

import pandas as pd
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from matplotlib import font_manager
from groq import Groq

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TimedOut, NetworkError, RetryAfter, BadRequest, TelegramError
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
CARDS_DIR = "cards"
TEMPLATE_FILE = "card_template.PNG"
ARABIC_FONT_FILE = "NotoNaskhArabic-Regular.ttf"

AI_COOLDOWN_SECONDS = 12
LAST_AI_REQUESTS = {}
MAX_MESSAGE_LENGTH = 3500

os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(CARDS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


async def safe_send_message(bot, chat_id, text, reply_markup=None):
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )
    except RetryAfter as e:
        await asyncio.sleep(int(e.retry_after) + 1)
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup
            )
        except Exception as err:
            logger.warning(f"Ошибка повторной отправки сообщения: {err}")
    except (TimedOut, NetworkError) as e:
        logger.warning(f"Telegram timeout/network при отправке сообщения: {e}")
    except TelegramError as e:
        logger.warning(f"Telegram error при отправке сообщения: {e}")
    except Exception as e:
        logger.warning(f"Неизвестная ошибка отправки сообщения: {e}")


async def safe_send_photo(bot, chat_id, photo, caption=None, reply_markup=None):
    try:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup
        )
    except RetryAfter as e:
        await asyncio.sleep(int(e.retry_after) + 1)
        try:
            photo.seek(0)
            return await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup
            )
        except Exception as err:
            logger.warning(f"Ошибка повторной отправки фото: {err}")
    except (TimedOut, NetworkError) as e:
        logger.warning(f"Telegram timeout/network при отправке фото: {e}")
        await safe_send_message(
            bot,
            chat_id,
            caption or "Слово отправлено, но картинка временно не загрузилась.",
            reply_markup=reply_markup
        )
    except TelegramError as e:
        logger.warning(f"Telegram error при отправке фото: {e}")
    except Exception as e:
        logger.warning(f"Неизвестная ошибка отправки фото: {e}")


async def safe_query_answer(query):
    try:
        await query.answer()
    except Exception as e:
        text = str(e)
        if "Query is too old" in text or "query id is invalid" in text:
            return
        logger.warning(f"Ошибка query.answer: {e}")


async def safe_delete_message(message):
    try:
        await message.delete()
    except BadRequest:
        pass
    except TimedOut:
        pass
    except TelegramError:
        pass
    except Exception:
        pass


async def send_long_message(bot, chat_id, text):
    if not text:
        return

    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        await safe_send_message(bot, chat_id, text[i:i + MAX_MESSAGE_LENGTH])


def get_user_words_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_words.csv")


def get_user_settings_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}_settings.json")


def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            merged = default.copy()
            merged.update(data)
            return merged

        return default

    except Exception as e:
        logger.warning(f"Ошибка чтения JSON {path}: {e}")
        return default


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Ошибка сохранения JSON {path}: {e}")


def load_user_settings(user_id):
    return load_json_file(
        get_user_settings_file(user_id),
        {
            "last_quiz_offer_count": 0,
            "started": False
        }
    )


def save_user_settings(user_id, settings):
    save_json_file(get_user_settings_file(user_id), settings)


def get_frequency_column(df):
    for col in ["частота", "frequency", "freq", "count", "количество"]:
        if col in df.columns:
            return col
    return None


def sort_by_quran_frequency(df):
    if df.empty:
        return df

    freq_col = get_frequency_column(df)

    if freq_col:
        temp = df.copy()
        temp[freq_col] = pd.to_numeric(temp[freq_col], errors="coerce").fillna(0)
        return temp.sort_values(by=freq_col, ascending=False)

    return df.sort_index()


def ensure_words_file_exists():
    if not os.path.exists(WORDS_FILE):
        df = pd.DataFrame(columns=["كلمة", "слово", "learned", "last_review", "interval"])
        df.to_csv(WORDS_FILE, index=False, encoding="utf-8-sig")


def load_user_words(user_id):
    ensure_words_file_exists()

    user_file = get_user_words_file(user_id)

    if not os.path.exists(user_file):
        shutil.copy(WORDS_FILE, user_file)

    try:
        df = pd.read_csv(user_file, encoding="utf-8-sig")
    except Exception as e:
        logger.warning(f"Ошибка чтения файла пользователя, пересоздаю: {e}")
        shutil.copy(WORDS_FILE, user_file)
        df = pd.read_csv(user_file, encoding="utf-8-sig")

    required_columns = {
        "كلمة": "",
        "слово": "",
        "learned": False,
        "last_review": pd.NaT,
        "interval": 1,
    }

    for col, default in required_columns.items():
        if col not in df.columns:
            df[col] = default

    df["learned"] = df["learned"].astype(str).str.lower().isin(["true", "1", "yes"])
    df["last_review"] = pd.to_datetime(df["last_review"], errors="coerce")
    df["interval"] = pd.to_numeric(df["interval"], errors="coerce").fillna(1).astype(int)

    df["كلمة"] = df["كلمة"].fillna("").astype(str)
    df["слово"] = df["слово"].fillna("").astype(str)

    return df


def save_user_words(user_id, df):
    try:
        df.to_csv(get_user_words_file(user_id), index=False, encoding="utf-8-sig")
    except Exception as e:
        logger.warning(f"Ошибка сохранения words пользователя: {e}")


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


def get_card_cache_path(arabic_word, russian_word=None):
    raw_name = f"{arabic_word}_{russian_word or 'quiz'}"
    digest = hashlib.md5(raw_name.encode("utf-8")).hexdigest()
    return os.path.join(CARDS_DIR, f"{digest}.png")


def create_word_card(arabic_word, russian_word=None):
    cache_path = get_card_cache_path(arabic_word, russian_word)

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                bio = BytesIO(f.read())
                bio.name = "word_card.png"
                bio.seek(0)
                return bio
        except Exception:
            pass

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

        try:
            img.save(cache_path, "PNG")
        except Exception as e:
            logger.warning(f"Не удалось сохранить кэш карточки: {e}")

        img.save(bio, "PNG")
        bio.seek(0)

        return bio

    except Exception as e:
        logger.warning(f"Ошибка создания карточки: {e}")

        img = Image.new("RGB", (1280, 720), "#f6ead7")
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(get_russian_font_path(), 70)

        text = f"{arabic_word}\n{russian_word or ''}"
        draw.text((120, 250), text, font=font, fill="#064d36")

        bio = BytesIO()
        bio.name = "word_card.png"
        img.save(bio, "PNG")
        bio.seek(0)

        return bio


def main_menu(show_new_word=True):
    buttons = []

    if show_new_word:
        buttons.append([
            InlineKeyboardButton("📖 Отправь новое слово", callback_data="cmd_word")
        ])

    buttons.extend([
        [InlineKeyboardButton("📋 Тестирование", callback_data="cmd_testing")],
        [InlineKeyboardButton("✅ Выученные", callback_data="cmd_learned")],
    ])

    return InlineKeyboardMarkup(buttons)


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
        "Отвечай на русском языке кратко, понятно и уважительно. "
        "Ты не муфтий и не даёшь фетвы. "
        "Фокусируйся только на языке: значение, корень, перевод, запоминание. "
        "Ответ должен быть не длиннее 6-8 предложений.\n"
        f"{word_context}\n"
        f"Вопрос пользователя: {user_question}"
    )


async def ask_ai(prompt):
    if not groq_client:
        return "📑 ИИ-учитель пока не подключён.\n\nНужно добавить GROQ_API_KEY в переменные окружения."

    def run():
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Ты языковой помощник по кораническому арабскому. Отвечай кратко на русском языке.",
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
        text = str(e).lower()

        if "rate" in text or "429" in text:
            return (
                "Ассаламу алейкум!\n\n"
                "Сейчас ИИ-учитель временно перегружен. Попробуй задать вопрос чуть позже."
            )

        logger.warning(f"Ошибка ИИ: {e}")
        return (
            "Ассаламу алейкум!\n\n"
            "Сейчас ИИ-учитель временно недоступен. "
            "Но бот продолжает работать: можно учить слова и проходить тестирование."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    load_user_words(user_id)

    settings = load_user_settings(user_id)
    already_started = bool(settings.get("started", False))

    text = (
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для заучивания слов из Священного Корана.\n\n"
        "Бот помогает постепенно запоминать часто встречающиеся слова Корана через карточки, повторение и тестирование.\n\n"
        "📖 Слова приходят в карточках\n"
        "📋 По мере заучивания бот предлагает проверить твои знания\n"
        "📑 Можно задать вопрос ИИ ассистенту прямо в чате или нажать «📑 Объяснить слово», ИИ расскажет о слове\n\n"
    )

    if already_started:
        text += "Ты уже начал обучение. Продолжай изучение по карточкам ниже."
    else:
        text += "Чтобы начать изучение, нажми «Отправь новое слово»."

    await safe_send_message(
        context.bot,
        update.effective_chat.id,
        text,
        reply_markup=main_menu(show_new_word=not already_started)
    )


async def send_quiz_offer(user_id, chat_id, bot):
    df = load_user_words(user_id)
    learned_count = int(df["learned"].sum())

    if learned_count < 5:
        await safe_send_message(
            bot,
            chat_id,
            "📋 Тестирование доступно после 5 выученных слов."
        )
        return

    options = [5]

    if learned_count >= 10:
        options.append(10)

    if learned_count >= 20:
        options.append(20)

    if learned_count not in options:
        options.append(learned_count)

    buttons = [
        [
            InlineKeyboardButton(
                f"📋 Проверить {count} слов",
                callback_data=f"quiz_start_{count}"
            )
        ]
        for count in options
    ]

    await safe_send_message(
        bot,
        chat_id,
        f"📋 Ты уже выучил {learned_count} слов.\n\nВыбери объём тестирования:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def maybe_offer_quiz(user_id, chat_id, bot, learned_count):
    if learned_count < 5:
        return

    settings = load_user_settings(user_id)
    last_offer = int(settings.get("last_quiz_offer_count", 0))

    should_offer = False

    if last_offer < 5 <= learned_count:
        should_offer = True
    elif learned_count - last_offer >= 10:
        should_offer = True

    if should_offer:
        settings["last_quiz_offer_count"] = learned_count
        save_user_settings(user_id, settings)
        await send_quiz_offer(user_id, chat_id, bot)


async def send_new_word(user_id, chat_id, bot, context=None):
    df = load_user_words(user_id)

    if df.empty:
        await safe_send_message(
            bot,
            chat_id,
            "Словарь пока пуст."
        )
        return

    today = datetime.now().replace(microsecond=0)

    due = df[
        (df["learned"]) &
        (
            df["last_review"] +
            pd.to_timedelta(df["interval"], unit="D")
            <= today
        )
    ]

    new_words = df[~df["learned"]]

    pool = pd.concat([
        sort_by_quran_frequency(due),
        sort_by_quran_frequency(new_words)
    ])

    if pool.empty:
        await safe_send_message(
            bot,
            chat_id,
            "🎉 Все слова на сегодня пройдены!"
        )
        return

    word = pool.iloc[0]
    idx = word.name

    arabic = str(word["كلمة"])
    russian = str(word["слово"])

    if context:
        context.user_data["last_word"] = {
            "arabic": arabic,
            "russian": russian,
            "idx": int(idx),
        }

    buttons = []

    if bool(word["learned"]):
        buttons.append([
            InlineKeyboardButton(
                "💡 Помню",
                callback_data=f"remember_{idx}"
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                "❌ Не помню",
                callback_data=f"forgot_{idx}"
            )
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                "✅ Выучено",
                callback_data=f"learned_{idx}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "📑 Объяснить слово",
            callback_data=f"explain_{idx}"
        )
    ])

    card = create_word_card(arabic, russian)

    await safe_send_photo(
        bot,
        chat_id,
        card,
        caption=f"{arabic} — {russian}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_user_settings(update.effective_user.id)
    settings["started"] = True
    save_user_settings(update.effective_user.id, settings)

    await send_new_word(
        update.effective_user.id,
        update.effective_chat.id,
        context.bot,
        context
    )


async def start_quiz(user_id, chat_id, bot, context, count):
    df = load_user_words(user_id)
    learned_words = df[df["learned"]]

    if len(learned_words) < 5:
        await safe_send_message(
            bot,
            chat_id,
            "📋 Тестирование проводится только по выученным словам.\n\nСначала выучи хотя бы 5 слов."
        )
        return

    learned_words = sort_by_quran_frequency(learned_words)
    count = min(count, len(learned_words))

    quiz_df = learned_words.head(count).copy()
    quiz_df["_idx"] = quiz_df.index

    context.user_data["quiz_session"] = {
        "words": quiz_df.sample(frac=1).to_dict("records"),
        "current": 0,
        "correct": 0,
        "wrong": [],
        "total": count,
        "current_options": [],
    }

    await send_next_quiz_question(user_id, chat_id, bot, context)


async def finish_quiz(user_id, chat_id, bot, context):
    session = context.user_data.get("quiz_session")

    if not session:
        return

    total = session["total"]
    correct = session["correct"]
    wrong = session["wrong"]

    if wrong:
        df = load_user_words(user_id)

        for item in wrong:
            idx = item.get("idx")

            if idx in df.index:
                df.at[idx, "learned"] = False
                df.at[idx, "last_review"] = pd.NaT
                df.at[idx, "interval"] = 1

        save_user_words(user_id, df)

    text = (
        f"📋 Тестирование завершено!\n\n"
        f"✅ Правильных ответов: {correct} из {total}"
    )

    if wrong:
        text += "\n\n❌ Слова для повторного изучения:\n"

        for item in wrong:
            text += f"{item['arabic']} — {item['russian']}\n"

        text += "\nЭти слова снова будут приходить для повторного изучения."
    else:
        text += "\n\n🎉 Отлично! Ошибок нет."

    context.user_data.pop("quiz_session", None)

    await send_long_message(bot, chat_id, text)


async def send_next_quiz_question(user_id, chat_id, bot, context):
    session = context.user_data.get("quiz_session")

    if not session:
        return

    if session["current"] >= session["total"]:
        await finish_quiz(user_id, chat_id, bot, context)
        return

    df = load_user_words(user_id)
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

    wrong_answers = random.sample(
        wrong_pool,
        min(3, len(wrong_pool))
    )

    options = wrong_answers + [correct_answer]
    random.shuffle(options)

    session["current_options"] = options
    context.user_data["quiz_session"] = session

    buttons = [
        [
            InlineKeyboardButton(
                option,
                callback_data=f"quiz_answer_{i}"
            )
        ]
        for i, option in enumerate(options)
    ]

    card = create_word_card(arabic_word, None)

    await safe_send_photo(
        bot,
        chat_id,
        card,
        caption=(
            f"📋 Вопрос {session['current'] + 1} "
            f"из {session['total']}\n"
            f"Выбери правильный перевод:"
        ),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def handle_quiz_answer(user_id, chat_id, selected_index, query, context):
    session = context.user_data.get("quiz_session")

    if not session:
        await safe_send_message(
            context.bot,
            chat_id,
            "Тестирование устарело. Начни заново."
        )
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
            "idx": question.get("_idx"),
            "arabic": str(question["كلمة"]),
            "russian": str(question["слово"]),
            "selected": selected,
        })

    session["current"] += 1
    context.user_data["quiz_session"] = session

    await safe_delete_message(query.message)

    await send_next_quiz_question(
        user_id,
        chat_id,
        context.bot,
        context
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await safe_query_answer(query)

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    today = datetime.now().replace(microsecond=0)

    try:
        df = load_user_words(user_id)

        if data.startswith("quiz_start_"):
            count = int(data.replace("quiz_start_", "", 1))

            await safe_delete_message(query.message)

            await start_quiz(
                user_id,
                chat_id,
                context.bot,
                context,
                count
            )

        elif data.startswith("quiz_answer_"):
            selected_index = data.replace("quiz_answer_", "", 1)

            await handle_quiz_answer(
                user_id,
                chat_id,
                selected_index,
                query,
                context
            )

        elif data.startswith("explain_"):
            idx = int(data.split("_")[1])

            if idx not in df.index:
                await safe_send_message(
                    context.bot,
                    chat_id,
                    "Ошибка: слово не найдено."
                )
                return

            word = df.loc[idx]

            context.user_data["last_word"] = {
                "arabic": str(word["كلمة"]),
                "russian": str(word["слово"]),
                "idx": int(idx),
            }

            await safe_send_message(
                context.bot,
                chat_id,
                "📑 Объясняю слово..."
            )

            prompt = build_teacher_prompt(
                (
                    f"Объясни слово {word['كلمة']} — {word['слово']}. "
                    f"Дай значение, корень если знаешь, "
                    f"и короткую ассоциацию."
                ),
                context.user_data["last_word"],
            )

            answer = await ask_ai(prompt)

            await send_long_message(
                context.bot,
                chat_id,
                answer
            )

        elif (
            data.startswith("learned_") or
            data.startswith("remember_") or
            data.startswith("forgot_")
        ):
            idx = int(data.split("_")[1])

            if idx not in df.index:
                await safe_send_message(
                    context.bot,
                    chat_id,
                    "Ошибка: слово не найдено."
                )
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

            settings = load_user_settings(user_id)
            settings["started"] = True
            save_user_settings(user_id, settings)

            await safe_delete_message(query.message)

            updated_df = load_user_words(user_id)
            learned_count = int(updated_df["learned"].sum())

            await maybe_offer_quiz(
                user_id,
                chat_id,
                context.bot,
                learned_count
            )

            await send_new_word(
                user_id,
                chat_id,
                context.bot,
                context
            )

        elif data == "cmd_word":
            settings = load_user_settings(user_id)
            settings["started"] = True
            save_user_settings(user_id, settings)

            await safe_delete_message(query.message)

            await send_new_word(
                user_id,
                chat_id,
                context.bot,
                context
            )

        elif data == "cmd_testing":
            await send_quiz_offer(
                user_id,
                chat_id,
                context.bot
            )

        elif data == "cmd_learned":
            learned_words = df[df["learned"]]

            if learned_words.empty:
                await safe_send_message(
                    context.bot,
                    chat_id,
                    "Вы пока не выучили ни одного слова."
                )
            else:
                text = "✅ Ваши выученные слова:\n\n"

                for _, r in learned_words.iterrows():
                    text += f"{r['слово']} — {r['كلمة']}\n"

                await send_long_message(
                    context.bot,
                    chat_id,
                    text
                )

    except TimedOut:
        logger.warning("TimedOut в button_handler")
        return
    except NetworkError as e:
        logger.warning(f"NetworkError в button_handler: {e}")
        return
    except Exception as e:
        logger.warning(f"Ошибка в button_handler: {e}")

        await safe_send_message(
            context.bot,
            chat_id,
            "Произошла ошибка. Попробуй ещё раз."
        )


async def ai_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now().timestamp()

    last_time = LAST_AI_REQUESTS.get(user_id, 0)

    if now - last_time < AI_COOLDOWN_SECONDS:
        await update.message.reply_text(
            "⏳ Подожди немного перед следующим вопросом."
        )
        return

    LAST_AI_REQUESTS[user_id] = now

    user_text = update.message.text.strip()

    if not user_text:
        return

    await update.message.reply_text("📑 Думаю над ответом...")

    last_word = context.user_data.get("last_word")
    prompt = build_teacher_prompt(user_text, last_word)
    answer = await ask_ai(prompt)

    await send_long_message(
        context.bot,
        update.effective_chat.id,
        answer
    )


async def error_handler(update, context):
    logger.warning(f"Глобальная ошибка: {context.error}")


app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .job_queue(None)
    .connect_timeout(60)
    .read_timeout(60)
    .write_timeout(60)
    .pool_timeout(60)
    .build()
)

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("word", daily_word))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        ai_text_handler
    )
)
app.add_error_handler(error_handler)

app.run_polling(
    drop_pending_updates=True,
    allowed_updates=Update.ALL_TYPES,
)