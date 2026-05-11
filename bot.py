import os
import pandas as pd
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# --- Загрузка словаря ---
if not os.path.exists('words.csv'):
    df = pd.DataFrame(columns=['كلمة','слово','learned','last_review','interval'])
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
else:
    df = pd.read_csv('words.csv', encoding='utf-8-sig')
    df['learned'] = df['learned'].astype(bool)
    df['last_review'] = pd.to_datetime(df['last_review'], errors='coerce')
    if 'interval' not in df.columns:
        df['interval'] = 1  # по умолчанию интервал 1 день

# --- Дата последнего изучения для каждого пользователя ---
user_last_seen = {}

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_last_seen[update.effective_user.id] = datetime.now()
    await update.message.reply_text("Ассаляму алейкум! Добро пожаловать в бот для изучения слов Священного Корана! 📖")

# --- Выдача слова с учетом интервального повторения ---
async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_seen[user_id] = datetime.now()

    today = datetime.now()
    due_words = df[(df['learned']) & (df['last_review'] + pd.to_timedelta(df['interval'], unit='d') <= today)]
    new_words = df[~df['learned']]
    candidates = pd.concat([due_words, new_words])

    if candidates.empty:
        await update.message.reply_text("Все слова выучены! 🎉")
        return

    word = candidates.sample(1).iloc[0]
    keyboard = [
        [InlineKeyboardButton("✅ Выучено", callback_data=f'learned_{word.name}')],
        [InlineKeyboardButton("💡 Помню", callback_data=f'remember_{word.name}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"{word['слово']} — {word['كلمة']}", reply_markup=reply_markup)

# --- Обработка кнопок ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    idx = int(data.split('_')[1])
    word_row = df.loc[idx]

    today = datetime.now()
    if data.startswith('learned'):
        df.at[idx, 'learned'] = True
        df.at[idx, 'last_review'] = today
        df.at[idx, 'interval'] = 1
    elif data.startswith('remember'):
        df.at[idx, 'last_review'] = today
        df.at[idx, 'interval'] = min(word_row.get('interval',1) * 2, 30)

    df.to_csv('words.csv', index=False, encoding='utf-8-sig')

    # Удаляем старое сообщение и отправляем новое слово
    await query.message.delete()
    await daily_word(update, context)

# --- Текстовый прогресс ---
async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    learned_count = df['learned'].sum()
    total_count = len(df)
    remaining_count = total_count - learned_count
    percent = int((learned_count / total_count) * 100) if total_count > 0 else 0
    message = (f"Вы выучили {learned_count} слов из {total_count}.\n"
               f"Осталось выучить ещё {remaining_count} слов.\n"
               f"Прогресс: {percent}% освоено ✅")
    await update.message.reply_text(message)

# --- Вывод выученных слов ---
async def learned_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    learned = df[df['learned']]
    if learned.empty:
        await update.message.reply_text("Вы пока не выучили ни одного слова.")
        return
    message = "Список выученных слов:\n"
    for _, row in learned.iterrows():
        message += f"{row['слово']} — {row['كلمة']}\n"
    await update.message.reply_text(message)

# --- Настройка приложения ---
app = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('word', daily_word))
app.add_handler(CommandHandler('progress', progress))
app.add_handler(CommandHandler('learned', learned_list))
app.add_handler(CallbackQueryHandler(button))

# --- Запуск ---
app.run_polling()