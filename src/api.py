import asyncio
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from . import db
from io import BytesIO
import httpx
import os
import uuid
from uuid_extensions import uuid7
from typing import Dict, Any
import aiofiles

TEMP_DIR = "./tmp"
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_MIMETYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp'
}

def create_app(lifespan_context=None):
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index():
        text = """
        <!DOCTYPE html>
        <html>
            <head>
                <title>Telegram is not CDN</title>
            </head>
            <body>
                <p> yesssssir </p>
            </body>
        </html>
        """
        return text

    def _sniff_image_mime(head: bytes) -> str:
        if head.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        elif head.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        elif head.startswith(b'GIF8'):
            return 'image/gif'
        elif head.startswith(b'RIFF') and b'WEBP' in head:
            return 'image/webp'
        elif head.startswith(b'BM'):
            return 'image/bmp'
        return 'application/octet-stream'
    
    async def _handle_upload(file_uuid: str):
        if not db.pool:
            raise RuntimeError("Database pool is not initialized.")
    
        async with db.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 이 이후의 데이터에 대해서는 일관성을 보장
                await cursor.execute(
                    "INSERT INTO queues (file_uuid) VALUES (%s)",
                    (uuid.UUID(file_uuid).bytes,)
                )
                return cursor.lastrowid

    @app.post("/upload")
    async def upload(request: Request, file: UploadFile = File(...)):

        def ret_err(code):
            return JSONResponse(content={
            'result': '-1',
            'file_uuid': '-1'
            }, status_code = code)

        if not file:
            return ret_err(400)

        if file.content_type not in ALLOWED_MIMETYPES:
            return ret_err(415)

        try:
            initial_chunk = await file.read(1024) # test
            await file.seek(0)
        except Exception:
            return ret_err(400)

        if _sniff_image_mime(initial_chunk) not in ALLOWED_MIMETYPES:
            return ret_err(415)

        # uuid7으로
        file_uuid = str(uuid7(as_type="str"))
        temp_path = os.path.join(TEMP_DIR, file_uuid)

        acc_sz = 0

        try:
            async with aiofiles.open(temp_path, 'wb') as f:
                while True:
                    chunk = await file.read(1024*64)
                    if not chunk:
                        break
                    # accumulate sz during download client's file upd
                    acc_sz += len(chunk)
                    if acc_sz> MAX_FILE_SIZE_BYTES:
                        raise HTTPException(status_code = 413)
                    await f.write(chunk)
            try:
                await _handle_upload(file_uuid)
            except Exception as e:
                print(f'[API]: db err {e}')
                return ret_err(500)
            return JSONResponse(content={
                        'result': '1', 
                        'file_uuid': file_uuid 
                        }, status_code=200)
        except HTTPException as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return ret_err(e.status_code)

    @app.get('/content/{file_uuid}')
    async def content(file_uuid: str, request: Request):
        _controller = request.app.state.controller
        if not _controller:
            return JSONResponse(status_code=503, content={"detail": "Controller not available."})

        target = await _controller.get_cache(file_uuid)
        if target is None:
            return JSONResponse(
                status_code=404,
                content={"detail": "File not found. It may be in processing or the UUID is invalid."}
            )

        client: httpx.AsyncClient = request.app.state.http_client
        req = client.build_request("GET", target)
        try:
            upstream_response = await client.send(req, stream=True)
        except httpx.RequestError as e:
            print(f"Request error to upstream: {e}")
            return JSONResponse(status_code=504, content={"detail": "Could not connect to upstream server."})
        if upstream_response.status_code >= 400:
            await upstream_response.aclose()
            return JSONResponse(
                status_code=upstream_response.status_code,
                content={"detail": "Upstream server returned an error."}
            )

        byte_iterator = upstream_response.aiter_bytes()
        
        try:
            first_chunk = await byte_iterator.__anext__()
        except StopAsyncIteration:
            first_chunk = b''
            await upstream_response.aclose()
            return JSONResponse(status_code=204, content={})
        
        mime_type = _sniff_image_mime(first_chunk)

        async def content_generator():
            try:
                yield first_chunk
                async for chunk in byte_iterator:
                    yield chunk
            except Exception as e:
                pass
            finally:
                await upstream_response.aclose()

        headers = {
            'Content-Disposition': f'inline; filename="{file_uuid}"',
            'Cache-Control': 'public, max-age=8640000',
            'Access-Control-Allow-Origin': "*"
        }

        return StreamingResponse(
            content_generator(), 
            headers=headers, 
            media_type=mime_type
        )

    return app
