# tg_cdn
2025 2nd Semester Database Project

## api
### upload
- endpoint: `/upload`
- method: `POST` with img file
- body: `{ "result": "1", "file_uuid": "<uuid>" }` or err json with result != 1
### retrive
- endpoint: `/content/<uuid>`
- method: `GET`
- body: `raw img bin data with appropriate mimetype` or err json

## install & build
```bash
git clone https://github.com/HATB4N/tg_cdn.git
cd tg_cdn
# check out docker-compose-standalone.yml
# fix .env
sudo docker compose up --build
```
### req
- channel id = 1, bot token>= 1 (distributed)  
- (optional) db svr [mysql / mariadb] (tested on mariadb 10.11 w/ rocky linux 10)  

## etc
### structure (legacy)
<img width="1000" height="711" alt="tg_cdn_structure" src="https://github.com/user-attachments/assets/d8c834cd-28da-4c62-ba39-d9bff7e78d14" />

---

The maximum exportable file size via ```api.telegram.org/file/bot{bot_token}/{file_path}``` is **20 MB**.  
The ```file_id``` is unique and static, but ```file_path``` is **not**.  
You need to request updates from the Telegram server using the ```file_id```(the check cache logic did it).  
Officially, the ```file_path``` is guaranteed to remain valid for at least **one hour**.  
[reference](https://core.telegram.org/bots/api#getfile)

### test res
<img width="1000" height="372" alt="cf_monthly" src="https://github.com/user-attachments/assets/c78c2594-8a01-4194-b7e0-14279a650f20" />
<img width="1000" height="1429" alt="100samples_dff_view" src="https://github.com/user-attachments/assets/f1401464-20eb-41a8-bdb5-75808806f315" />
<img width="1000" height="628" alt="mk_dg" src="https://github.com/user-attachments/assets/a9193d6f-c080-4d11-85b6-56541a4ffc07" />
