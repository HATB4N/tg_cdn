# d
hola

## api
### upload
- endpoint: `/upload`
- method: `POST` with img file
- body: ```
        {
          "result": "1",
          "file_id": "<uuid>"
        }
        ```
### retrive
- endpoint: `/content/<uuid>`
- method: `GET`
- body: `raw img bin data with appropriate mimetype`

## build
```bash
(sudo) docker compose up --build (-d)
```

## need
channel id, bot token>= 1, db svr [mysql / mariadb]

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
| bot_id | int(11) | YES | MUL | NULL | |
| created_at | timestamp | NO | MUL | current_timestamp() | |
| updated_at | timestamp | YES | | NULL | on update current_timestamp() |

*7 rows in set (0.012 sec)*

---

`MariaDB [tg_cdn_db]> desc url_caches;`

| Field | Type | Null | Key | Default | Extra |
| :--- | :--- | :--- | :--- | :--- | :--- |
| file_uuid | binary(16) | NO | PRI | NULL | |
| file_path | varchar(50) | NO | | NULL | |
| bot_id | int(small11) | NO | MUL | NULL | |
| file_path_updated_at | timestamp | NO | | current_timestamp() | on update current_timestamp() |

*4 rows in set (0.004 sec)*
