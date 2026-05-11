import os
import pandas as pd
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import matplotlib.pyplot as plt

# Загрузка CSV
if not os.path.exists('words.csv'):
    df = pd.DataFrame(columns=['كلمة','слово','learned','last_review'])
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
else:
    df = pd.read_csv('words.csv', encoding='utf-8-sig')
    df['learned'] = df['learned'].astype(bool)
    df['last_review'] = pd.to_datetime(df['last_review'], errors='coerce')

user_pause = {}

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Готов учить слова Корана! 📖")

async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_pause.get(user_id, False):
        await update.message.reply_text("Вы на отдыхе 🛌, новых слов пока нет.")
        return

    new_words = df[~df['learned']]
    if new_words.empty:
        await update.message.reply_text("Все слова выучены! 🎉")
        return

    word = new_words.sample(1).iloc[0]
    keyboard = [[InlineKeyboardButton("✅ Выучено", callback_data=f'learned_{word.name}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"{word['كلمة']} — {word['слово']}", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    idx = int(data.split('_')[1])
    df.at[idx, 'learned'] = True
    df.at[idx, 'last_review'] = datetime.now()
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
    await query.edit_message_text("Слово отмечено как выученное ✅")

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_pause[update.effective_user.id] = True
    await update.message.reply_text("Режим отдыха активирован на 7 дней 🛌")

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_pause[update.effective_user.id] = False
    await update.message.reply_text("Возвращаемся к изучению 📖")

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    learned_count = df['learned'].sum()
    total_count = len(df)
    plt.figure(figsize=(6,4))
    plt.bar(['Выучено','Осталось'], [learned_count, total_count-learned_count])
    plt.title('Прогресс изучения слов')
    plt.savefig('progress.png')
    plt.close()
    await update.message.reply_photo(open('progress.png','rb'))

async def add_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        arabic, meaning = context.args[0], context.args[1]
        global df
        df = pd.concat([df, pd.DataFrame({'كلمة':[arabic],'слово':[meaning],'learned':[False],'last_review':[pd.NaT]})], ignore_index=True)
        df.to_csv('words.csv', index=False, encoding='utf-8-sig')
        await update.message.reply_text(f"Слово {arabic} добавлено ✅")
    except:
        await update.message.reply_text("Использование: /add_word <арабское слово> <значение>")

# --- Настройка приложения ---
app = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('word', daily_word))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(CommandHandler('pause', pause))
app.add_handler(CommandHandler('resume', resume))
app.add_handler(CommandHandler('progress', progress))
app.add_handler(CommandHandler('add_word', add_word))

# --- Запуск ---
app.run_polling()