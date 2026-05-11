import os
import pandas as pd
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import matplotlib.pyplot as plt

# --- Загрузка словаря ---
if not os.path.exists('words.csv'):
    df = pd.DataFrame(columns=['كلمة','слово','learned','last_review'])
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
else:
    df = pd.read_csv('words.csv', encoding='utf-8-sig')
    df['learned'] = df['learned'].astype(bool)
    df['last_review'] = pd.to_datetime(df['last_review'], errors='coerce')

# --- Дата последнего изучения для каждого пользователя ---
user_last_seen = {}  # {user_id: datetime}

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_last_seen[update.effective_user.id] = datetime.now()
    await update.message.reply_text("Привет! Готов учить слова Корана! 📖")

async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_seen[user_id] = datetime.now()

    new_words = df[~df['learned']]
    if new_words.empty:
        await update.message.reply_text("Все слова выучены! 🎉")
        return

    word = new_words.sample(1).iloc[0]
    keyboard = [[InlineKeyboardButton("✅ Выучено", callback_data=f'learned_{word.name}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"{word['слово']} — {word['كلمة']}", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[1])
    df.at[idx, 'learned'] = True
    df.at[idx, 'last_review'] = datetime.now()
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
    await query.edit_message_text("Слово отмечено как выученное ✅")

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    learned_count = df['learned'].sum()
    remaining_count = len(df) - learned_count

    colors = ['#D35400', '#F1C40F']  # Кавказская палитра
    labels = ['Выучено', 'Осталось']

    plt.figure(figsize=(6,6))
    plt.pie([learned_count, remaining_count], labels=labels, autopct='%1.1f%%', startangle=90,
            colors=colors, wedgeprops={'edgecolor':'#6E2C00', 'linewidth':2})
    plt.title('Прогресс изучения слов', fontsize=16, fontweight='bold', color='#6E2C00')
    plt.savefig('progress.png', dpi=150, transparent=True)
    plt.close()
    await update.message.reply_photo(open('progress.png','rb'))

# --- Напоминания пользователям ---
async def remind_users(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for user_id, last_seen in user_last_seen.items():
        if now - last_seen >= timedelta(days=3):
            try:
                await context.bot.send_message(chat_id=user_id,
                    text="⏰ Вы не изучали слова последние 3 дня! Пора повторить /word")
            except:
                continue

# --- Настройка приложения ---
app = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('word', daily_word))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(CommandHandler('progress', progress))

# --- JobQueue для напоминаний ---
job_queue = app.job_queue
job_queue.run_repeating(remind_users, interval=24*3600, first=10)  # проверка каждый день

# --- Запуск ---
app.run_polling()