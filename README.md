# DeathTG

```text
██████╗░███████╗░█████╗░████████╗██╗░░██╗        ████████╗░██████╗░
██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██║░░██║        ╚══██╔══╝██╔════╝░
██║░░██║█████╗░░███████║░░░██║░░░███████║        ░░░██║░░░██║░░██╗░
██║░░██║██╔══╝░░██╔══██║░░░██║░░░██╔══██║        ░░░██║░░░██║░░╚██╗
██████╔╝███████╗██║░░██║░░░██║░░░██║░░██║        ░░░██║░░░╚██████╔╝
╚═════╝░╚══════╝╚═╝░░╚═╝░░░╚═╝░░░╚═╝░░╚═╝        ░░░╚═╝░░░░╚═════╝░
```

DeathTG — модульный Telegram userbot на Telethon с неоновой web-панелью, Matrix-фоном, защитой модулей, статистикой, профилем и браузером модулей.

## Что уже есть

- userbot на Telethon;
- setup wizard в web panel;
- зелёный Matrix-style dashboard;
- профиль Telegram в панели;
- статистика использований, дни работы, DTG level/ELO;
- browser модулей под будущий `DTG_Modules/index.json`;
- загрузка модулей по ссылке и файлом;
- protected-модули: `core`, `system`, `antivirus`, `terminal`;
- security scanner перед установкой модулей;
- красивый `.help` с copy-friendly командами;
- `.unloadnod` добавлен как алиас к `.unloadmod`.

## Установка

```bash
git clone https://github.com/Error4ikUa/DeathTG.git
cd DeathTG
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install python-multipart
```

Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install python-multipart
```

## Первый запуск через сайт

```bash
python panel.py
```

Открой:

```text
http://127.0.0.1:8080/setup
```

Введи:

- `API_ID`
- `API_HASH`
- номер Telegram
- имя session, можно оставить `deathtg`
- `PANEL_SECRET`
- `BOT_TOKEN`, если хочешь потом inline-бота и красивые кнопки

После сохранения сайт создаст `.env`. Потом один раз запусти:

```bash
python main.py
```

Telethon спросит код Telegram и 2FA, если она есть. После успешного входа создастся `.session`, и web panel начнёт открывать полноценное меню.

## Запуск

Userbot:

```bash
python main.py
```

Panel:

```bash
python panel.py
```

## Oracle Cloud

По умолчанию panel слушает только localhost:

```text
127.0.0.1:8080
```

Для сервера лучше открыть её через Nginx + HTTPS, а не светить голый порт наружу.

Пример reverse proxy:

```nginx
server {
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Команды

```text
.help                 красивый список модулей и команд
.help core            описание конкретного модуля
.modules              список загруженных модулей
.dlmod <link>         скачать и загрузить модуль
.loadmod <file.py>    загрузить модуль из modules
.unloadmod <name>     выгрузить модуль
.unloadnod <name>     алиас unloadmod
.scanmod <file.py>    проверить модуль антивирусом
.antivirus            статус защиты
.term <command>       безопасный терминал
.ping                 задержка
.alive                статус
```

## Формат DTG_Modules/index.json

```json
{
  "modules": [
    {
      "name": "YouTube Downloader",
      "description": "Скачивает видео и аудио с YouTube",
      "image": "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/youtube/modul.png",
      "link": "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/youtube/youtube.py"
    }
  ]
}
```

## Security scanner

Перед установкой модуль проверяется на:

- удаление Telegram-аккаунта;
- выход из аккаунта;
- попытки трогать `.session`;
- `eval` / `exec`;
- системные команды;
- массовое удаление файлов;
- подозрительные сетевые/SSH/FTP импорты.

Это не магический бог-антивирус, но типичную вредоносную хуйню режет до загрузки.
