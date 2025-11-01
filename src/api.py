import asyncio
from quart import Quart, request, Response, current_app, send_file, jsonify
from sqlalchemy import select
from db import Session, Bot
from io import BytesIO
import httpx
import aiohttp
import os
import uuid

def create_app(ctr_instance):
    app = Quart(__name__)
    app.config['controller'] = ctr_instance

    @app.route('/')
    async def index():
        text = """
        <!DOCTYPE html>

        <head>
        <title>Telegram is not CDN</title>
        </head>

        <body>
            <p> yesssssir </p>
        </body>
        """
        return text 

    
    def sniff_image_mime(head: bytes) -> str:
        if head.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        elif head.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        elif head.startswith(b'GIF8'):
            return 'image/gif'
        elif head[:4] == b'RIFF' and b'WEBP' in head:
            return 'image/webp'
        elif head.startswith(b'BM'):
            return 'image/bmp'
        return 'application/octet-stream'

    @app.route('/upload', methods=['POST'])
    async def upload():
        _controller = current_app.config['controller']
        if not _controller:
            return jsonify({
                    'result': '-1', 
                    'file_id': '-1'
                    }), 500

        files = await request.files
        upload_file = files.get('file')

        if not upload_file:
            return jsonify({
                    'result': '-1', 
                    'file_id': '-1'
                    }), 400 

        # check mimetype
        allowed_mimetypes = [
            'image/jpeg',
            'image/png',
            'image/gif',
            'image/webp',
            'image/bmp'
        ]

        if upload_file.mimetype not in allowed_mimetypes:
            return jsonify({
                    'result': '-1',
                    'file_id': '-1'
                    }), 415 

        initial_chunk = upload_file.read(1024)
        upload_file.seek(0)

        sniffed_mime = sniff_image_mime(initial_chunk)
        if sniffed_mime not in allowed_mimetypes:
            return jsonify({
                    'result': '-1', 
                    'file_id': '-1' 
                    }), 415 


        file_id = str(uuid.uuid4())

        # Save the file temporarily
        # 용량 빨리 차니까 sysd등으로 /tmp/tg_img_cdn dir file age< 1h면 삭제되게 설정 하는 것을 강력히 추천
        temp_dir = "/tmp/tg_img_cdn"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file_id)
        await upload_file.save(temp_path) # File is now saved to disk

        actual_file_size = os.path.getsize(temp_path)
        max_file_size_mb = 20 # file up to 20MB 
        max_file_size_bytes = max_file_size_mb * 1024 * 1024


        if actual_file_size > max_file_size_bytes:
            os.remove(temp_path) # Delete the large file
            return jsonify({
                    'result': '-1', 
                    'file_id': '-1' 
                    }), 413 
        # test end

        # Add to controller queue
        await _controller.handle_upload(file_id)

        return jsonify({
                    'result': '1', 
                    'file_id': file_id 
                    }), 200 


    async def open_stream(target_url: str):
        client = httpx.AsyncClient()
        stream_context_manager = client.stream("GET", target_url)
        response = await stream_context_manager.__aenter__()
        response.raise_for_status()
        return client, response, stream_context_manager


    @app.route('/content/<file_id>')
    async def load(file_id: str):
        _controller = current_app.config.get('controller')
        if not _controller:
            return 'err', 404

        target = await _controller.get_path(file_id)
        if target is None:
            return 'err', 404

        try:
            client, response, stream_context_manager = await open_stream(target)

            # 첫 chunk 선읽기
            aiter = response.aiter_bytes()
            first_chunk = await aiter.__anext__()
            mime = sniff_image_mime(first_chunk)

            async def generator():
                try:
                    yield first_chunk
                    async for chunk in aiter:
                        yield chunk
                finally:
                    # 연결 닫기
                    await stream_context_manager.__aexit__(None, None, None)
                    await client.aclose()

            headers = {
                'Content-Disposition': f'inline; filename="{file_id}"',
                'Cache-Control': 'public, max-age=8640000', # /content 정적 캐싱 가능
                'Access-Control-Allow-Origin': "*"
            }
            return Response(
                response=generator(),
                headers=headers,
                mimetype=mime
            ), 200

        except httpx.HTTPStatusError as e:
            print(f'stream err: {e}')
            if 'client' in locals() and not client.is_closed:
                await client.aclose()
            if 'stream_context_manager' in locals():
                await stream_context_manager.__aexit__(None, None, None)
            return 'err', 404
        except Exception as e:
            print(f'err: {e}')
            if 'client' in locals() and not client.is_closed:
                await client.aclose()
            if 'stream_context_manager' in locals():
                await stream_context_manager.__aexit__(None, None, None)
            return 'err', 404
    return app

