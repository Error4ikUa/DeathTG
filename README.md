# DeathTG

```text
██████╗░███████╗░█████╗░████████╗██╗░░██╗        ████████╗░██████╗░
██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██║░░██║        ╚══██╔══╝██╔════╝░
██║░░██║█████╗░░███████║░░░██║░░░███████║        ░░░██║░░░██║░░██╗░
██║░░██║██╔══╝░░██╔══██║░░░██║░░░██╔══██║        ░░░██║░░░██║░░╚██╗
██████╔╝███████╗██║░░██║░░░██║░░░██║░░██║        ░░░██║░░░╚██████╔╝
╚═════╝░╚══════╝╚═╝░░╚═╝░░░╚═╝░░░╚═╝░░╚═╝        ░░░╚═╝░░░░╚═════╝░
```

DeathTG — модульный Telegram userbot на Telethon с веб-панелью управления в стиле зелёного Matrix-интерфейса.

> Важно: userbot работает от твоего Telegram-аккаунта. Не грузи левые модули от непонятных типов — модуль получает доступ к клиенту и может писать/читать от имени аккаунта.

## Что уже есть

- подключение к Telegram-аккаунту через Telethon;
- конфиг через `.env`;
- модульная система;
- декоратор `@command` для команд;
- `.help` — список модулей и команд;
- `.modules` — список загруженных модулей;
- `.dlmod <link>` — скачать и загрузить модуль по ссылке;
- `.loadmod <file.py>` — загрузить модуль из папки `modules`;
- `.unloadmod <name>` — выгрузить модуль;
- `.ping` и `.alive` для проверки работы;
- security scanner для модулей;
- web dashboard: login, status, modules, upload, download, delete, scanner, update;
- Matrix-style зелёный визуал на фоне.

## Установка

```bash
git clone https://github.com/Error4ikUa/DeathTG.git
cd DeathTG
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Настройка

Открой `.env` и вставь данные:

```env
API_ID=123456
API_HASH=your_api_hash_here
SESSION_NAME=deathtg
COMMAND_PREFIX=.
OWNER_ID=

PANEL_PASSWORD=change_me_now
PANEL_SECRET=change_me_to_random_long_string
```

`API_ID` и `API_HASH` берутся с https://my.telegram.org/apps.

## Запуск userbot

```bash
python main.py
```

Первый запуск попросит номер, код Telegram и, если включена, 2FA-пароль.

## Запуск web panel

```bash
python panel.py
```

Открой:

```text
http://127.0.0.1:8080
```

Для Oracle Cloud лучше держать панель за reverse proxy и не светить её голой в интернет без HTTPS и нормального пароля.

## Основные команды

```text
.help                 показать все модули и команды
.help core            показать команды конкретного модуля
.modules              список загруженных модулей
.dlmod <link>         скачать и загрузить модуль по ссылке
.loadmod <file.py>    загрузить модуль из папки modules
.unloadmod <name>     выгрузить модуль
.ping                 проверить задержку
.alive                статус DeathTG
```

## Security scanner

Перед установкой модуль проверяется на опасные маркеры и AST-паттерны:

- удаление Telegram-аккаунта;
- выход из аккаунта;
- попытки трогать `.session`;
- `eval` / `exec`;
- запуск системных команд;
- массовое удаление файлов и папок;
- подозрительные сетевые/SSH/FTP импорты.

Это не замена ручному аудиту, но типичную вредоносную хуйню оно режет ещё до загрузки.

## Пример модуля

```python
from deathtg.command import command
from deathtg.ui import ok


@command("hello", description="Сказать привет", usage=".hello")
async def hello_cmd(event, args):
    await event.edit(ok("привет, DeathTG жив"), parse_mode="html")
```

Сохрани как `modules/hello.py`, потом в Telegram напиши:

```text
.loadmod hello.py
.hello
```

## Структура

```text
DeathTG/
├── main.py
├── panel.py
├── requirements.txt
├── .env.example
├── deathtg/
│   ├── app.py
│   ├── command.py
│   ├── config.py
│   ├── loader.py
│   ├── main.py
│   ├── registry.py
│   ├── security.py
│   ├── ui.py
│   ├── modules/
│   │   ├── core.py
│   │   └── system.py
│   └── panel/
│       ├── server.py
│       ├── templates/
│       └── static/
└── modules/
    └── example.py
```
