# 🎧 VK Uncensor Bridge

Телеграм-бот для экспорта плейлистов из ВКонтакте с автоматическим поиском «незацензуренных» версий треков на mp3party.net и доставкой в приватный Telegram-канал.

## ⚡ Быстрый старт

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 2. Настройка

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

Обязательные параметры:
- `BOT_TOKEN` — токен Telegram-бота (получить у @BotFather)
- `VK_TOKEN` — токен VK с доступом к аудио (через Kate Mobile)
- `VK_USER_ID` — ваш ID VK

### 3. Запуск

```bash
python bot/main.py
```

## 📖 Использование

1. Создайте приватный канал в Telegram
2. Добавьте бота как администратора (права: «Отправка сообщений»)
3. Отправьте `/link` в канале — бот вернёт числовой `chat_id`
4. Отправьте боту в ЛС: `<chat_id> <ссылка на плейлист VK>`

**Пример:**
```
-1001234567890 https://vk.com/music/playlist/123_456
```

## 🏗 Архитектура

```
vk_music/
├── bot/                     # Telegram-бот (aiogram 3.x)
│   ├── main.py             # Точка входа
│   └── handlers/           # Хендлеры команд
├── core/                    # Бизнес-логика
│   ├── scraper/mp3party.py # Скрапер mp3party.net
│   ├── matcher.py          # Fuzzy-матчинг (rapidfuzz)
│   ├── vk_fetcher.py       # VK API wrapper
│   ├── downloader.py       # Скачивание с retry
│   └── cache_manager.py    # Кэш: hash → файл + file_id
├── services/                # Сервисы
│   ├── tg_sender.py        # Отправка аудио в канал
│   └── processor.py        # Оркестрация обработки
├── utils/                   # Утилиты
│   ├── config.py           # Загрузка .env
│   ├── logger.py           # structlog
│   ├── helpers.py          # Хеши, парсинг URL
│   └── storage.py          # users.json
└── data/                    # Данные (в .gitignore)
    ├── cache/              # MP3 + метаданные
    └── users.json          # Привязки каналов
```

## ⚠️ Дисклеймер

Проект создан в образовательных целях. Не предназначен для коммерческого использования. Кэш автоматически очищается каждые 7 дней. Авторские права на музыку принадлежат правообладателям.
