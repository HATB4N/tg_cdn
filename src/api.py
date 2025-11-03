import asyncio
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from .db import Session, Bot
from io import BytesIO
import httpx
import os
import uuid

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

    def sniff_image_mime(head: bytes) -> str:
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

    @app.post("/upload")
    async def upload(request: Request, file: UploadFile = File(...)):
        _controller = request.app.state.controller
        if not _controller:
            return JSONResponse(content={
                    'result': '-1', 
                    'file_uuid': '-1'
                    }, status_code=500)

        if not file:
            return JSONResponse(content={
                    'result': '-1', 
                    'file_uuid': '-1'
                    }, status_code=400)

        # check mimetype
        allowed_mimetypes = [
            'image/jpeg',
            'image/png',
            'image/gif',
            'image/webp',
            'image/bmp'
        ]

        if file.content_type not in allowed_mimetypes:
            return JSONResponse(content={
                    'result': '-1',
                    'file_uuid': '-1'
                    }, status_code=415)

        initial_chunk = await file.read(1024) # test
        await file.seek(0)

        sniffed_mime = sniff_image_mime(initial_chunk)
        if sniffed_mime not in allowed_mimetypes:
            return JSONResponse(content={
                    'result': '-1', 
                    'file_uuid': '-1' 
                    }, status_code=415)

        file_uuid = str(uuid.uuid4())

        # Save the file temporarily
        temp_dir = "/tmp/tg_img_cdn"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file_uuid)
        
        with open(temp_path, 'wb') as f:
            while content := await file.read(1024):
                f.write(content)

        actual_file_size = os.path.getsize(temp_path)
        max_file_size_mb = 20 # file up to 20MB 
        max_file_size_bytes = max_file_size_mb * 1024 * 1024

        if actual_file_size > max_file_size_bytes:
            os.remove(temp_path) # Delete the large file
            return JSONResponse(content={
                    'result': '-1', 
                    'file_uuid': '-1' 
                    }, status_code=413)

        # Add to controller queue
        await _controller.handle_upload(file_uuid)

        return JSONResponse(content={
                    'result': '1', 
                    'file_uuid': file_uuid 
                    }, status_code=200)

    async def open_stream(target_url: str):
        client = httpx.AsyncClient()
        stream_context_manager = client.stream("GET", target_url)
        response = await stream_context_manager.__aenter__()
        response.raise_for_status()
        return client, response, stream_context_manager

    @app.get('/content/{file_uuid}')
    async def load(file_uuid: str, request: Request):
        _controller = request.app.state.controller
        if not _controller:
            raise HTTPException(status_code=404, detail="err")

        target = await _controller.get_path(file_uuid)
        if target is None:
            raise HTTPException(status_code=404, detail="err")

        try:
            client = httpx.AsyncClient()
            response = await client.get(target)
            response.raise_for_status()
            
            first_chunk = response.content[:1024]
            mime = sniff_image_mime(first_chunk)

            async def generator():
                yield response.content

            # fix after read cloudlfare docs
            headers = {
                'Content-Disposition': f'inline; filename="{file_uuid}"',
                'Cache-Control': 'public, max-age=8640000',
                'Access-Control-Allow-Origin': "*"
            }
            return StreamingResponse(generator(), headers=headers, media_type=mime)

        except httpx.HTTPStatusError as e:
            print(f'stream err: {e}')
            raise HTTPException(status_code=404, detail="err")
        except Exception as e:
            print(f'err: {e}')
            raise HTTPException(status_code=404, detail="err")
        finally:
            if 'client' in locals() and not client.is_closed:
                await client.aclose()

    return app


