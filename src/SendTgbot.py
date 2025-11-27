import asyncio
from telegram import Bot
from telegram.ext import ApplicationBuilder
from telegram.error import RetryAfter
import aiomysql
from . import db
import uuid
import os

class Tgbot:
    _bot_id: int
    _token: str
    _chat_id: str
    _worker_task: asyncio.Task | None
    len_q: int
    busy: bool
    batch_size: int
    MAX_FLOOD_RETRIES = 5

    def __init__(self, bot_id: int, token: str, chat_id: str, batch_size: int = 10):
        self._bot_id = bot_id
        self._token = token
        self._chat_id = chat_id
        self._worker_task = None
        self.batch_size = batch_size
        self.busy = False 
    
    async def _queue_worker(self):
        print(f"sbot[{self._bot_id}]: _queue_worker started.")
        try:
            while True:
                claimed_jobs = await self._fetch_and_claim_jobs()

                if not claimed_jobs:
                    await asyncio.sleep(5)
                    continue

                for job in claimed_jobs:
                    _file_uuid_bytes = job['file_uuid']
                    _file_uuid_str = str(uuid.UUID(bytes=_file_uuid_bytes))
                    _path = f"./tmp/{_file_uuid_str}"
                    try:
                        self.busy = True 
                        
                        # state 10 -> 20 (Upload Started)
                        await self._update_state(
                                file_uuid=_file_uuid_bytes, 
                                state=20, 
                                exp_state=[10])
                        
                        _msg_id, _file_id = await self._send_file(path = _path, caption = _file_uuid_str)
                        
                        # state 20 -> 30 (Upload Finished, wait for commit)
                        await self._update_state(
                                file_uuid=_file_uuid_bytes,
                                state=30,
                                exp_state=[20])

                        ok = await self._write_index(
                            file_uuid=_file_uuid_bytes, 
                            msg_id=_msg_id, 
                            file_id = _file_id
                            )

                        if ok:
                            try:
                                # gc loop state 30 redo에서도 진행해야 함.
                                # committed state = 40 <-> do not req any other actions
                                os.remove(_path)
                            except OSError as e:
                                print(f"sbot[{self._bot_id}]: Error deleting temp file {_path}: {e}")

                    except Exception as e:
                        print(f"sbot[{self._bot_id}]: Error processing file {_file_uuid_str}: {e}")
                        try:
                            await self._mark_fail(_file_uuid_bytes, str(e))
                            print(f"sbot[{self._bot_id}]: File {_file_uuid_str} marked as failed.")
                        except Exception as e2:
                            print(f"sbot[{self._bot_id}]: fail mark error:", e2)
                    finally:
                        self.busy = False  

        except asyncio.CancelledError:
            print(f"sbot[{self._bot_id}]: _queue_worker cancelled.")
            pass
    
    async def _send_file(self, path: str, caption: str):
        bot = self._app.bot
        for attempt in range(self.MAX_FLOOD_RETRIES):
            try:
                with open(path, "rb") as f:
                    msg = await bot.send_document(
                            chat_id=self._chat_id,
                            document = f,
                            caption = caption,
                            read_timeout = 60,
                            write_timeout = 60,
                            connect_timeout = 60
                            )
                return msg.message_id, msg.document.file_id
            except RetryAfter as e:
                print(f"sbot[{self._bot_id}]: Flood control exceeded for file {path}. Waiting for {e.retry_after}s. Attempt {attempt+1}/{self.MAX_FLOOD_RETRIES}")
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                # 스코프에서 버려 ㅇㅇ
                raise e
        
        raise Exception(f"Failed to send file {path} after {self.MAX_FLOOD_RETRIES} flood control retries.")
        
    async def _fetch_and_claim_jobs(self) -> list[dict]:
        if not db.pool:
            raise RuntimeError("Database pool is not initialized.")
        
        async with db.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await conn.begin()
                try:
                    await cursor.execute(
                        """
                        SELECT file_uuid
                        FROM queues
                        WHERE state = 0 AND available_at <= NOW()
                        ORDER BY created_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                        """,
                        (self.batch_size,)
                    )
                    jobs_to_claim = await cursor.fetchall()
                    
                    if not jobs_to_claim:
                        await conn.commit()
                        return []

                    job_uuids = [job['file_uuid'] for job in jobs_to_claim]
                    placeholders = ', '.join(['%s'] * len(job_uuids))
                    sql = f"""
                        UPDATE queues
                        SET state = 10, bot_id = %s, updated_at = NOW()
                        WHERE file_uuid IN ({placeholders})
                        """
                    
                    params = (self._bot_id,) + tuple(job_uuids)
                    await cursor.execute(sql, params)
                    
                    await conn.commit()
                    return jobs_to_claim

                except Exception as e:
                    await conn.rollback()
                    print(f"sbot[{self._bot_id}]: Error claiming jobs: {e}")
                    return []
    
    async def _write_index(self, file_uuid: bytes, msg_id: int, file_id: str) -> bool:
        if not db.pool: raise RuntimeError("Database pool is not initialized.")
        
        async with db.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await conn.begin() # Start transaction
                try:
                    # 1. INSERT into files table
                    await cursor.execute(
                        """
                        INSERT INTO files (file_uuid, file_id, msg_id, bot_id)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (file_uuid, file_id, msg_id, self._bot_id)
                    )

                    # 2. UPDATE queues table
                    await cursor.execute(
                        """
                        UPDATE queues
                        SET state = 40, updated_at = NOW()
                        WHERE file_uuid = %s AND state = 30
                        """,
                        (file_uuid,)
                    )
                    
                    await conn.commit() # Commit transaction
                    return True

                except Exception as e:
                    await conn.rollback() # Rollback on error
                    print(f"sbot[{self._bot_id}]: _write_index transaction error: {e}")
                    return False

    async def _mark_fail(self, file_uuid: bytes, err: str) -> int:
        if not db.pool: raise RuntimeError("Database pool is not initialized.")
        
        async with db.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(
                        """
                        UPDATE queues
                        SET state = 100, updated_at = NOW()
                        WHERE file_uuid = %s AND state IN (10, 20, 30)
                        """, 
                        (file_uuid,)
                    )
                    await conn.commit()
                    return cursor.rowcount
                except Exception as e:
                    print(f"sbot[{self._bot_id}]: _mark_fail error: {e}")
                    return 0

    async def _update_state(self, file_uuid: bytes, state: int, exp_state: list[int]) -> int:
        if not db.pool:
            raise RuntimeError("Database pool is not initialized.")

        async with db.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    exp_state_placeholders = ', '.join(['%s'] * len(exp_state))
                    
                    query = f"""
                        UPDATE queues
                        SET state = %s, bot_id = %s, updated_at = NOW()
                        WHERE file_uuid = %s AND state IN ({exp_state_placeholders})
                    """
                    params = (state, self._bot_id, file_uuid, *exp_state)
                    
                    await cursor.execute(query, params)
                    await conn.commit()
                    return cursor.rowcount
                except Exception as e:
                    await conn.rollback()
                    print(f"sbot[{self._bot_id}]: _update_state error: {e}")
                    return 0            
    def build(self):
        app = ApplicationBuilder().token(self._token).build()
        self._app = app
        return app

    async def start_background(self):
        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._queue_worker())

    async def stop_background(self):
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None