import configparser
import importlib
import os
import sqlite3
from pydantic import BaseModel
import asyncio
from typing import Union
import logging
from aiogram import Router, Bot, Dispatcher, F, types
from aiogram.filters import Command
from html import escape
from datetime import datetime, timezone, timedelta
import pytz


config = configparser.ConfigParser()
config.read("config_example.ini")

TOKEN = config["telegram"]["token"].strip('"')
USER_ID = int(config["telegram"]["user_id"].strip('"'))
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

EDITED_MESSAGE_FORMAT = language_module.EDITED_MESSAGE_FORMAT
DELETED_MESSAGE_FORMAT = language_module.DELETED_MESSAGE_FORMAT
NEW_USER_MESSAGE_FORMAT = language_module.NEW_USER_MESSAGE_FORMAT


def dict_factory(cursor, row) -> dict:
    save_dict = {}
    for idx, col in enumerate(cursor.description):
        save_dict[col[0]] = row[idx]
    return save_dict


def update_format(sql, parameters: dict) -> tuple[str, list]:
    values = ", ".join([f"{item} = ?" for item in parameters])
    sql += f" {values}"
    return sql, list(parameters.values())


class MessageRecord(BaseModel):
    user_id: int
    message_id: int
    message_text: str
    timestamp: str


class Messagesx:
    storage_name = "messages"
    PATH_DATABASE = "messages.db"


    @staticmethod
    def create_db():
        with sqlite3.connect(Messagesx.PATH_DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS messages
                              (id INTEGER PRIMARY KEY,
                               user_id INTEGER,
                               message_id INTEGER,
                               message_text TEXT,
                               timestamp TEXT)''')


    @staticmethod
    def add(user_id: int, message_id: int, message_text: str, timestamp: str):
        with sqlite3.connect(Messagesx.PATH_DATABASE) as con:
            con.row_factory = dict_factory
            con.execute(
                f"INSERT INTO {Messagesx.storage_name} (user_id, message_id, message_text, timestamp) VALUES (?, ?, ?, ?)",
                [user_id, message_id, message_text, timestamp],
            )


    @staticmethod
    def get(user_id: int, message_id: int) -> Union[MessageRecord, None]:
        with sqlite3.connect(Messagesx.PATH_DATABASE) as con:
            con.row_factory = dict_factory
            sql = f"SELECT * FROM {Messagesx.storage_name} WHERE user_id = ? AND message_id = ?"
            response = con.execute(sql, [user_id, message_id]).fetchone()
            if response is not None:
                response = MessageRecord(**response)
            return response


    @staticmethod
    def update(user_id: int, message_id: int, **kwargs):
        with sqlite3.connect(Messagesx.PATH_DATABASE) as con:
            con.row_factory = dict_factory
            sql = f"UPDATE {Messagesx.storage_name} SET"
            sql, parameters = update_format(sql, kwargs)
            parameters.extend([user_id, message_id])
            con.execute(sql + " WHERE user_id = ? AND message_id = ?", parameters)


    @staticmethod
    def delete(user_id: int, message_id: int):
        with sqlite3.connect(Messagesx.PATH_DATABASE) as con:
            con.row_factory = dict_factory
            sql = f"DELETE FROM {Messagesx.storage_name} WHERE user_id = ? AND message_id = ?"
            con.execute(sql, [user_id, message_id])


    @staticmethod
    def delete_old_messages(cutoff_timestamp: str):
        with sqlite3.connect(Messagesx.PATH_DATABASE) as con:
            sql = f"DELETE FROM {Messagesx.storage_name} WHERE timestamp < ?"
            con.execute(sql, [cutoff_timestamp])


class UsersDB:
    storage_name = "users"
    PATH_DATABASE = "users.db"


    @staticmethod
    def create_db():
        with sqlite3.connect(UsersDB.PATH_DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS users
                              (user_id INTEGER PRIMARY KEY, user_fullname TEXT)''')


    @staticmethod
    def add(user_id: int, user_fullname: str):
        with sqlite3.connect(UsersDB.PATH_DATABASE) as con:
            con.row_factory = dict_factory
            con.execute(
                f"INSERT INTO {UsersDB.storage_name} (user_id, user_fullname) VALUES (?, ?)",
                [user_id, user_fullname],
            )


    @staticmethod
    def get(user_id: int) -> Union[dict, None]:
        with sqlite3.connect(UsersDB.PATH_DATABASE) as con:
            con.row_factory = dict_factory
            sql = f"SELECT * FROM {UsersDB.storage_name} WHERE user_id = ?"
            response = con.execute(sql, [user_id]).fetchone()
            return response


Messagesx.create_db()

UsersDB.create_db()


async def cleanup_old_messages():
    while True:
        now_local = datetime.now(timezone_local)
        next_run = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        sleep_seconds = (next_run - now_local).total_seconds()
        await asyncio.sleep(sleep_seconds)
        cutoff_datetime = datetime.now(timezone.utc) - timedelta(days=30)
        cutoff_timestamp_iso = cutoff_datetime.isoformat()
        Messagesx.delete_old_messages(cutoff_timestamp_iso)


async def send_msg(message_old: str, message_new: Union[str, None], user_fullname: str, user_id: int, timestamp: str, bot: Bot = None):
    user_fullname_escaped = escape(user_fullname)
    if message_new is None:
        msg = DELETED_MESSAGE_FORMAT.format(user_fullname_escaped=user_fullname_escaped, user_id=user_id, timestamp=timestamp, old_text=message_old)
    else:
        msg = EDITED_MESSAGE_FORMAT.format(user_fullname_escaped=user_fullname_escaped, user_id=user_id, timestamp=timestamp, old_text=message_old, new_text=message_new)
    await bot.send_message(USER_ID, msg, parse_mode='html')


@router.message(Command(commands=["start"]))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    user_fullname_escaped = escape(message.from_user.full_name)
    msg = NEW_USER_MESSAGE_FORMAT.format(user_fullname_escaped=user_fullname_escaped, user_id=user_id)
    await message.answer(msg, parse_mode='html')


@router.edited_business_message()
async def edited_business_message(message: types.Message):
    if message.from_user.id == message.chat.id:
        user_msg = Messagesx.get(user_id=message.from_user.id, message_id=message.message_id)
        if user_msg:
            message_timestamp = datetime.fromisoformat(user_msg.timestamp).astimezone(timezone_local)
            timestamp_formatted = message_timestamp.strftime('%d/%m/%y %H:%M')
            await send_msg(message_old=user_msg.message_text, message_new=message.text, user_fullname=message.from_user.full_name, user_id=message.chat.id, timestamp=timestamp_formatted, bot=message.bot)
            Messagesx.update(user_id=message.from_user.id, message_id=message.message_id, message_text=message.text)


@router.deleted_business_messages()
async def deleted_business_messages(message: types.Message):
    user_id = message.chat.id
    user_fullname = message.chat.full_name
    for msg_id in message.message_ids:
        user_msg = Messagesx.get(user_id=user_id, message_id=msg_id)
        if user_msg:
            message_timestamp = datetime.fromisoformat(user_msg.timestamp).astimezone(timezone_local)
            timestamp_formatted = message_timestamp.strftime('%d/%m/%y %H:%M')
            await send_msg(message_old=user_msg.message_text, message_new=None, user_fullname=user_fullname, user_id=user_id, timestamp=timestamp_formatted, bot=message.bot)
            Messagesx.delete(user_id=user_id, message_id=msg_id)


@router.business_message(F.text)
async def business_message(message: types.Message):
    if message.from_user.id == message.chat.id:
        user_id = message.from_user.id
        user_fullname = message.from_user.full_name
        user_fullname_escaped = escape(user_fullname)
        user_in_db = UsersDB.get(user_id=user_id)
        if user_in_db is None:
            UsersDB.add(user_id=user_id, user_fullname=user_fullname)
            msg = NEW_USER_MESSAGE_FORMAT.format(user_fullname_escaped=user_fullname_escaped, user_id=user_id)
            await message.bot.send_message(USER_ID, msg, parse_mode='html')
        message_datetime_utc = message.date.replace(tzinfo=timezone.utc)
        message_datetime_local = message_datetime_utc.astimezone(timezone_local)
        timestamp_formatted = message_datetime_local.strftime('%d/%m/%y %H:%M')
        timestamp_iso = message_datetime_utc.isoformat()
        Messagesx.add(user_id=user_id, message_id=message.message_id, message_text=message.text, timestamp=timestamp_iso)


async def main() -> None:
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())