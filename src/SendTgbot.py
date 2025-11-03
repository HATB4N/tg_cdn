import asyncio
from telegram import Bot
from telegram.ext import ApplicationBuilder
from sqlalchemy import update, func, or_, select
from .db import Session, File

class Tgbot:
    '''each obj's token must be unique'''
    _bot_id: int
    _token: str
    _chat_id: str
    _api_svr: str
    _q: asyncio.Queue[tuple[str, str]]
    # file_id, path, bot_id
    _worker_task: asyncio.Task | None
    len_q: int
    busy: bool
    def __init__(self, bot_id: int, token: str, chat_id: str):
        self._q = asyncio.Queue()
        self._bot_id = bot_id
        self._token = token
        self._chat_id = chat_id
        self._worker_task = None
        self.len_q = 0
        self.busy = 0

    async def add_file(self, file_uuid: str, path: str): # add file & alert to queue
        # print(f'sbot[{self._bot_id}]: file added')
        try:
            self._q.put_nowait((file_uuid, path))
            self.len_q = self._q.qsize()
            await self._update_file_state(file_uuid=file_uuid, state=20, exp_state=[10])
        except:
            print(f'sbot[{self._bot_id}]: error: add file')
            # 0 -> 10 (큐 등록) -> 20 (등록 성공)
            # 실패시 100으로 에러 처리 하거나, 5같은 값 부여해서 일정 횟수 retry가능하게
            raise

    async def _queue_worker(self):
        try:
            while True:
                # print(f'sbot[{self._bot_id}]: start loop')
                _file_uuid, _path = await self._q.get()
                self.len_q = self._q.qsize()
                try:
                    self.busy = 1
                    await self._update_file_state(
                            file_uuid=_file_uuid, 
                            state=30, 
                            exp_state=[20])
                    _msg_id, _file_id = await self._send_file(path = _path, caption = _file_uuid)
                    ok = await self._write_index(
                        file_uuid=_file_uuid, 
                        msg_id=_msg_id, 
                        file_id = _file_id
                        )
                    if not ok:
                        print(f"sbot[{self._bot_id}]: id={_file_uuid} wrong state")
                except Exception as e:
                    print(f"sbot[{self._bot_id}]: Error processing file {_file_uuid}: {e}")
                    try:
                        await self._mark_fail(_file_uuid, str(e))
                    except Exception as e2:
                        print(f"sbot[{self._bot_id}]: fail mark error:", e2)
                finally:
                    self.busy = 0
        except asyncio.CancelledError:
            pass
            # TODO

    async def _send_file(self, path: str, caption: str):
        # print(f'sbot[{self._bot_id}]: send file')
        bot = self._app.bot
        with open(path, "rb") as f:
            msg = await bot.send_document(
                    chat_id=self._chat_id,
                    document = f,
                    caption = caption,
                    read_timeout = 60,
                    write_timeout = 60,
                    connect_timeout = 60
                    )
        # print(f'sbot[{self._bot_id}]: send file done')
        return msg.message_id, msg.document.file_id

    async def _write_index(self, file_uuid: str, msg_id: int, file_id: str) -> bool:
        async with Session() as s:
            async with s.begin():
                res = await s.execute(
                    update(File)
                    .where(
                        File.file_uuid == file_uuid,
                        File.state.in_([10, 20, 30]),
                        or_(File.msg_id.is_(None), File.msg_id == msg_id),
                    )
                    .values(
                        file_uuid=file_uuid,
                        msg_id=msg_id,
                        file_id=file_id,
                        state=40,
                        updated_at=func.now(),
                    )
                )
        if res.rowcount == 1:
            return True
        async with Session() as s:
            got = await s.scalar(select(File.msg_id).where(File.file_uuid == file_uuid))
            return bool(got == msg_id)

    async def _mark_fail(self, file_uuid: str, err: str) -> int:
        async with Session() as s:
            async with s.begin():
                res = await s.execute(
                    update(File)
                    .where(File.file_uuid == file_uuid, File.state.in_([20, 30]))
                    .values(
                        state=100,
                        updated_at=func.now()
                    )
                )
            return res.rowcount  # 0이면 조건 불일치

    async def _update_file_state(self, file_uuid: str, state: int, exp_state: list[int] | None = None) -> int:
        async with Session() as s:
            async with s.begin():
                query = update(File).where(File.file_uuid == file_uuid)
                if exp_state:
                    query = query.where(File.state.in_(exp_state))
                res = await s.execute(
                    query.values(
                        state=state,
                        updated_at=func.now()
                    )
                )
                return res.rowcount

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

