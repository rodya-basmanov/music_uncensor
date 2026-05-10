# 🎧 VK Music Uncensor

Телеграм-бот для экспорта плейлистов из ВКонтакте с автоматическим поиском **незацензуренных** версий треков и доставкой в приватный Telegram-канал.

**SoundCloud** — основной источник (uncensored, без VPN), **mp3party** — fallback.

## ✨ Возможности

- 🔍 **SoundCloud** — поиск и скачивание uncensored-версий через `yt-dlp` (работает в РФ без VPN)
- 🎵 **mp3party** — fallback-источник если SoundCloud не нашёл или трек DRM-защищён
- 🧠 **Умный выбор трека** — приоритет: трек от самого артиста > не-ремикс > ближайший по длительности
- 🚫 **Фильтрация мусора** — slowed, reverb, sped up, remix автоматически исключаются
- 📝 **Оригинальные названия** — в Telegram отправляются с `feat.`, `prod. by` и т.д., а для поиска — очищенные
- 💾 **Кэширование** — SHA256-хеш по artist+title, повторная отправка через `file_id` без скачивания
- 🎯 **Fuzzy-матчинг** — `rapidfuzz` для неточного совпадения треков
- ⏱ **Проверка длительности** — если VK указал duration, треки с неподходящей длиной отбрасываются
- 📎 **Прямой запрос** — можно отправить `Артист - Название` текстом, без файла

## ⚡ Быстрый старт

### 1. Установка

```bash
pip install -r requirements.txt
playwright install chromium   # для fallback-скачивания с mp3party
```

> **ffmpeg** — нужен для конвертации в MP3. Если не установлен системно, `imageio-ffmpeg` поставит автоматически.

### 2. Настройка

```bash
cp .env.example .env
```

Обязательные:
- `BOT_TOKEN` — токен бота от [@BotFather](https://t.me/BotFather)

Опционально:
- `TELEGRAM_PROXY_URL` — прокси для Telegram API (socks5://user:pass@host:port)
- `FUZZY_THRESHOLD` — порог fuzzy-матчинга (по умолчанию 85)

### 3. Экспорт плейлиста из VK

1. Откройте плейлист ВКонтакте в браузере
2. **F12** → вкладка **Console**
3. Вставьте содержимое `script.js`, нажмите Enter
4. Скрипт прокрутит плейлист и скачает `.txt` файл с треками

### 4. Запуск

```bash
python bot/main.py
```

## 📖 Использование

1. Создайте приватный Telegram-канал
2. Добавьте бота как администратора (права: отправка сообщений)
3. Отправьте `/link` в канале → бот вернёт `chat_id`
4. Отправьте боту в ЛС:
   - **Файл** `.txt` с треками (из `script.js`)
   - Или **текст** вида:

```
-1001234567890
J.ROUH feat. SALUKI - SALUT
Макс Корж - Неважно (Acoustic)
масло черного тмина - тысячу раз спокойной ночи
```

Первая строка — `chat_id` канала, далее треки в формате `Артист - Название [длительность]`.

### Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие |
| `/link` | Привязать канал (отправить в канале) |
| `/status` | Статус текущей задачи |

## 🔎 Как работает поиск

```
Трек из VK: "J.ROUH feat. SALUKI - SALUT [220]"
                    │
                    ▼
        Очистка для поиска:
        artist: "J.ROUH"  title: "SALUT"
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
     SoundCloud           mp3party
     scsearch5:           search API
     "J.ROUH SALUT"       "J.ROUH SALUT"
          │                   │
     5 результатов        fuzzy match
     выбор лучшего:       среди кандидатов
     1. от артиста
     2. не remix
     3. по duration
          │                   │
     ┌────┴────┐         fallback если
     ▼         ▼         SoundCloud не нашёл
  Скачано    DRM/404
     │         │
     ▼         ▼
  Telegram   mp3party
  (original) (censored)
```

**Оригинальные названия** (с `feat.`, `prod. by`) сохраняются для отображения в Telegram. Очистка применяется **только** к поисковым запросам.

## 🏗 Архитектура

```
vk_music/
├── bot/                        # Telegram-бот (aiogram 3.x)
│   ├── main.py                # Точка входа, polling
│   └── handlers/
│       ├── start.py           # /start
│       ├── link.py            # /link — привязка канала
│       ├── playlist.py        # Обработка плейлиста/текста
│       └── status.py          # /status
├── core/                       # Бизнес-логика
│   ├── scraper/
│   │   ├── soundcloud.py      # SoundCloud через yt-dlp (основной)
│   │   └── mp3party.py        # mp3party.net (fallback)
│   ├── matcher.py             # Fuzzy-матчинг (rapidfuzz)
│   ├── downloader.py          # Скачивание с retry + Playwright fallback
│   └── cache_manager.py       # Кэш: hash → файл + telegram file_id
├── services/
│   ├── tg_sender.py           # Отправка аудио в Telegram-канал
│   └── processor.py           # Оркестрация: поиск → скачивание → отправка
├── utils/
│   ├── config.py              # Загрузка .env
│   ├── logger.py              # structlog
│   ├── helpers.py             # Хеши, парсинг, clean_artist/title_for_search
│   └── storage.py             # users.json — привязки каналов
├── script.js                  # Экспорт плейлиста из VK (F12 → Console)
├── .env.example               # Шаблон конфигурации
└── requirements.txt
```

## ⚙️ Конфигурация

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `BOT_TOKEN` | — | Токен Telegram-бота |
| `TELEGRAM_PROXY_URL` | — | SOCKS5 прокси для Telegram API |
| `FUZZY_THRESHOLD` | 85 | Порог fuzzy-матчинга (0–100) |
| `MAX_PLAYLIST_SIZE` | 100 | Максимум треков в плейлисте |
| `CACHE_DIR` | ./data/cache | Папка кэша |
| `CACHE_TTL_DAYS` | 7 | Срок жизни кэша |
| `MAX_CACHE_SIZE_GB` | 20 | Макс. размер кэша |
| `RATE_LIMIT_SEARCH_SEC` | 2 | Задержка между поисками |
| `RATE_LIMIT_DOWNLOAD_SEC` | 2 | Задержка между скачиваниями |
| `LOG_LEVEL` | INFO | Уровень логирования |

## 📜 Лицензия

MIT
