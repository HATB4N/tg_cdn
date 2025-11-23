from __future__ import annotations
import os
import uuid
import aiomysql
import asyncio
from datetime import datetime
import pymysql.converters

user = os.getenv("DB_USER", "tg_cdn_db_user")
pwd  = os.getenv("DB_PASSWORD", "password")
host = os.getenv("DB_HOST", "localhost")
port = int(os.getenv("DB_PORT", 3306))
db   = os.getenv("DB_DATABASE", "tg_cdn_db")

pymysql.converters.conversions[uuid.UUID] = pymysql.converters.escape_bytes

pool: aiomysql.Pool | None = None

async def init_db_pool():
    global pool
    if pool:
        await close_db_pool()

    print("Initializing database connection pool...")
    pool = await aiomysql.create_pool(
        host=host, port=port,
        user=user, password=pwd,
        db=db,
        charset='utf8mb4',
        autocommit=True,
        minsize=10,
        maxsize=20,
        pool_recycle=1800,
        loop=asyncio.get_event_loop(),
    )
    print("Database pool initialized.")

async def close_db_pool():
    global pool
    if pool:
        pool.close()
        await pool.wait_closed()
        pool = None
        print("Database pool closed.")

SQL_CREATE_BOTS = """
CREATE TABLE IF NOT EXISTS bots (
    bot_id SMALLINT PRIMARY KEY AUTO_INCREMENT,
    bot_token VARCHAR(50) NULL,
    UNIQUE KEY (bot_token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

SQL_CREATE_FILES = """
CREATE TABLE IF NOT EXISTS files (
    file_uuid BINARY(16) PRIMARY KEY,
    file_id VARCHAR(191) NOT NULL,
    msg_id INT NOT NULL,
    bot_id SMALLINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (bot_id) REFERENCES bots(bot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

SQL_CREATE_QUEUES = """
CREATE TABLE IF NOT EXISTS queues (
    file_uuid BINARY(16) PRIMARY KEY,
    file_id VARCHAR(191) NULL,
    state SMALLINT NOT NULL DEFAULT 0,
    msg_id INT NULL,
    bot_id SMALLINT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (bot_id) REFERENCES bots(bot_id),
    INDEX idx_state (state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

SQL_CREATE_URL_CACHES = """
CREATE TABLE IF NOT EXISTS url_caches (
    file_uuid BINARY(16) PRIMARY KEY,
    file_id VARCHAR(191) NULL,
    bot_token VARCHAR(50) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (file_uuid) REFERENCES files(file_uuid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

SQL_CREATE_GC_RUNS = """
CREATE TABLE IF NOT EXISTS gc_runs (
    run_id INT AUTO_INCREMENT PRIMARY KEY,
    run_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cnt_10 INT DEFAULT 0,
    cnt_20 INT DEFAULT 0,
    cnt_30 INT DEFAULT 0,
    cnt_40 INT DEFAULT 0,
    cnt_100 INT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def init_models():
    global pool
    if not pool:
        raise RuntimeError("Database pool is not initialized.")
    
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # await cursor.execute(SQL_CREATE_METADATAS)
            await cursor.execute(SQL_CREATE_BOTS)
            await cursor.execute(SQL_CREATE_FILES)
            await cursor.execute(SQL_CREATE_QUEUES)
            await cursor.execute(SQL_CREATE_URL_CACHES)
            await cursor.execute(SQL_CREATE_GC_RUNS)

def _bin_to_uuid_str(value: bytes | None) -> str | None:
    """ DB (bytes) -> Python (str) """
    if value is None:
        return None
    try:
        return str(uuid.UUID(bytes=value))
    except ValueError:
        return None

class FilesRepository:
    async def get_file_by_uuid(self, file_uuid: str | uuid.UUID) -> dict | None:
        file_uuid_obj: uuid.UUID | None = None

        try:
            if isinstance(file_uuid, str):
                if len(file_uuid) == 36: 
                    file_uuid_obj = uuid.UUID(file_uuid)
                elif len(file_uuid) == 32: 
                    file_uuid_obj = uuid.UUID(hex=file_uuid)
                else:
                    return None
            elif isinstance(file_uuid, uuid.UUID):
                file_uuid_obj = file_uuid
            else:
                return None
                
        except ValueError:
            return None

        file_uuid_bytes = file_uuid_obj.bytes

        global pool
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT * FROM files WHERE file_uuid = %s",
                    (file_uuid_bytes,)
                )
                row = await cursor.fetchone()
                
                if row:
                    row['file_uuid'] = _bin_to_uuid_str(row['file_uuid'])
                
                return row

class UrlCacheRepository:
    async def get_url_cache_by_uuid(self, file_uuid: str | uuid.UUID) -> dict | None:
        file_uuid_obj: uuid.UUID | None = None

        try:
            if isinstance(file_uuid, str):
                if len(file_uuid) == 36: 
                    file_uuid_obj = uuid.UUID(file_uuid)
                elif len(file_uuid) == 32: 
                    file_uuid_obj = uuid.UUID(hex=file_uuid)
                else:
                    return None
            elif isinstance(file_uuid, uuid.UUID):
                file_uuid_obj = file_uuid
            else:
                return None
                
        except ValueError:
            return None

        file_uuid_bytes = file_uuid_obj.bytes

        global pool
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT file_id, bot_token FROM url_caches WHERE file_uuid = %s",
                    (file_uuid_bytes,)
                )
                row = await cursor.fetchone()
                return row

    async def insert_url_cache(self, file_uuid: str | uuid.UUID, file_id: str, bot_token: str) -> int:
        file_uuid_obj: uuid.UUID | None = None

        try:
            if isinstance(file_uuid, str):
                if len(file_uuid) == 36: 
                    file_uuid_obj = uuid.UUID(file_uuid)
                elif len(file_uuid) == 32: 
                    file_uuid_obj = uuid.UUID(hex=file_uuid)
                else:
                    return 0
            elif isinstance(file_uuid, uuid.UUID):
                file_uuid_obj = file_uuid
            else:
                return 0
                
        except ValueError:
            return 0

        file_uuid_bytes = file_uuid_obj.bytes

        global pool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO url_caches (file_uuid, file_id, bot_token) VALUES (%s, %s, %s)",
                    (file_uuid_bytes, file_id, bot_token)
                )
                return cursor.rowcount
