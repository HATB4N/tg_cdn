## api
### upload
- endpoint: `/upload`
- method: `POST` with img file
- body: ```
        {
          "result": "1",
          "file_uuid": "<uuid>"
        }
        ```
### retrive
- endpoint: `/content/<uuid>`
- method: `GET`
- body: `raw img bin data with appropriate mimetype`

## install & build
```bash
git clone https://github.com/HATB4N/tg_cdn.git
cd tg_cdn
(sudo) docker compose up --build (-d)
```

## req
- channel id, bot token>= 1(distributed)  
- db svr [mysql / mariadb](tested on mariadb 10.11)  
- recommend linux(tested on rocky linux 10)  
- temp file basically stored in `/tmp/tg_cdn/` I recommend shorten the cache flush period(<1h).

## etc
### structure
<img width="2520" height="1792" alt="tg_cdn_structure" src="https://github.com/user-attachments/assets/d8c834cd-28da-4c62-ba39-d9bff7e78d14" />


---

The maximum exportable file size via ```api.telegram.org/file/bot{bot_token}/{file_path}``` is **20 MB**.  
The ```file_id``` is unique and static, but ```file_path``` is **not**.  
You need to request updates from the Telegram server using the ```file_id```(the check cache logic did it).  
Officially, the ```file_path``` is guaranteed to remain valid for at least **one hour**.  
[reference](https://core.telegram.org/bots/api#getfile)

### tables

---

`MariaDB [tg_cdn_db]> show tables;`

| Tables_in_tg_cdn_db |
| :--- |
| bots |
| files |
| url_caches |

*3 rows in set (0.011 sec)*

---

`MariaDB [tg_cdn_db]> desc bots;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| bot_id | smallint(6) | NO | PRI | NULL | auto_increment |
| token | varchar(50) | NO | UNI | NULL | |

*2 rows in set (0.012 sec)*

---

`MariaDB [tg_cdn_db]> desc files;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| file_uuid | binary(16) | NO | PRI | NULL | |
| file_id | verchar(191) | YES | MUL | NULL | |
| state | smallint(6) | NO | MUL | 0 | |
| msg_id | int(11) | YES | MUL | NULL | |
| bot_id | smallint(6) | YES | MUL | NULL | |
| created_at | timestamp | NO | MUL | current_timestamp() | |
| updated_at | timestamp | YES | | NULL | on update current_timestamp() |

*7 rows in set (0.012 sec)*

---

`MariaDB [tg_cdn_db]> desc url_caches;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| file_uuid | binary(16) | NO | PRI | NULL | |
| file_path | varchar(50) | NO | | NULL | |
| bot_id | smallint(6) | NO | MUL | NULL | |
| file_path_updated_at | timestamp | NO | | current_timestamp() | on update current_timestamp() |

*4 rows in set (0.004 sec)*
