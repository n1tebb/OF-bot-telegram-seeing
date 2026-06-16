import configparser
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

config = configparser.ConfigParser()
config.read('config.ini')

try:
    BOT_TOKEN = config['bot']['token']
    SUPER_ADMIN_ID = int(config['admin']['super_admin_id'])
    DB_NAME = config['database'].get('db_name', 'multi_all_seeing_bot.db')
except KeyError as e:
    raise SystemExit(f"Критическая ошибка: В файле config.ini отсутствует секция или ключ: {e}")


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def init_db():
    """
    Создает таблицы. Структура сообщений полностью повторяет логику оригинала,
    но расширена полем business_connection_id для жесткой изоляции данных пользователей.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    
   
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_cache (
            business_connection_id TEXT,
            chat_id INTEGER,
            message_id INTEGER,
            author_name TEXT,
            text TEXT,
            PRIMARY KEY (business_connection_id, chat_id, message_id)
        )
    """)
    conn.commit()
    conn.close()
    logging.info("База данных SQLite успешно инициализирована.")

init_db()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Регистрация нового бизнес-клиента в базе бота"""
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "Без имени"
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()
    
    await message.reply(
        "🌟 **Бот-ассистент готов к работе!**\n\n"
        "Чтобы бот отслеживал удаления в ваших диалогах:\n"
        "1. Зайдите в Настройки -> Telegram для бизнеса -> Чат-боты.\n"
        "2. Добавьте юзернейм этого бота и выберите чаты для защиты."
    )

@dp.business_message()
async def handle_incoming_business_message(message: Message):
    """
    Сохранение входящих/исходящих сообщений. 
    Использует INSERT OR REPLACE для фиксации отредактированного текста (как в оригинале).
    """
    if not message.text:
        return

    author = message.from_user.full_name if message.from_user else "Собеседник"
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO business_cache (business_connection_id, chat_id, message_id, author_name, text)
        VALUES (?, ?, ?, ?, ?)
    """, (message.business_connection_id, message.chat.id, message.message_id, author, message.text))
    conn.commit()
    conn.close()

@dp.deleted_business_messages()
async def handle_deleted_business_messages(event: BusinessMessagesDeleted):
    """Перехват удалений и отправка отчетов соответствующему пользователю"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    for msg_id in event.message_ids:
        # Ищем сообщение строго по связке сессии, чата и ID сообщения
        cursor.execute("""
            SELECT author_name, text FROM business_cache 
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
        """, (event.business_connection_id, event.chat.id, msg_id))
        
        result = cursor.fetchone()

        if result:
            author_name, old_text = result
            
            # Экранирование Markdown спецсимволов
            safe_text = old_text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")
            
            report_text = (
                f"🗑 *В ВАШЕМ БИЗНЕС-ЧАТЕ УДАЛЕНО СООБЩЕНИЕ!*\n\n"
                f"👤 *Отправитель:* {author_name}\n"
                f"🆔 *ID сообщения:* `{msg_id}`\n\n"
                f"📝 *Текст до удаления:*\n_{safe_text}_"
            )
            
            try:
                # Отсылка лога идет строго в бизнес-соединение того пользователя, кто подключил бота
                await bot.send_message(chat_id=event.business_connection_id, text=report_text, parse_mode="MarkdownV2")
                
                # Точечная очистка кэша, чтобы база не забивала диск
                cursor.execute("""
                    DELETE FROM business_cache 
                    WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
                """, (event.business_connection_id, event.chat.id, msg_id))
                conn.commit()
            except TelegramBadRequest:
                logging.error(f"Не удалось доставить отчет для бизнес-сессии {event.business_connection_id}")
                
    conn.close()


@dp.message(Command("admin"), F.from_user.id == SUPER_ADMIN_ID)
async def cmd_admin(message: Message):
    """Главный экран панели администратора"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM business_cache")
    total_cached = cursor.fetchone()[0]
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_list_users")],
        [InlineKeyboardButton(text="📊 Обновить данные", callback_data="admin_update_stats")]
    ])

    await message.reply(
        f"👑 *Панель управления (SaaS режим)*\n\n"
        f"Зарегистрировано клиентов: `{total_users}`\n"
        f"Всего сообщений в буфере БД: `{total_cached}`",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "admin_list_users", F.from_user.id == SUPER_ADMIN_ID)
async def process_admin_users(callback: Message):
    """Вывод списка пользователей с динамическими кнопками бана"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, status FROM users LIMIT 10")
    users = cursor.fetchall()
    conn.close()

    if not users:
        await callback.answer("База пользователей пуста.")
        return

    text = "👤 *Список последних пользователей (Max 10):*\n\n"
    buttons = []
    
    for uid, name, status in users:
        text += f"• ID: `{uid}` | {name} | Статус: *{status}*\n"
        if status == 'active':
            buttons.append([InlineKeyboardButton(text=f"🚫 Забанить {name}", callback_data=f"block_{uid}")])
        else:
            buttons.append([InlineKeyboardButton(text=f"✅ Разбанить {name}", callback_data=f"unblock_{uid}")])

    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="admin_update_stats")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith(("block_", "unblock_")), F.from_user.id == SUPER_ADMIN_ID)
async def handle_ban_unban(callback: Message):
    """Изменение статуса пользователя в БД"""
    action, target_id = callback.data.split("_")
    new_status = "blocked" if action == "block" else "active"
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET status = ? WHERE user_id = ?", (new_status, int(target_id)))
    
    if new_status == "blocked":
        # Стираем накопленный кэш заблокированного человека
        cursor.execute("DELETE FROM business_cache WHERE business_connection_id = ?", (str(target_id),))
        
    conn.commit()
    conn.close()
    
    await callback.answer(f"Статус пользователя {target_id} изменен на {new_status}")
    await process_admin_users(callback)

@dp.callback_query(F.data == "admin_update_stats", F.from_user.id == SUPER_ADMIN_ID)
async def handle_refresh_stats(callback: Message):
    """Обновление текста главной панели"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM business_cache")
    total_cached = cursor.fetchone()[0]
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_list_users")],
        [InlineKeyboardButton(text="📊 Обновить данные", callback_data="admin_update_stats")]
    ])

    await callback.message.edit_text(
        f"👑 *Панель управления (SaaS режим)*\n\n"
        f"Зарегистрировано клиентов: `{total_users}`\n"
        f"Всего сообщений в буфере БД: `{total_cached}`",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

if __name__ == "__main__":
    logging.info("Бот запущен...")
    dp.run_polling(bot)