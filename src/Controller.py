import asyncio
import os
from typing import Tuple, Optional
from . import SendTgbot
import httpx
import aiomysql
from . import db
from datetime import datetime, timedelta
import redis.asyncio as redis
import uuid

class Con:
    _sbots: list[SendTgbot.Tgbot]
    MIN_JITTER_VALUE = 1
    MAX_JITTER_VALUE = 5
    # MAX_RETRY = 10 
    # TODO
    # (이거 넘으면 cnt = 999등으로 버리거나, 갱신 시간 늘리지 않도록)
    # 일단은 LEAST(POW(2, retry_count), 3000) 정도?

    def __init__(self, sbots, db_queue: asyncio.Queue, http_client: httpx.AsyncClient):
        self._sbots = sbots
        self._redis = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
        self._db_queue = db_queue
        self._http_client = http_client

    async def task(self):
        # enum state descriptions
        # state = 0: Init state
        # state = 10: Assigned to bot worker
        # state = 20: On uploading to Telegram server
        # state = 30: Upload successfully & file_id, msg_id, etc recorded to queues
        # state = 40: Inserted to files table & deleted tmp
        # state = 100: error
        while True:
            try:
                if db.pool:
                    async with db.pool.acquire() as conn:
                        await conn.begin()
                        try:
                            async with conn.cursor(aiomysql.DictCursor) as cursor:
                                # Init cnt
                                cnt_10, cnt_20, cnt_30, cnt_40, cnt_100 = 0, 0, 0, 0, 0

                                # 1: UNDO STATE 10 | 20 > 10min w/ Jitter
                                await cursor.execute("SELECT file_uuid, state FROM queues WHERE state IN (10, 20) AND updated_at < NOW() - INTERVAL 10 MINUTE")
                                stale_undo_jobs = await cursor.fetchall()
                                if stale_undo_jobs:
                                    uuids_to_undo_10 = [j['file_uuid'] for j in stale_undo_jobs if j['state'] == 10]
                                    uuids_to_undo_20 = [j['file_uuid'] for j in stale_undo_jobs if j['state'] == 20]
                                    cnt_10, cnt_20 = len(uuids_to_undo_10), len(uuids_to_undo_20)
                                    
                                    all_uuids = uuids_to_undo_10 + uuids_to_undo_20
                                    if all_uuids:
                                        placeholders = ', '.join(['%s'] * len(all_uuids))
                                        undo_query = f"""
                                            UPDATE queues 
                                            SET 
                                                state = 0, 
                                                bot_id = NULL, 
                                                updated_at = NOW(), 
                                                available_at = NOW() + INTERVAL (
                                                    {self.MIN_JITTER_VALUE} 
                                                    + RAND() * ({self.MAX_JITTER_VALUE} - {self.MIN_JITTER_VALUE})
                                                ) SECOND 
                                            WHERE file_uuid IN ({placeholders})
                                        """
                                        await cursor.execute(undo_query, tuple(all_uuids))
                                        print(f"[Controller GC] Reset {len(all_uuids)} stale jobs (State 10, 20).")

                                # 2: REDO STATE 30
                                await cursor.execute("SELECT file_uuid, file_id, msg_id, bot_id FROM queues WHERE state = 30 AND updated_at < NOW() - INTERVAL 10 MINUTE")
                                stale_redo_jobs = await cursor.fetchall()
                                if stale_redo_jobs:
                                    cnt_30 = len(stale_redo_jobs)
                                    print(f"[Controller GC] Re-committing {cnt_30} stuck jobs (State 30).")
                                    for job in stale_redo_jobs:  # fix
                                        file_uuid_bytes, file_uuid_str = job['file_uuid'], str(uuid.UUID(bytes=job['file_uuid']))
                                        try:
                                            await cursor.execute("INSERT INTO files (file_uuid, file_id, msg_id, bot_id) VALUES (%s, %s, %s, %s)",
                                                                 (file_uuid_bytes, job['file_id'], job['msg_id'], job['bot_id']))
                                            await cursor.execute("UPDATE queues SET state = 40, updated_at = NOW() WHERE file_uuid = %s AND state = 30", (file_uuid_bytes,))
                                        except Exception as e:
                                            print(f"[Controller GC] Error re-committing job {file_uuid_str}: {e}")
                                
                                # 3: UNDO STATE 100 w/ Exponential Backoff with Jitter
                                await cursor.execute("SELECT file_uuid FROM queues WHERE state = 100")
                                failed_jobs = await cursor.fetchall()
                                if failed_jobs:
                                    cnt_100 = len(failed_jobs)
                                    uuids_to_retry = [job['file_uuid'] for job in failed_jobs]
                                    placeholders = ', '.join(['%s'] * len(uuids_to_retry))
                                    retry_query = f"""
                                        UPDATE queues 
                                        SET 
                                            state = 0, 
                                            updated_at = NOW(), 
                                            retry_count = retry_count + 1, 
                                            available_at = NOW() + INTERVAL (
                                                LEAST(POW(2, retry_count)-1, 3000)
                                                + {self.MIN_JITTER_VALUE} 
                                                + (RAND() * ({self.MAX_JITTER_VALUE} - {self.MIN_JITTER_VALUE}))
                                            ) SECOND 
                                        WHERE file_uuid IN ({placeholders})
                                    """
                                    await cursor.execute(retry_query, tuple(uuids_to_retry))
                                    print(f"[Controller GC] Retrying {cnt_100} failed jobs (State 100).")

                                # 4: DELETE STATE 40
                                await cursor.execute("DELETE FROM queues WHERE state = 40")
                                cnt_40 = cursor.rowcount
                                if cnt_40 > 0:
                                    print(f"[Controller GC] Deleted {cnt_40} processed jobs.")

                                # 5: LOGGING
                                if (cnt_10 + cnt_20 + cnt_30 + cnt_40 + cnt_100) > 0:
                                    await cursor.execute(
                                        "INSERT INTO gc_runs (cnt_10, cnt_20, cnt_30, cnt_40, cnt_100) VALUES (%s, %s, %s, %s, %s)",
                                        (cnt_10, cnt_20, cnt_30, cnt_40, cnt_100)
                                    )
                                    print(f"[Controller GC] Logged GC run summary.")
                            
                            await conn.commit()

                        except Exception as e:
                            await conn.rollback()
                            print(f"[Controller GC] Error during transaction: {e}")
            except Exception as e:
                print(f"[Controller GC] Error in task loop: {e}")

            # GC sleeps an hour
            await asyncio.sleep(3600)

    # redis?
    async def _get_token(self, bot_id: int) -> str | None:
        # L1...?
        bot_token = await self._redis.get(str(bot_id))
        if bot_token:
            return bot_token

        if not db.pool:
            raise RuntimeError("Database pool is not initialized.")
        async with db.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                
                await cursor.execute(
                    """
                    SELECT bot_token
                    FROM bots
                    WHERE bot_id = %s
                    """, (bot_id,)
                )
                
                result = await cursor.fetchone()
                bot_token = result['bot_token'] if result else None
                if(bot_token):
                    await self._redis.setex(str(bot_id), 999999999, bot_token)
                    return bot_token
                return None

    async def get_cache(self, file_uuid: str) -> str | None:
        # L1: redis
        telegram_file_url = await self._redis.get(file_uuid)
        if telegram_file_url:
            return telegram_file_url
        
        # L2: url_caches
        url_cache_repo = db.UrlCacheRepository()
        url_cache_result = await url_cache_repo.get_url_cache_by_uuid(file_uuid)
        
        if url_cache_result:
            telegram_file_url = await self._get_telegram_file_url(
                url_cache_result["bot_token"],
                url_cache_result['file_id']
            )

            # generate L1
            await self._redis.setex(file_uuid, 3600, telegram_file_url)
            return telegram_file_url
                
        # L3: files, etc
        files_repo = db.FilesRepository()
        file_result = await files_repo.get_file_by_uuid(file_uuid)
        if file_result:
            bot_id = int(file_result['bot_id'])
            file_id = file_result['file_id']
            bot_token = await self._get_token(bot_id)
            if not bot_token: return None
            
            # generate L1
            telegram_file_url = await self._get_telegram_file_url(bot_token, file_id)
            await self._redis.setex(file_uuid, 3600, telegram_file_url)
            
            # generate L2
            # stateless -> stateless (lockfree)
            db_task = {
                "query": "INSERT IGNORE INTO url_caches (file_uuid, file_id, bot_token) VALUES (%s, %s, %s)",
                "params": (uuid.UUID(file_uuid).bytes, file_id, bot_token)
            }
            try:
                self._db_queue.put_nowait(db_task) # offload
            except asyncio.QueueFull:
                pass # ignore(anyway ensure redis cache)
            return telegram_file_url
        return None

    async def _get_telegram_file_url(self, bot_token: str, file_id: str) -> str:
        url = f'https://api.telegram.org/bot{bot_token}/getFile'
        resp = await self._http_client.get(url, params={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()
        file_path = data['result']['file_path']
        return f'https://api.telegram.org/file/bot{bot_token}/{file_path}'
