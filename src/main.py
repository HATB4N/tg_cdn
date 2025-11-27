import os
import signal
import asyncio
import contextlib
import aiomysql
from contextlib import asynccontextmanager
from . import Controller
from . import SendTgbot
from .api import create_app
from . import db
from .worker import DBWorker
import httpx

@asynccontextmanager
async def lifespan(app: create_app):
    sbot_chat_id = os.getenv("SENDBOT_CHAT_ID")
    sbot_tokens_str = os.getenv("SENDBOT_TOKENS")

    if not sbot_chat_id or not sbot_tokens_str:
        print("SENDBOT_CHAT_ID and SENDBOT_TOKENS environment variables must be set.")
        exit()

    sbot_tokens = [t.strip() for t in sbot_tokens_str.split(',')]

    async def bootstrap_db(max_try=20, delay=1.5):
        for i in range(max_try):
            try:
                await db.init_models()
                print("[db] models ready")
                return
            except Exception as e:
                print(f"[db] not ready ({i+1}/{max_try}): {e}")
                await asyncio.sleep(delay)
        raise RuntimeError("DB init failed")

    try:
        await db.init_db_pool() # 커낵션
        await bootstrap_db() # create table
    except Exception as e:
        print(f"CRITICAL: Failed to initialize database: {e}")
        exit()

    db_worker_instance = DBWorker()
    db_worker_task = asyncio.create_task(db_worker_instance.run())

    bot_records = await asyncio.gather(*(get_or_create_bot(token) for token in sbot_tokens))

    sbots = [SendTgbot.Tgbot(bot_id=bot['bot_id'], token=bot['bot_token'], chat_id=int(sbot_chat_id)) for bot in bot_records]
    apps = [b.build() for b in sbots]

    http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            timeout=httpx.Timeout(30.0, connect=5.0)
            )

    ctr = Controller.Con(
        sbots=sbots,
        db_queue=db_worker_instance.queue,
        http_client=http_client
        )
    app.state.controller = ctr
    controller_task = asyncio.create_task(ctr.task())
    app.state.http_client = http_client

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

        db_worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await db_worker_task
        
        await db.close_db_pool()


async def get_or_create_bot(token: str) -> dict:
    if not db.pool:
        raise RuntimeError("Database pool is not initialized.")

    async with db.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            
            # id -> token: static & unique
            await cursor.execute("SELECT * FROM bots WHERE bot_token = %s", (token,))
            bot = await cursor.fetchone()
            
            if bot:
                print(f"Found existing bot with ID: {bot['bot_id']} for token.")
                return bot
            else:
                print(f"Creating new bot for token.")
                await cursor.execute("INSERT INTO bots (bot_token) VALUES (%s)", (token,))
                
                new_bot_id = cursor.lastrowid
                print(f"Created new bot with ID: {new_bot_id}")
                
                return {"bot_id": new_bot_id, "bot_token": token}

app = create_app(None)
app.router.lifespan_context = lifespan
