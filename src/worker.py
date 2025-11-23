import asyncio
import aiomysql
from . import db

class DBWorker:
    def __init__(self):
        """
        offload the querry process
        """
        self.queue = asyncio.Queue()

    async def run(self):
        while True:
            try:
                # 1: get the task from asyncio queue
                task_data = await self.queue.get()
                if not db.pool:
                    print("[DBWorker] error: dbpool")
                    self.queue.task_done()
                    continue
                # 2: run querry (offload...?)
                try:
                    async with db.pool.acquire() as conn:
                        async with conn.cursor() as cursor:
                            query = task_data.get('query')
                            params = task_data.get('params', ())
                            
                            await cursor.execute(query, params)
                            await conn.commit()
                            
                except Exception as e:
                    print(f"[DBWorker] err while processing querry: {e} / data: {task_data}")

            except asyncio.CancelledError:
                print("[DBWorker] closing...")
                break
            except Exception as e:
                print(f"[DBWorker] err while closing: {e}")
                await asyncio.sleep(1)
            finally:
                if 'task_data' in locals():
                    self.queue.task_done()