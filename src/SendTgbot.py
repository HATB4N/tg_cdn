import asyncio
from telegram import Bot
from telegram.ext import ApplicationBuilder
from sqlalchemy import update, func, or_, select
from db import Session, File

class Tgbot:
    '''each obj's token must be unique'''
    _bot_id: int
    _token: str
    _chat_id: str
    _api_svr: str
    _q: asyncio.Queue[tuple[str, str, str, int]]
    # file_id, path, title, bot_id
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

    async def add_file(self, path: str, file_id: str, title: str): # add file & alert to queue
        print(f'sbot[{self._bot_id}]: file added')
        try:
            self._q.put_nowait((file_id, path, title, self._bot_id))
            self.len_q = self._q.qsize()
            await self._update_file_state(file_id=file_id, state=20, exp_state=[10])
        except:
            print('error')
            raise

    async def _queue_worker(self):
        try:
            while True:
                print(f'sbot[{self._bot_id}]: start loop')
                _file_id, _path, _title, _bot_id = await self._q.get()
                self.len_q = self._q.qsize()
                try:
                    self.busy = 1
                    await self._update_file_state(file_id=_file_id, state=30, exp_state=[20])
                    _msg_id = await self._send_file(path = _path, caption = _title, bot_id = _bot_id)
                    ok = await self._write_index(file_id=_file_id, msg_id=_msg_id)
                    if not ok:
                        print(f"sbot[{self._bot_id}]: id={_file_id} wrong state")
                except Exception as e:
                    print(f"sbot[{self._bot_id}]: Error processing file {_file_id}: {e}")
                    try:
                        await self._mark_fail(_file_id, str(e))
                    except Exception as e2:
                        print(f"sbot[{self._bot_id}]: fail mark error:", e2)
                finally:
                    self.busy = 0
        except asyncio.CancelledError:
            pass
            # TODO
    async def _send_file(self, path: str, caption: str, bot_id: int):
        print(f'sbot[{self._bot_id}]: send file')
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
        print(f'sbot[{self._bot_id}]: send file done')
        return msg.message_id

    async def _write_index(self, file_id: str, msg_id: int) -> bool:
        async with Session() as s:
            async with s.begin():
                res = await s.execute(
                    update(File)
                    .where(
                        File.file_id == file_id,
                        File.state.in_([10, 20, 30]),
                        or_(File.msg_id.is_(None), File.msg_id == msg_id),
                    )
                    .values(
                        msg_id=msg_id,
                        state=40,
                        updated_at=func.now(),
                    )
                )
        if res.rowcount == 1:
            return True
        async with Session() as s:
            got = await s.scalar(select(File.msg_id).where(File.file_id == file_id))
            return bool(got == msg_id)

    async def _mark_fail(self, file_id: str, err: str) -> int:
        async with Session() as s:
            async with s.begin():
                res = await s.execute(
                    update(File)
                    .where(File.file_id == file_id, File.state.in_([20, 30]))
                    .values(
                        state=100,
                        updated_at=func.now()
                    )
                )
            return res.rowcount  # 0이면 조건 불일치

    async def _update_file_state(self, file_id: str, state: int, exp_state: list[int] | None = None) -> int:
        async with Session() as s:
            async with s.begin():
                query = update(File).where(File.file_id == file_id)
                if exp_state:
                    query = query.where(File.state.in_(exp_state))
                res = await s.execute(
                    query.values(
                        state=state,
                        updated_at=func.now()
                    )
                )
                return res.rowcount

    async def _renew_msg(self, msg_id):
        bot = self._app.bot
        forwarded_msg = await bot.forward_message(
                chat_id = self._chat_id, # fix to new room?
                from_chat_id = self._chat_id,
                message_id = msg_id
                )
        file_id = forwarded_msg.document.file_id
        print(f'sbot[{self._bot_id}]: forward file done')
        return file_id

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

