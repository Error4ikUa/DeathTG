# DeathTG

```text
██████╗░███████╗░█████╗░████████╗██╗░░██╗        ████████╗░██████╗░
██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██║░░██║        ╚══██╔══╝██╔════╝░
██║░░██║█████╗░░███████║░░░██║░░░███████║        ░░░██║░░░██║░░██╗░
██║░░██║██╔══╝░░██╔══██║░░░██║░░░██╔══██║        ░░░██║░░░██║░░╚██╗
██████╔╝███████╗██║░░██║░░░██║░░░██║░░██║        ░░░██║░░░╚██████╔╝
╚═════╝░╚══════╝╚═╝░░╚═╝░░░╚═╝░░░╚═╝░░╚═╝        ░░░╚═╝░░░░╚═════╝░
```

DeathTG — модульный Telegram userbot на Telethon. Идея: чистое ядро, свои модули, загрузка по ссылке или из файла, нормальный фундамент под будущие мощные модули: YouTube downloader, music search, file tools, OSINT helpers и другое.

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
- пример внешнего модуля `modules/example.py`.

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

Открой `.env` и вставь данные с https://my.telegram.org/apps:

```env
API_ID=123456
API_HASH=your_api_hash_here
SESSION_NAME=deathtg
COMMAND_PREFIX=.
OWNER_ID=
```

Первый запуск попросит номер, код Telegram и, если включена, 2FA-пароль.

## Запуск

```bash
python main.py
```

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

## Планы

- менеджер обновлений ядра;
- репозиторий модулей;
- безопасный режим загрузки модулей;
- YouTube downloader модуль;
- music search модуль;
- красивое меню модулей;
- автодокументация команд;
- hot reload без перезапуска;
- настройки модулей.

## Структура

```text
DeathTG/
├── main.py
├── requirements.txt
├── .env.example
├── deathtg/
│   ├── app.py
│   ├── command.py
│   ├── config.py
│   ├── loader.py
│   ├── main.py
│   ├── registry.py
│   ├── ui.py
│   └── modules/
│       ├── core.py
│       └── system.py
└── modules/
    └── example.py
```
