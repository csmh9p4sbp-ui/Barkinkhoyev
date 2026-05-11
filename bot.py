import os
import pandas as pd
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext
import matplotlib.pyplot as plt

TOKEN = os.environ['BOT_TOKEN']

if not os.path.exists('words.csv'):
    df = pd.DataFrame(columns=['كلمة','слово','learned','last_review'])
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')
else:
    df = pd.read_csv('words.csv', encoding='utf-8-sig')
    df['learned'] = df['learned'].astype(bool)
    df['last_review'] = pd.to_datetime(df['last_review'], errors='coerce')

user_pause = {}

def start(update: Update, context: CallbackContext):
    update.message.reply_text("Привет! Готов учить слова Корана! 📖")

def daily_word(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_pause.get(user_id, False):
        update.message.reply_text("Вы на отдыхе 🛌, новых слов пока нет.")
        return
    new_words = df[~df['learned']]
    if new_words.empty:
        update.message.reply_text("Все слова выучены! 🎉")
        return
    word = new_words.sample(1).iloc[0]
    keyboard = [[InlineKeyboardButton("✅ Выучено", callback_data=f'learned_{word.name}'),
                 InlineKeyboardButton("🔄 Повторить позже", callback_data=f'repeat_{word.name}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"{word['كلمة']} — {word['слово']}", reply_markup=reply_markup)

def button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    idx = int(data.split('_')[1])
    if data.startswith('learned'):
        df.at[idx, 'learned'] = True
        df.at[idx, 'last_review'] = datetime.now()
        query.edit_message_text("Слово отмечено как выученное ✅")
    else:
        df.at[idx, 'last_review'] = datetime.now()
        query.edit_message_text("Слово добавлено на повтор 🔄")
    df.to_csv('words.csv', index=False, encoding='utf-8-sig')

def pause(update: Update, context: CallbackContext):
    user_pause[update.message.from_user.id] = True
    update.message.reply_text("Режим отдыха активирован на 7 дней 🛌")

def resume(update: Update, context: CallbackContext):
    user_pause[update.message.from_user.id] = False
    update.message.reply_text("Возвращаемся к изучению 📖")

def progress(update: Update, context: CallbackContext):
    learned_count = df['learned'].sum()
    total_count = len(df)
    plt.figure(figsize=(6,4))
    plt.bar(['Выучено','Осталось'], [learned_count, total_count-learned_count])
    plt.title('Прогресс изучения слов')
    plt.savefig('progress.png')
    plt.close()
    update.message.reply_photo(open('progress.png','rb'))

def add_word(update: Update, context: CallbackContext):
    try:
        arabic, meaning = context.args[0], context.args[1]
        global df
        df = pd.concat([df, pd.DataFrame({'كلمة':[arabic],'слово':[meaning],'learned':[False],'last_review':[pd.NaT]})], ignore_index=True)
        df.to_csv('words.csv', index=False, encoding='utf-8-sig')
        update.message.reply_text(f"Слово {arabic} добавлено ✅")
    except:
        update.message.reply_text("Использование: /add_word <арабское слово> <значение>")

updater = Updater(TOKEN)
dp = updater.dispatcher
dp.add_handler(CommandHandler('start', start))
dp.add_handler(CommandHandler('word', daily_word))
dp.add_handler(CallbackQueryHandler(button))
dp.add_handler(CommandHandler('pause', pause))
dp.add_handler(CommandHandler('resume', resume))
dp.add_handler(CommandHandler('progress', progress))
dp.add_handler(CommandHandler('add_word', add_word))

updater.start_polling()
updater.idle()