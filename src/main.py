import os
import signal
import asyncio
import contextlib
from contextlib import asynccontextmanager
from . import Controller
from . import SendTgbot
from .api import create_app
from .db import init_models, Session, Bot, select

@asynccontextmanager
async def lifespan(app: create_app):
    sbot_chat_id = os.getenv("SENDBOT_CHAT_ID")
    sbot_tokens_str = os.getenv("SENDBOT_TOKENS")

    if not sbot_chat_id or not sbot_tokens_str:
        print("SENDBOT_CHAT_ID and SENDBOT_TOKENS environment variables must be set.")
        exit()

    sbot_tokens = [t.strip() for t in sbot_tokens_str.split(',')]

    await init_models()
    bot_records = await asyncio.gather(*(get_or_create_bot(token) for token in sbot_tokens))

    sbots = [SendTgbot.Tgbot(bot_id=bot.bot_id, token=bot.token, chat_id=int(sbot_chat_id)) for bot in bot_records]
    apps = [b.build() for b in sbots]

    async def bootstrap_db(max_try=20, delay=1.5):
        for i in range(max_try):
            try:
                await init_models()
                print("[db] models ready")
                return
            except Exception as e:
                print(f"[db] not ready ({i+1}/{max_try}): {e}")
                await asyncio.sleep(delay)
        raise RuntimeError("DB init failed")

    await bootstrap_db()

    ctr = Controller.Con(sbots=sbots)
    app.state.controller = ctr
    controller_task = asyncio.create_task(ctr.task())

    await asyncio.gather(*(app.initialize() for app in apps))
    await asyncio.gather(*(app.start() for app in apps))
    await asyncio.gather(*(b.start_background() for b in sbots))

    try:
        yield
    finally:
        await asyncio.gather(*(b.stop_background() for b in sbots))
        await asyncio.gather(*(app.stop() for app in reversed(apps)))
        await asyncio.gather(*(app.shutdown() for app in reversed(apps)))
        controller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await controller_task

async def get_or_create_bot(token: str) -> Bot:
    async with Session() as s:
        async with s.begin():
            result = await s.execute(select(Bot).where(Bot.token == token))
            bot = result.scalar_one_or_none()
            if bot:
                print(f"Found existing bot with ID: {bot.bot_id} for token.")
                return bot
            else:
                print(f"Creating new bot for token.")
                new_bot = Bot(token=token)
                s.add(new_bot)
                await s.flush()
                print(f"Created new bot with ID: {new_bot.bot_id}")
                return new_bot

app = create_app(None)
app.router.lifespan_context = lifespan
