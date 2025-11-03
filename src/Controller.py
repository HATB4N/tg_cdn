import asyncio
import os
from typing import Tuple, Optional
from . import SendTgbot
import httpx
from sqlalchemy import select, update, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import NoResultFound
from .db import Session, File, Bot, UrlCache
from datetime import datetime, timedelta

class Con:
    _sbots: list[SendTgbot.Tgbot]

    def __init__(self, sbots):
        self._sbots = sbots
        self._assign_lock = asyncio.Lock()

    async def task(self):
        print('task start')
        temp_dir = "/tmp/tg_img_cdn" # env에서 읽어오게 수정
        # 봇 할당 루프
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
                                target_sbot = min(self._sbots, key=lambda b: (
                                    b._q.qsize() + b.busy, id(b))
                                    )
                                # State 10 for assigned to bot
                                res = await self._update_state(
                                        file_uuid=doc.file_uuid, 
                                        bot_id=target_sbot._bot_id, 
                                        state=10, 
                                        exp_state=[0]
                                        )
                                if res > 0:
                                    temp_path = os.path.join(temp_dir, doc.file_uuid)
                                    await target_sbot.add_file(
                                            file_uuid=doc.file_uuid,
                                            path=temp_path
                                            )
                        except Exception as e:
                            print(f"Error assigning doc {doc.file_uuid}: {e}")

            await asyncio.sleep(5) # Check for new docs every 5 seconds

    async def handle_upload(self, file_uuid: str):
        async with Session() as s:
            async with s.begin():
                new_file = File(
                    file_uuid=file_uuid,
                    state=0 # Initial state for new uploads
                )
                s.add(new_file)

    async def _get_token(self, bot_id: int) -> str | None:
        async with Session() as s:
            result = await s.execute(
                    select(Bot.token).where(Bot.bot_id == bot_id)
                    )
            token = result.scalar_one_or_none()
            return token

    async def get_path(self, file_uuid: str) -> str:
        telegram_file_url = None

        # file_uuid -> file_id, bot_id를 files에서 뽑아옴
        async with Session() as s:
            # check cache
            cache_q = select(UrlCache).where(UrlCache.file_uuid == file_uuid)
            cache_record = (await s.scalars(cache_q)).first()
            if cache_record and (
                    datetime.utcnow() - cache_record.file_path_updated_at < timedelta(hours=1)
                    ):
                token = await self._get_token(cache_record.bot_id)
                if token:
                    telegram_file_url = f'https://api.telegram.org/file/bot{token}/{cache_record.file_path}'
            file_q = select(File).where(File.file_uuid == file_uuid)
            try:
                file_record = (await s.scalars(file_q)).one()
            except NoResultFound:
                return None
            if file_record.file_id is None or file_record.bot_id is None:
                return None
        
            token = await self._get_token(file_record.bot_id)
            if not token:
                raise ValueError(f"Token for bot_id {file_record.bot_id} not found")
            
            file_id = file_record.file_id
            bot_id = file_record.bot_id

            # chech fail, get file_path from tg svr
            if not telegram_file_url:
                async with httpx.AsyncClient() as client:
                    url = f'https://api.telegram.org/bot{token}/getFile'
                    resp = await client.get(url, params={"file_id": file_id})
                    resp.raise_for_status()
                    data = resp.json()
                    file_path = data['result']['file_path']

                if not file_path:
                    return None
                
                telegram_file_url = f'https://api.telegram.org/file/bot{token}/{file_path}'

                # update cache
                upsert_stmt = mysql_insert(UrlCache).values(
                    file_uuid=file_uuid, 
                    file_path=file_path,
                    bot_id=file_record.bot_id,
                    file_path_updated_at=datetime.utcnow()
                )
                upsert_stmt = upsert_stmt.on_duplicate_key_update(
                    file_path=upsert_stmt.inserted.file_path,
                    bot_id=upsert_stmt.inserted.bot_id,
                    file_path_updated_at=upsert_stmt.inserted.file_path_updated_at
                )
                await s.execute(upsert_stmt)
                await s.commit()

        # serve final link
        if not telegram_file_url:
             return None

        return telegram_file_url

    async def _update_state(self, file_uuid: str, bot_id: int, state: int, exp_state: list[int]) -> int:
        """
        if doc.state = exp_state : doc.state == state
        """
        async with Session() as s:
            async with s.begin():
                res = await s.execute(
                    update(File)
                    .where(File.file_uuid == file_uuid, File.state.in_(exp_state))
                    .values(
                        state=state,
                        bot_id=bot_id,
                        updated_at=func.now()
                    )
                )
                return res.rowcount
