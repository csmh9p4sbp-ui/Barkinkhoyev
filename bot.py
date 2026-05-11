import os
import pandas as pd
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# --- Загрузка словаря ---
if not os.path.exists('words.csv'):
    df = pd.DataFrame(columns=['كلمة', 'слово', 'learned', 'last_review', 'interval'])
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
else:
    df = pd.read_csv('words.csv', encoding='utf-8-sig')

    df['learned'] = df['learned'].astype(bool)
    df['last_review'] = pd.to_datetime(df['last_review'], errors='coerce')

    if 'interval' not in df.columns:
        df['interval'] = 1

# --- Проверка BOT_TOKEN ---
token = os.environ.get('BOT_TOKEN')

if not token:
    raise ValueError("Ошибка: переменная BOT_TOKEN не установлена!")

# --- Главное меню ---
def main_menu():
    buttons = [
        [InlineKeyboardButton("📖 Новое слово", callback_data="cmd_word")],
        [InlineKeyboardButton("📊 Прогресс", callback_data="cmd_progress")],
        [InlineKeyboardButton("✅ Выученные", callback_data="cmd_learned")],
        [InlineKeyboardButton("🔄 Начать заново", callback_data="cmd_reset")],
    ]

    return InlineKeyboardMarkup(buttons)

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    greeting = (
        "Ассаляму алейкум! 📖\n\n"
        "Добро пожаловать в бот для изучения слов Священного Корана.\n\n"
        "Нажмите кнопку «Новое слово», чтобы начать обучение."
    )

    await update.message.reply_text(
        greeting,
        reply_markup=main_menu()
    )

# --- Отправка нового слова ---
async def send_new_word(chat_id, bot):
    today = datetime.now().replace(microsecond=0)

    due = df[
        (df['learned']) &
        (df['last_review'] + pd.to_timedelta(df['interval'], unit='D') <= today)
    ]

    new_words = df[~df['learned']]

    pool = pd.concat([due, new_words])

    if pool.empty:
        await bot.send_message(
            chat_id=chat_id,
            text="🎉 Все слова выучены!"
        )
        return

    word = pool.sample(1).iloc[0]

    buttons = [
        [InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{word.name}")]
    ]

    if word['learned']:
        buttons.append(
            [InlineKeyboardButton("💡 Помню", callback_data=f"remember_{word.name}")]
        )

    markup = InlineKeyboardMarkup(buttons)

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "╔════════════╗\n\n"
            f"      {word['كلمة']}\n\n"
            f"       {word['слово']}\n\n"
            "╚════════════╝"
        ),
        reply_markup=markup
    )

# --- Команда /word ---
async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_new_word(update.effective_chat.id, context.bot)

# --- Обработка кнопок ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    data = query.data
    chat_id = query.message.chat_id
    today = datetime.now().replace(microsecond=0)

    # --- Изучение слов ---
    if data.startswith("learned_") or data.startswith("remember_"):
        idx = int(data.split("_")[1])

        if data.startswith("learned"):
            df.at[idx, 'learned'] = True
            df.at[idx, 'last_review'] = today
            df.at[idx, 'interval'] = 1

        elif data.startswith("remember"):
            old_interval = df.at[idx, 'interval']

            df.at[idx, 'last_review'] = today
            df.at[idx, 'interval'] = min(old_interval * 2, 30)

        df.to_csv('words.csv', index=False, encoding='utf-8-sig')

        try:
            await query.message.delete()
        except Exception:
            pass

        await send_new_word(chat_id, context.bot)

    # --- Новое слово ---
    elif data == "cmd_word":
        await send_new_word(chat_id, context.bot)

    # --- Прогресс ---
    elif data == "cmd_progress":
        learned = df['learned'].sum()
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

    # --- Выученные слова ---
    elif data == "cmd_learned":
        learned = df[df['learned']]

        if learned.empty:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Вы пока не выучили ни одного слова."
            )

        else:
            text = "✅ Выученные слова:\n\n"

            for _, r in learned.iterrows():
                text += f"{r['слово']} — {r['كلمة']}\n"

            await context.bot.send_message(
                chat_id=chat_id,
                text=text[:4000]
            )

    # --- Сброс ---
    elif data == "cmd_reset":
        df['learned'] = False
        df['last_review'] = pd.NaT
        df['interval'] = 1

        df.to_csv('words.csv', index=False, encoding='utf-8-sig')

        await context.bot.send_message(
            chat_id=chat_id,
            text="🔄 Все слова сброшены. Можно начать заново!"
        )

# --- Настройка приложения ---
app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('word', daily_word))

app.add_handler(
    CallbackQueryHandler(button_handler)
)

# --- Запуск ---
app.run_polling()