import asyncio
import logging
import os
import requests

from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pyrogram import Client, filters, raw
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "sniper")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 8))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

CHANNEL_ID_FILE = "channel_id.txt"

snipe_task: asyncio.Task | None = None
target_username: str | None = None
is_running: bool = False

SESSION_STRING = os.getenv("SESSION_STRING")  # <- сюда вставляем session string или bot token
app: Client = Client(
    name=SESSION_STRING or SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING  # <- это важно, чтобы не спрашивало ввод
)


def ts():
    return datetime.now().strftime("%H:%M:%S")


def load_channel_id() -> int | None:
    if Path(CHANNEL_ID_FILE).exists():
        return int(Path(CHANNEL_ID_FILE).read_text().strip())
    return None


def save_channel_id(channel_id: int):
    Path(CHANNEL_ID_FILE).write_text(str(channel_id))


async def ensure_channel() -> int:
    channel_id = load_channel_id()
    if channel_id:
        print(f"[{ts()}] Канал уже существует: {channel_id}")
        return channel_id
    print(f"[{ts()}] Создаём канал...")
    chat = await app.create_channel("Sniper Channel", "Username sniper reserve channel")
    save_channel_id(chat.id)
    print(f"[{ts()}] Канал создан: {chat.id}")
    return chat.id


def check_fragment(username: str) -> str:
    try:
        r = requests.get(
            f"https://fragment.com/?query={username}",
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        soup = BeautifulSoup(r.text, "html.parser")
        status_tag = soup.find(class_=lambda c: c and "tm-status" in c)
        if status_tag:
            classes = " ".join(status_tag.get("class", []))
            if "tm-status-unavail" in classes:
                return "available"
            return "unavailable"
        return "available"
    except Exception as e:
        print(f"[{ts()}] Ошибка запроса: {e}")
        return "error"


async def snipe_loop(username: str, channel_id: int):
    global is_running, target_username, snipe_task
    is_running = True
    peer = await app.resolve_peer(channel_id)
    while True:
        status = check_fragment(username)
        if status == "unavailable":
            print(f"[{ts()}] @{username} — занят")
        elif status == "error":
            print(f"[{ts()}] @{username} — ошибка сети, повтор...")
        else:
            print(f"[{ts()}] @{username} — СВОБОДЕН! Claiming...")
            while True:
                try:
                    await app.invoke(raw.functions.channels.UpdateUsername(
                        channel=peer,
                        username=username,
                    ))
                    await app.send_message(
                        ADMIN_CHAT_ID,
                        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>@{username} успешно захвачен!</b>',
                    )
                    print(f"[{ts()}] @{username} — захват успешен!")
                    is_running = False
                    target_username = None
                    snipe_task = None
                    return
                except Exception as e:
                    err = str(e)
                    if "USERNAME_OCCUPIED" in err:
                        print(f"[{ts()}] @{username} — USERNAME_OCCUPIED, выходим...")
                        is_running = False
                        target_username = None
                        snipe_task = None
                        return
                    elif "FLOOD_WAIT" in err:
                        try:
                            wait = int(err.split("wait_")[1].split(" ")[0])
                        except Exception:
                            wait = 15
                        print(f"[{ts()}] @{username} — FLOOD_WAIT {wait}с, ждём и ретраим...")
                        await asyncio.sleep(wait)
                    else:
                        print(f"[{ts()}] @{username} — ошибка установки: {e}")
                        await app.send_message(
                            ADMIN_CHAT_ID,
                            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Ошибка захвата @{username}:</b> <code>{e}</code>',
                        )
                        is_running = False
                        target_username = None
                        snipe_task = None
                        return
        await asyncio.sleep(CHECK_INTERVAL)
    is_running = False
    target_username = None
    snipe_task = None


async def cmd_snipe(client: Client, message: Message):
    global snipe_task, target_username, is_running

    if message.from_user.id != ADMIN_CHAT_ID:
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply(
            '<tg-emoji emoji-id="6028435952299413210">ℹ️</tg-emoji> <b>Использование:</b> <code>/snipe username</code>'
        )
        return

    username = args[1].lstrip("@")

    if is_running and target_username:
        await message.reply(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Уже снайплю <b>@{target_username}</b>. Отправь /stop для отмены.'
        )
        return

    channel_id = load_channel_id()
    if not channel_id:
        await message.reply('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Канал не найден.')
        return

    target_username = username
    snipe_task = asyncio.get_event_loop().create_task(snipe_loop(username, channel_id))

    await message.reply(
        f'<tg-emoji emoji-id="5870676941614354370">🎯</tg-emoji> Начинаю снайпить <b>@{username}</b>'
    )


async def cmd_stop(client: Client, message: Message):
    global snipe_task, is_running, target_username

    if message.from_user.id != ADMIN_CHAT_ID:
        return

    if not is_running or snipe_task is None:
        await message.reply('<tg-emoji emoji-id="6028435952299413210">ℹ️</tg-emoji> Снайпинг не запущен.')
        return

    snipe_task.cancel()
    snipe_task = None
    is_running = False
    stopped = target_username
    target_username = None

    await message.reply(
        f'<tg-emoji emoji-id="5870657884844462243">⛔️</tg-emoji> Снайпинг <b>@{stopped}</b> остановлен.'
    )


async def cmd_status(client: Client, message: Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return

    if is_running and target_username:
        text = (
            f'<tg-emoji emoji-id="5870921681735781843">📊</tg-emoji> <b>Статус:</b> активен\n'
            f'<tg-emoji emoji-id="5870676941614354370">🎯</tg-emoji> <b>Цель:</b> @{target_username}'
        )
    else:
        text = f'<tg-emoji emoji-id="5870921681735781843">📊</tg-emoji> <b>Статус:</b> неактивен'
    await message.reply(text)


async def main():
    global app

    app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

    app.add_handler(MessageHandler(cmd_snipe, filters.command("snipe") & filters.private))
    app.add_handler(MessageHandler(cmd_stop, filters.command("stop") & filters.private))
    app.add_handler(MessageHandler(cmd_status, filters.command("status") & filters.private))

    async with app:
        await ensure_channel()
        print(f"[{ts()}] Юзербот запущен. Жду команды в личку...")
        await asyncio.Event().wait()


asyncio.run(main())
