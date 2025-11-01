import asyncio
import os
from typing import Tuple, Optional
import SendTgbot
import httpx
import aiohttp
from sqlalchemy import select, update, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import NoResultFound
from db import Session, File, Bot, UrlCache
from datetime import datetime, timedelta

class Con:
    _sbots: list[SendTgbot.Tgbot]

    def __init__(self, sbots):
        self._sbots = sbots
        self._assign_lock = asyncio.Lock()

    async def _get_token(self, bot_id: int) -> str | None:
        async with Session() as s:
            result = await s.execute(
                    select(Bot.token).where(Bot.bot_id == bot_id)
                    )
            token = result.scalar_one_or_none()
            return token

    async def task(self):
        print('task start')
        temp_dir = "/tmp/tg_img_cdn"
        # This loop will periodically check for new documents and assign them to bots.
        while True:
            async with Session() as s:
                async with s.begin():
                    q = (
                        select(File)
                        .where(File.state == 0) # State 0 for new uploads
                        .order_by(File.created_at.asc())
                        .limit(10) # Process 10 at a time
                    )
                    docs = (await s.scalars(q)).all()
                    for doc in docs:
                        try:
                            async with self._assign_lock:
                                target_sbot = min(self._sbots, key=lambda b: (b._q.qsize() + b.busy, id(b)))
                                # State 10 for assigned to bot
                                res = await self.update_state(file_id=doc.file_id, bot_id=target_sbot._bot_id, state=10, exp_state=[0])
                                if res > 0:
                                    file_path = os.path.join(temp_dir, doc.file_id)
                                    await target_sbot.add_file(path=file_path, file_id=doc.file_id, title=doc.file_id)
                        except Exception as e:
                            print(f"Error assigning doc {doc.file_id}: {e}")

            await asyncio.sleep(5) # Check for new docs every 5 seconds

    async def handle_upload(self, file_id: str):
        """
        Handles a new file upload from the API.
        Creates a new File record.
        """
        async with Session() as s:
            async with s.begin():
                new_file = File(
                    file_id=file_id,
                    state=0 # Initial state for new uploads
                )
                s.add(new_file)

    async def get_path(self, file_id: str) -> str:
        telegram_file_url = None
        async with Session() as s:
            # 1. Check cache
            cache_q = select(UrlCache).where(UrlCache.file_id == file_id)
            cache_record = (await s.scalars(cache_q)).first()
            if cache_record and (datetime.utcnow() - cache_record.url_updated_at < timedelta(hours=1)):
                token = await self._get_token(cache_record.bot_id)
                if token:
                    telegram_file_url = f'https://api.telegram.org/file/bot{token}/{cache_record.tg_file_path}'

            # 2. If cache is invalid/missing, get new URL
            if not telegram_file_url:
                file_q = select(File).where(File.file_id == file_id)
                try:
                    file_record = (await s.scalars(file_q)).one()
                except NoResultFound:
                    return None

                if file_record.msg_id is None or file_record.bot_id is None:
                    return None

                token = await self._get_token(file_record.bot_id)
                if not token:
                    raise ValueError(f"Token for bot_id {file_record.bot_id} not found")

                tg_file_path = await self._api_get(file_record.bot_id, file_record.msg_id, token)
                if not tg_file_path:
                    return None
                
                telegram_file_url = f'https://api.telegram.org/file/bot{token}/{tg_file_path}'

                # 3. Update cache (UPSERT)
                upsert_stmt = mysql_insert(UrlCache).values(
                    file_id=file_id, 
                    tg_file_path=tg_file_path,
                    bot_id=file_record.bot_id,
                    url_updated_at=datetime.utcnow()
                )
                upsert_stmt = upsert_stmt.on_duplicate_key_update(
                    tg_file_path=upsert_stmt.inserted.tg_file_path,
                    bot_id=upsert_stmt.inserted.bot_id,
                    url_updated_at=upsert_stmt.inserted.url_updated_at
                )
                await s.execute(upsert_stmt)
                await s.commit()

        # 4. Download the file
        if not telegram_file_url:
             return None

        return telegram_file_url

    async def update_state(self, file_id: str, bot_id: int, state: int, exp_state: list[int]) -> int:
        """
        if doc.state = exp_state : doc.state == state
        """
        async with Session() as s:
            async with s.begin():
                res = await s.execute(
                    update(File)
                    .where(File.file_id == file_id, File.state.in_(exp_state))
                    .values(
                        state=state,
                        bot_id=bot_id,
                        updated_at=func.now()
                    )
                )
                return res.rowcount

    async def _api_get(self, bot_id, msg_id, token):
        _target = next((bot for bot in self._sbots if bot._bot_id == int(bot_id)), None)
        if _target:
            # This renews the file link, which is valid for at least 1 hour
            tg_file_id = await _target._renew_msg(msg_id)
            async with httpx.AsyncClient() as client:
                url = f'https://api.telegram.org/bot{token}/getFile'
                resp = await client.get(url, params={"file_id": tg_file_id})
                resp.raise_for_status()
                data = resp.json()
                file_path = data['result']['file_path']
                return file_path
        else:
            print('dbg: return none in Controller')
            return None
