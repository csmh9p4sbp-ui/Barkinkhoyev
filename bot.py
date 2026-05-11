import os
import pandas as pd
import random
from datetime import datetime
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
        df['interval'] = 1

# --- Приветствие ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Ассаляму алейкум! Добро пожаловать в бот для изучения слов Священного Корана! 📖"
    )

# --- Новый Word ---
async def send_new_word(chat_id, bot):
    today = datetime.now()
    due = df[(df['learned']) & (df['last_review'] + pd.to_timedelta(df['interval'], unit='d') <= today)]
    new = df[~df['learned']]
    pool = pd.concat([due, new])

    if pool.empty:
        await bot.send_message(chat_id=chat_id, text="Все слова выучены! 🎉")
        return

    word = pool.sample(1).iloc[0]

    buttons = [[InlineKeyboardButton("✅ Выучено", callback_data=f"learned_{word.name}")]]
    if word['learned']:
        buttons.append([InlineKeyboardButton("💡 Помню", callback_data=f"remember_{word.name}")])

    markup = InlineKeyboardMarkup(buttons)
    await bot.send_message(chat_id=chat_id, text=f"{word['слово']} — {word['كلمة']}", reply_markup=markup)

# --- /word ---
async def daily_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_new_word(update.effective_chat.id, context.bot)

# --- Обработка кнопок ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    idx = int(data.split("_")[1])
    today = datetime.now()

    # учим или запоминаем
    if data.startswith("learned"):
        df.at[idx, 'learned'] = True
        df.at[idx, 'last_review'] = today
        df.at[idx, 'interval'] = 1
    elif data.startswith("remember"):
        old_int = df.at[idx, 'interval']
        df.at[idx, 'last_review'] = today
        df.at[idx, 'interval'] = min(old_int * 2, 30)

    df.to_csv('words.csv', index=False, encoding='utf-8-sig')

    # удаляем сообщение
    try:
        await query.message.delete()
    except:
        pass

    # отправляем новое
    await send_new_word(update.effective_chat.id, context.bot)

# --- /progress ---
async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    learned = df['learned'].sum()
    total = len(df)
    remain = total - learned
    pct = int((learned/total)*100) if total > 0 else 0

    await update.message.reply_text(
        f"Вы выучили {learned} слов из {total}.\n"
        f"Осталось выучить ещё {remain}.\n"
        f"Прогресс: {pct}% освоено ✅"
    )

# --- /learned ---
async def learned_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    learned = df[df['learned']]
    if learned.empty:
        await update.message.reply_text("Вы пока не выучили ни одного слова.")
        return
    text = "Список выученных слов:\n"
    for _, r in learned.iterrows():
        text += f"{r['слово']} — {r['كلمة']}\n"
    await update.message.reply_text(text)

# --- Запуск ---
app = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('word', daily_word))
app.add_handler(CommandHandler('progress', progress))
app.add_handler(CommandHandler('learned', learned_list))
app.add_handler(CallbackQueryHandler(button_handler))

app.run_polling()