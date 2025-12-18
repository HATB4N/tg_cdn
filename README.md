# tg_cdn
2025 2nd Semester Database Project

## Abstract
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-blue)](https://www.docker.com/)
[![Database](https://img.shields.io/badge/MariaDB-10.11-orange)](https://mariadb.org/)

This project implements a low-cost, self-hostable image hosting service by utilizing the **Telegram Bot API** as an infinite storage backend. While the Telegram API offers unlimited capacity, it suffers from latency, volatility, and strict rate limits.

**tg_cdn** bridges this gap by using a Relational Database as a State-Tracking Control Plane. Instead of simple file mapping, it employs a FSM with robust UNDO/REDO recovery logic to guarantee data consistency and high availability.

<img width="348" height="66" alt="image" src="https://github.com/user-attachments/assets/ddf97363-2044-4fe4-8618-dcfecbaa796e" />

## Example Usages
- self host website img backend
- *individual* crawling image backend
- idk. anyway you can upload & retrive imgs if you want

### Test Vectors
```python
# crawler example
content_type = f"image/{image_type}"
if not content_type.startswith("image"):
    raise ValueError(f"[type err] input var: '{content_type}' (must start with 'image')")

    response = await self.manager.cdn_client.post(
        "/upload", files={"file": (url.split("/")[-1], content, content_type)}
        )
    response.raise_for_status()
    data = response.json()
    if str(data.get("result")) == "1":
        file_uuid_str = data["file_uuid"]
        file_uuid_bytes = uuid.UUID(file_uuid_str).bytes
        images.append((url, file_uuid_bytes, content_type, sha256_hash))
else:
#...
```
```python
# more polite example written by AI
import requests

# 1. Upload
url = "http://localhost:8000/upload" # Change to your server address
files = {'file': open('example.jpg', 'rb')}

try:
    response = requests.post(url, files=files)
    response.raise_for_status()
    
    result = response.json()
    if result.get("result") == "1":
        uuid = result['file_uuid']
        print(f"Upload Success! UUID: {uuid}")
        print(f"Access Link: http://localhost:8000/content/{uuid}")
    else:
        print(f"Upload Failed: {result}")

except Exception as e:
    print(f"Error: {e}")
```
```html
<img src="https://example.com/content/uuid">
```
```sh
# Upload a file (Returns JSON with UUID)
curl -X POST -F "file=@/path/to/file" https://example.com/upload

# resp: {"result":"1","file_uuid":"<uuid>"}⏎

# Retrieve and display in terminal (using chafa)
curl -s https://example.com/content/<uuid> | chafa
```
<img width="1000" height="795" alt="image" src="https://github.com/user-attachments/assets/7c542d58-8f4e-4a80-9f6e-fd26405cd7e4" />

## API
### Upload
- endpoint: `/upload`
- method: `POST` with img file
- body: `{ "result": "1", "file_uuid": "<uuid>" }` or err json with result != 1
### Retrive
- endpoint: `/content/<uuid>`
- method: `GET`
- body: `raw img bin data with appropriate mimetype` or err json

## Install & Build
```bash
git clone https://github.com/HATB4N/tg_cdn.git
cd tg_cdn
# check out docker-compose-standalone.yml
# fix .env
sudo docker compose up --build
```
### Requirements
- channel id = 1, bot token>= 1 (distributed bot workers)
  - You should invite bots & assign them administrator privileges
- (optional) db svr [mysql / mariadb] (tested on mariadb 10.11 w/ rocky linux 10)  

## etc
### Structure (legacy)
<img width="1000" height="711" alt="tg_cdn_structure" src="https://github.com/user-attachments/assets/d8c834cd-28da-4c62-ba39-d9bff7e78d14" />
<img width="1000" height="453" alt="image" src="https://github.com/user-attachments/assets/716aa968-93b9-4a05-93b3-bac6e9195b68" />

---

The maximum exportable file size via ```api.telegram.org/file/bot{bot_token}/{file_path}``` is **20 MB**.  
The ```file_id``` is unique and static, but ```file_path``` is **not**.  
You need to request updates from the Telegram server using the ```file_id```(the check cache logic did it).  
Officially, the ```file_path``` is guaranteed to remain valid for at least **one hour**.  
[documentation](https://core.telegram.org/bots/api#getfile)

### Performances
<img width="1000" height="372" alt="cf_monthly" src="https://github.com/user-attachments/assets/c78c2594-8a01-4194-b7e0-14279a650f20" />
<img width="1000" height="1429" alt="100samples_dff_view" src="https://github.com/user-attachments/assets/f1401464-20eb-41a8-bdb5-75808806f315" />
<img width="1000" height="628" alt="mk_dg" src="https://github.com/user-attachments/assets/a9193d6f-c080-4d11-85b6-56541a4ffc07" />
