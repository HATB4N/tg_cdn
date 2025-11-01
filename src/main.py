import os
import signal
import asyncio
import contextlib
import Controller
import SendTgbot
from api import create_app
from hypercorn.config import Config
from hypercorn.asyncio import serve
from db import init_models, Session, Bot, select

async def get_or_create_bot(token: str) -> Bot:
    async with Session() as s:
        async with s.begin():
            # Try to find the bot by token
            result = await s.execute(select(Bot).where(Bot.token == token))
            bot = result.scalar_one_or_none()

            if bot:
                print(f"Found existing bot with ID: {bot.bot_id} for token.")
                return bot
            else:
                # If not found, create a new one
                print(f"Creating new bot for token.")
                new_bot = Bot(token=token)
                s.add(new_bot)
                await s.flush() # Flush to get the auto-incremented ID
                print(f"Created new bot with ID: {new_bot.bot_id}")
                return new_bot

async def main():
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

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    ctr = Controller.Con(sbots = sbots)
    controller_task = asyncio.create_task(ctr.task())

    quart_app = create_app(ctr)
    config = Config()
    config.bind = ["0.0.0.0:3000"]
    quart_task = asyncio.create_task(serve(quart_app, config))

    await asyncio.gather(*(app.initialize() for app in apps))
    await asyncio.gather(*(app.start() for app in apps))

    await asyncio.gather(*(b.start_background() for b in sbots))

    try:
        await stop_event.wait()
    finally:
        await asyncio.gather(*(b.stop_background() for b in sbots))
        await asyncio.gather(*(app.stop() for app in reversed(apps)))
        await asyncio.gather(*(app.shutdown() for app in reversed(apps)))
        controller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await controller_task
        quart_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await quart_task

if __name__ == "__main__":
    asyncio.run(main())

