import configparser
import importlib
import os
import sqlite3
import asyncio
import logging
from html import escape
from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta
import pytz
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

config = configparser.ConfigParser()
config.read("config.ini")

SUPER_ADMIN_ID = int(config["telegram"]["super_admin_id"].strip('"'))
TOKEN = config["telegram"]["token"].strip('"')
TIMEZONE_NAME = config["timezone"]["name"].strip('"')
timezone_local = pytz.timezone(TIMEZONE_NAME)
LANGUAGE = config["settings"]["language"].strip('"')

try:
    language_module = importlib.import_module(f"languages.{LANGUAGE}")
except ImportError:
    raise ImportError(f"Language module for '{LANGUAGE}' not found.")

router = Router(name=__name__)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "bot_data.db")

EDITED_MESSAGE_FORMAT = language_module.EDITED_MESSAGE_FORMAT
DELETED_MESSAGE_FORMAT = language_module.DELETED_MESSAGE_FORMAT
NEW_USER_MESSAGE_FORMAT = language_module.NEW_USER_MESSAGE_FORMAT


class MessageRecord(BaseModel):
    business_connection_id: int
    chat_id: int
    message_id: int
    message_text: str
    timestamp: str


class UserRecord(BaseModel):
    user_id: int
    username: str
    status: str
    registered_at: str


class Database:
    def __init__(self, path: str):
        self.path = path
        self.create_tables()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_tables(self) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    status TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    business_connection_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    message_text TEXT,
                    timestamp TEXT,
                    PRIMARY KEY (business_connection_id, chat_id, message_id)
                )
                """
            )
            conn.commit()

    def add_user(self, user_id: int, username: str, status: str = "active") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (user_id, username, status, registered_at) VALUES (?, ?, ?, ?)",
                [user_id, username, status, datetime.now(timezone.utc).isoformat()],
            )
            conn.commit()

    def get_user(self, user_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            sql = "SELECT * FROM users WHERE user_id = ?"
            row = conn.execute(sql, [user_id]).fetchone()
            return dict(row) if row else None

    def update_user_status(self, user_id: int, status: str) -> bool:
        with self._connect() as conn:
            sql = "UPDATE users SET status = ? WHERE user_id = ?"
            cursor = conn.execute(sql, [status, user_id])
            conn.commit()
            return cursor.rowcount > 0

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            return int(row["cnt"])

    def count_messages(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()
            return int(row["cnt"])

    def delete_messages_for_user(self, business_connection_id: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE business_connection_id = ?",
                [business_connection_id],
            )
            conn.commit()
            return cursor.rowcount

    def add_message(
        self,
        business_connection_id: int,
        chat_id: int,
        message_id: int,
        message_text: str,
        timestamp: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO messages (business_connection_id, chat_id, message_id, message_text, timestamp) VALUES (?, ?, ?, ?, ?)",
                [business_connection_id, chat_id, message_id, message_text, timestamp],
            )
            conn.commit()

    def get_message(
        self,
        business_connection_id: int,
        chat_id: int,
        message_id: int,
    ) -> Optional[Dict]:
        with self._connect() as conn:
            sql = "SELECT * FROM messages WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?"
            row = conn.execute(sql, [business_connection_id, chat_id, message_id]).fetchone()
            return dict(row) if row else None

    def update_message(
        self,
        business_connection_id: int,
        chat_id: int,
        message_id: int,
        message_text: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET message_text = ? WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?",
                [message_text, business_connection_id, chat_id, message_id],
            )
            conn.commit()

    def delete_message(
        self,
        business_connection_id: int,
        chat_id: int,
        message_id: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?",
                [business_connection_id, chat_id, message_id],
            )
            conn.commit()

    def delete_old_messages(self, cutoff_timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE timestamp < ?",
                [cutoff_timestamp],
            )
            conn.commit()

    def get_active_users(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT user_id, username FROM users WHERE status = 'active'").fetchall()
            return [dict(row) for row in rows]


db = Database(DATABASE_PATH)


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="Заблокировать пользователя", callback_data="admin_ban")],
            [InlineKeyboardButton(text="Разблокировать пользователя", callback_data="admin_unban")],
            [InlineKeyboardButton(text="Рассылка", callback_data="admin_broadcast")],
        ]
    )


async def send_report(bot: Bot, business_connection_id: int, text: str) -> None:
    try:
        await bot.send_message(business_connection_id, text, parse_mode="html")
    except Exception as error:
        logger.warning(
            "Не удалось доставить отчет пользователю %s: %s. Переводим статус в 'inactive'.",
            business_connection_id,
            error,
        )
        db.update_user_status(business_connection_id, "inactive")


async def cleanup_old_messages() -> None:
    while True:
        now_local = datetime.now(timezone_local)
        next_run = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        await asyncio.sleep((next_run - now_local).total_seconds())
        cutoff_timestamp_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        db.delete_old_messages(cutoff_timestamp_iso)
        logger.info("Удалены сообщения старше 30 дней")


@router.message(Command(commands=["start"]))
async def start_command(message: types.Message) -> None:
    user_id = message.from_user.id
    user_fullname_escaped = escape(message.from_user.full_name)
    msg = NEW_USER_MESSAGE_FORMAT.format(user_fullname_escaped=user_fullname_escaped, user_id=user_id)
    await message.answer(msg, parse_mode="html")
    # Автосохранение пользователя при первом запуске
    try:
        username = message.from_user.username or message.from_user.full_name
    except Exception:
        username = str(user_id)
    db.add_user(user_id, username)


@router.message(Command(commands=["admin"]), F.from_user.id == SUPER_ADMIN_ID)
async def admin_command(message: types.Message) -> None:
    await message.answer("Админ-панель. Выберите действие:", reply_markup=build_admin_menu())


@router.callback_query(F.data == "admin_stats", F.from_user.id == SUPER_ADMIN_ID)
async def admin_stats_callback(query: CallbackQuery) -> None:
    user_count = db.count_users()
    message_count = db.count_messages()
    text = f"Пользователей в системе: {user_count}\nЗакэшировано сообщений: {message_count}"
    await query.answer()
    await query.message.edit_text(text)


@router.callback_query(F.data == "admin_ban", F.from_user.id == SUPER_ADMIN_ID)
async def admin_ban_callback(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer("Используйте команду /ban <user_id> для блокировки пользователя.")


@router.callback_query(F.data == "admin_unban", F.from_user.id == SUPER_ADMIN_ID)
async def admin_unban_callback(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer("Используйте команду /unban <user_id> для разблокировки пользователя.")


@router.callback_query(F.data == "admin_broadcast", F.from_user.id == SUPER_ADMIN_ID)
async def admin_broadcast_callback(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer("Используйте команду /broadcast <текст> для отправки сообщения всем активным пользователям.")


@router.message(Command(commands=["ban"]), F.from_user.id == SUPER_ADMIN_ID)
async def ban_command(message: types.Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Неверный формат. Пример: /ban 123456789")
        return

    target_id = int(args[1])
    if not db.update_user_status(target_id, "blocked"):
        await message.answer(f"Пользователь с ID {target_id} не найден в системе.")
        return

    deleted_messages = db.delete_messages_for_user(target_id)
    await message.answer(
        f"Пользователь {target_id} заблокирован. Удалено {deleted_messages} сообщений из кэша."
    )


@router.message(Command(commands=["unban"]), F.from_user.id == SUPER_ADMIN_ID)
async def unban_command(message: types.Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Неверный формат. Пример: /unban 123456789")
        return

    target_id = int(args[1])
    if not db.update_user_status(target_id, "active"):
        await message.answer(f"Пользователь с ID {target_id} не найден в системе.")
        return

    await message.answer(f"Пользователь {target_id} разблокирован и может снова отправлять события.")


@router.message(Command(commands=["broadcast"]), F.from_user.id == SUPER_ADMIN_ID)
async def broadcast_command(message: types.Message) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Неверный формат. Пример: /broadcast Текст сообщения")
        return

    broadcast_text = parts[1].strip()
    active_users = db.get_active_users()

    if not active_users:
        await message.answer("Нет активных пользователей для рассылки.")
        return

    sent = 0
    failed = 0
    for row in active_users:
        target_id = row["user_id"]
        try:
            await message.bot.send_message(target_id, broadcast_text)
            sent += 1
        except Exception as error:
            logger.warning("Рассылка: не удалось доставить сообщение %s: %s", target_id, error)
            db.update_user_status(target_id, "inactive")
            failed += 1

    await message.answer(f"Рассылка отправлена. Успешно: {sent}, не доставлено: {failed}.")


async def process_business_message(message: types.Message) -> None:
    business_connection_id = getattr(message, "business_connection_id", None)
    if business_connection_id is None:
        logger.warning("Отсутствует business_connection_id в событии бизнес-сообщения.")
        return

    # Если нет записи о владельце (business_connection_id), создаём её автоматически
    user_record = db.get_user(business_connection_id)
    if not user_record:
        candidate_name = (
            getattr(message.from_user, 'username', None)
            or getattr(message.from_user, 'full_name', None)
            or getattr(message.chat, 'username', None)
            or getattr(message.chat, 'full_name', None)
            or str(business_connection_id)
        )
        db.add_user(business_connection_id, candidate_name)
        user_record = db.get_user(business_connection_id)

    if not user_record or user_record["status"] != "active":
        return

    timestamp_iso = message.date.replace(tzinfo=timezone.utc).isoformat()
    db.add_message(
        business_connection_id=business_connection_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        message_text=message.text,
        timestamp=timestamp_iso,
    )


async def process_edited_business_message(message: types.Message) -> None:
    business_connection_id = getattr(message, "business_connection_id", None)
    if business_connection_id is None:
        logger.warning("Отсутствует business_connection_id в событии редактирования.")
        return

    user_record = db.get_user(business_connection_id)
    if not user_record or user_record["status"] != "active":
        return

    cached = db.get_message(business_connection_id, message.chat.id, message.message_id)
    if not cached:
        return

    message_timestamp = datetime.fromisoformat(cached["timestamp"]).astimezone(timezone_local)
    timestamp_formatted = message_timestamp.strftime("%d/%m/%y %H:%M")
    text = EDITED_MESSAGE_FORMAT.format(
        user_fullname_escaped=escape(message.from_user.full_name),
        user_id=business_connection_id,
        timestamp=timestamp_formatted,
        old_text=cached["message_text"],
        new_text=message.text,
    )
    await send_report(message.bot, business_connection_id, text)
    db.update_message(
        business_connection_id=business_connection_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        message_text=message.text,
    )


async def process_deleted_business_messages(message: types.Message) -> None:
    business_connection_id = getattr(message, "business_connection_id", None)
    if business_connection_id is None:
        logger.warning("Отсутствует business_connection_id в событии удаления.")
        return

    user_record = db.get_user(business_connection_id)
    if not user_record or user_record["status"] != "active":
        return

    for message_id in message.message_ids:
        cached = db.get_message(business_connection_id, message.chat.id, message_id)
        if not cached:
            continue

        message_timestamp = datetime.fromisoformat(cached["timestamp"]).astimezone(timezone_local)
        timestamp_formatted = message_timestamp.strftime("%d/%m/%y %H:%M")
        text = DELETED_MESSAGE_FORMAT.format(
            user_fullname_escaped=escape(message.chat.full_name),
            user_id=business_connection_id,
            timestamp=timestamp_formatted,
            old_text=cached["message_text"],
        )
        await send_report(message.bot, business_connection_id, text)
        db.delete_message(business_connection_id, message.chat.id, message_id)


@router.business_message(F.text)
async def business_message(message: types.Message) -> None:
    await process_business_message(message)


@router.edited_business_message()
async def edited_business_message(message: types.Message) -> None:
    await process_edited_business_message(message)


@router.deleted_business_messages()
async def deleted_business_messages(message: types.Message) -> None:
    await process_deleted_business_messages(message)


async def main() -> None:
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(cleanup_old_messages())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
