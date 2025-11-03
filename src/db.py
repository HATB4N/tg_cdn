from __future__ import annotations
import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, Integer, SmallInteger, Index, func, select, update, ForeignKey, UUID, TIMESTAMP, BINARY, TypeDecorator
from datetime import datetime

# MariaDB/MySQL
user = os.getenv("DB_USER", "tg_cdn_db_user")
pwd  = os.getenv("DB_PASSWORD", "password")
host = os.getenv("DB_HOST", "db")
port = os.getenv("DB_PORT", "3306")
db   = os.getenv("DB_DATABASE", "tg_cdn_db")
DATABASE_URL = f"mysql+aiomysql://{user}:{pwd}@{host}:{port}/{db}?charset=utf8mb4"

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "0") == "1",
    pool_size=10,
    max_overflow=10,
    pool_recycle=1800,
)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase): pass

class BinaryUUID(TypeDecorator):
    # convert between str % binary(16)
    impl = BINARY(16)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        # str to bin
        if value is None:
            return None
        try:
            return uuid.UUID(value).bytes
        except (ValueError, TypeError):
            return None

    def process_result_value(self, value, dialect):
        # bin to str
        if value is None:
            return None
        try:
            return str(uuid.UUID(bytes=value))
        except ValueError:
            return None

class Bot(Base):
    __tablename__ = "bots"
    bot_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(50), nullable=True)

class File(Base):
    __tablename__ = "files"
    file_uuid: Mapped[str] = mapped_column(
        BinaryUUID, 
        primary_key=True,
        default=lambda: str(uuid.uuid4()) # Python단에서 UUID 문자열 생성
    )
    file_id: Mapped[str] = mapped_column(String(191), nullable=True) # need to be check 
    state:  Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bot_id: Mapped[int | None] = mapped_column(SmallInteger, ForeignKey('bots.bot_id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(onupdate=func.now(), nullable=True)

    __table_args__ = (
        Index("idx_fid", "file_id"),
        Index("idx_state", "state")
    )

class UrlCache(Base):
    __tablename__ = "url_caches"
    file_uuid: Mapped[str] = mapped_column(
        BinaryUUID, 
        ForeignKey('files.file_uuid', ondelete='CASCADE'),
        primary_key=True
    )
    file_path: Mapped[str] = mapped_column(String(50), nullable=False)
    bot_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey('bots.bot_id'), nullable=False)
    file_path_updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
