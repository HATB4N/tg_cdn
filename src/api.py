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

def create_app(ctr_instance):
    app = FastAPI()
    app.state.controller = ctr_instance

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

        ret_err = JSONResponse(content={
            'result': '-1',
            'file_uuid': '-1'
            }, status_code = 400)

        if not file:
            return ret_err # 400

        # check mimetype
        allowed_mimetypes = [
            'image/jpeg',
            'image/png',
            'image/gif',
            'image/webp',
            'image/bmp'
        ]

        if file.content_type not in allowed_mimetypes:
            return ret_err # 415

        initial_chunk = await file.read(1024) # test
        await file.seek(0)

        sniffed_mime = _sniff_image_mime(initial_chunk)
        if sniffed_mime not in allowed_mimetypes:
            return ret_err # 415

        # uuid7으로
        file_uuid = str(uuid7(as_type="str"))

        temp_dir = "./tmp"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file_uuid)
        
        with open(temp_path, 'wb') as f:
            while content := await file.read(1024):
                f.write(content)

        file_size = os.path.getsize(temp_path)
        max_file_size_mb = 20 # file up to 20MB 
        max_file_size_bytes = max_file_size_mb * 1024 * 1024

        if file_size > max_file_size_bytes:
            os.remove(temp_path) # Delete the large file
            return ret_err # 413

        # ctr중계 없이 여기서 db 쿼리 직접 날려
        await _handle_upload(file_uuid)

        return JSONResponse(content={
                    'result': '1', 
                    'file_uuid': file_uuid 
                    }, status_code=200)

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

        client = httpx.AsyncClient()
        try:
            # 수정됨: client.stream(...) 대신 request를 빌드하고 send(stream=True) 사용
            # stream context manager 밖에서도 response 객체를 유지하기 위함입니다.
            req = client.build_request("GET", target)
            upstream_response = await client.send(req, stream=True)

            if upstream_response.status_code >= 400:
                await upstream_response.aclose()
                await client.aclose()
                return JSONResponse(
                    status_code=upstream_response.status_code,
                    content={"detail": "Upstream server returned an error."}
                )

            byte_iterator = upstream_response.aiter_bytes()
            
            try:
                first_chunk = await byte_iterator.__anext__()
            except StopAsyncIteration:
                first_chunk = b''
            
            mime_type = _sniff_image_mime(first_chunk)

            async def content_generator():
                try:
                    if first_chunk:
                        yield first_chunk
                    async for chunk in byte_iterator:
                        yield chunk
                finally:
                    # 스트리밍이 끝나거나 클라이언트 연결이 끊기면 리소스 정리
                    await upstream_response.aclose()
                    await client.aclose()

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
        
        except httpx.RequestError as e:
            print(f"Request error to upstream: {e}")
            await client.aclose()
            return JSONResponse(status_code=504, content={"detail": "Could not connect to upstream server."})
        except Exception as e:
            print(f"Error setting up stream: {e}")
            await client.aclose()
            return JSONResponse(status_code=500, content={"detail": "An unexpected internal error occurred."})

    return app
