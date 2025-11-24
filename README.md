# tg_cdn
Database project

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
# fix .env
sudo docker compose up --build
```

## etc
### structure (legacy)
<img width="2520" height="1792" alt="tg_cdn_structure" src="https://github.com/user-attachments/assets/d8c834cd-28da-4c62-ba39-d9bff7e78d14" />

---

The maximum exportable file size via ```api.telegram.org/file/bot{bot_token}/{file_path}``` is **20 MB**.  
The ```file_id``` is unique and static, but ```file_path``` is **not**.  
You need to request updates from the Telegram server using the ```file_id```(the check cache logic did it).  
Officially, the ```file_path``` is guaranteed to remain valid for at least **one hour**.  
[reference](https://core.telegram.org/bots/api#getfile)

### req
- channel id, bot token>= 1 (distributed)  
- db svr [mysql / mariadb] (tested on mariadb 10.11 w/ rocky linux 10)  

### tables

---

`MariaDB [tg_cdn_db]> show tables;`

| Tables_in_tg_cdn_db |
| :--- |
| bots |
| files |
| gc_runs |
| queues |
| url_caches |

*5 rows in set (0.079 sec)*

---

`MariaDB [tg_cdn_db]> desc bots;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| bot_id | smallint(6) | NO | PRI | NULL | auto_increment |
| bot_token | varchar(50) | NO | UNI | NULL | |

*2 rows in set (0.179 sec)*

---

`MariaDB [tg_cdn_db]> desc files;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| file_uuid | binary(16) | NO | PRI | NULL | |
| file_id | verchar(191) | NO | MUL | NULL | |
| msg_id | int(11) | NO | | NULL | |
| bot_id | smallint(6) | NO | MUL | NULL | |
| created_at | timestamp | NO | | current_timestamp() | |

*5 rows in set (0.012 sec)*

---

`MariaDB [tg_cdn_db]> desc gc_runs;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| run_id | int(11) | NO | PRI | NULL | auto_increment |
| run_at | timestamp | NO | | current_timestamp() | |
| cnt_10 | smallint(6) | YES | | 0 | |
| cnt_20 | smallint(6) | YES | | 0 | |
| cnt_30 | smallint(6) | YES | | 0 | |
| cnt_40 | smallint(6) | YES | | 0 | |
| cnt_100 | smallint(6) | YES | | 0 | |

*7 rows in set (0.081 sec)*

---

`MariaDB [tg_cdn_db]> desc queues;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| file_uuid | binary(16) | NO | PRI | NULL | |
| file_id | verchar(191) | YES | MUL | NULL | |
| state | smallint(6) | NO | MUL | 0 | |
| msg_id | int(11) | YES | | NULL | |
| bot_id | smallint(6) | YES | MUL | NULL | |
| retry_count | smallint(6) | NO | | 0 | |
| created_at | timestamp | YES | | current_timestamp() | |
| updated_at | timestamp | YES| | NULL | on update current_timestamp() |
| available_at | timestamp | YES | | current_timestamp() | |

*9 rows in set (0.081 sec)*

---

`MariaDB [tg_cdn_db]> desc url_caches;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| file_uuid | binary(16) | NO | PRI | NULL | |
| file_id | verchar(191) | YES | | NULL | |
| bot_token | varchar(50) | NO | | NULL | |
| created_at | timestamp | NO | | current_timestamp() | |

*4 rows in set (0.004 sec)*

---