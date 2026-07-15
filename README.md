# BWTFT Telegram Bot

Telegram-бот для подготовки персонализированных детских книг:

- собирает свободное описание ребёнка;
- предлагает варианты тематики сказки;
- генерирует сказку на заданное число страниц;
- принимает фотографии персонажей и важных объектов после утверждения сказки;
- создаёт общий `character_prompt` / correction prompt;
- создаёт `scene_blueprints`;
- собирает финальные промпты для иллюстраций;
- создаёт иллюстрации страниц через NVIDIA Flux.1-Dev, если подключён NVIDIA API;
- показывает выборы и страницы через нижнее Telegram-меню.

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Заполните `.env`, затем запустите:

```bash
bwtft-bot
```

## Деплой на Bot Hosting

Для хостинга, который запускает код из GitHub:

1. Укажите команду запуска:

```bash
python main.py
```

2. Добавьте переменные окружения:

```bash
TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
OPENAI_TEXT_MODEL=gpt-5.2
OPENAI_VISION_MODEL=gpt-5.2
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
DATABASE_URL=sqlite+aiosqlite:////app/data/bwtft.sqlite3
ADMIN_USER_IDS=123456789,987654321
NVIDIA_API_KEY=...
NVIDIA_IMAGE_ENDPOINT=https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev
NVIDIA_REFERENCE_IMAGE_ENDPOINT=https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev
NVIDIA_IMAGE_MODEL=black-forest-labs/flux.1-dev
NVIDIA_REFERENCE_IMAGE_MODEL=black-forest-labs/flux.1-kontext-dev
NVIDIA_REFERENCE_IMAGES_MAX=1
NVIDIA_IMAGE_WIDTH=1024
NVIDIA_IMAGE_HEIGHT=1024
```

3. Если хостинг сам устанавливает зависимости, он может использовать `requirements.txt`.

В BotHost базу лучше хранить в `/app/data`, чтобы она сохранялась между обновлениями из Git.
Если в вашем OpenAI-кабинете доступно другое точное API-имя самой новой модели,
достаточно заменить `OPENAI_TEXT_MODEL` и `OPENAI_VISION_MODEL` в переменных окружения.

`ADMIN_USER_IDS` — список Telegram user ID через запятую. Если переменная пустая,
бот доступен всем. Если заполнена, бот отвечает только пользователям из списка.
Чтобы узнать свой ID, напишите боту [@userinfobot](https://t.me/userinfobot)
или [@getmyid_bot](https://t.me/getmyid_bot).

### NVIDIA Flux.1-Dev для генерации иллюстраций

1. Откройте [NVIDIA API Catalog](https://build.nvidia.com/) и войдите в аккаунт NVIDIA.
2. Найдите модель `FLUX.1-dev` / `black-forest-labs/flux.1-dev`.
3. Нажмите кнопку получения API key и скопируйте ключ вида `nvapi-...`.
4. В BotHost откройте проект бота → раздел переменных окружения.
5. Добавьте переменные:

```bash
NVIDIA_API_KEY=nvapi-ваш_ключ
NVIDIA_IMAGE_ENDPOINT=https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev
NVIDIA_REFERENCE_IMAGE_ENDPOINT=https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev
NVIDIA_IMAGE_MODEL=black-forest-labs/flux.1-dev
NVIDIA_REFERENCE_IMAGE_MODEL=black-forest-labs/flux.1-kontext-dev
NVIDIA_REFERENCE_IMAGES_MAX=1
NVIDIA_IMAGE_WIDTH=1024
NVIDIA_IMAGE_HEIGHT=1024
```

6. Сохраните переменные и перезапустите бота на BotHost.

Обычный `flux.1-dev` используется для генерации по тексту. Бот сохраняет загруженные фото
в базе вместе с книгой и сначала пробует отправить первый референс в `flux.1-kontext-dev`.
Но у NVIDIA cloud preview сейчас есть ограничение: поле `image` принимает только встроенный
`example_id`, а не произвольный base64-файл пользователя. Если NVIDIA отклоняет фото-референс,
бот автоматически повторяет генерацию через обычный `flux.1-dev` по текстовому промпту.

Если в карточке модели NVIDIA указан другой endpoint, замените соответствующую переменную:
`NVIDIA_IMAGE_ENDPOINT` для текстовой генерации или `NVIDIA_REFERENCE_IMAGE_ENDPOINT`
для генерации с фото-референсом. Код бота берёт адреса из окружения.
Если `NVIDIA_API_KEY` не задан, кнопка «Создать иллюстрацию» покажет подсказку,
что API нужно добавить в окружение.

## Сценарий

1. `/start`
2. Пользователь отправляет свободное описание ребёнка.
3. Бот предлагает 5 тематик сказки с кнопками выбора, «Свой вариант», «Редактировать тему» и «Предложить ещё».
4. Пользователь выбирает вариант, надиктовывает/вводит свои пожелания к теме или отправляет свой вариант сказки.
   Текстовый «Свой вариант» используется как готовая основа без переписывания, голосовой — как вводные для генерации истории.
   Если в текстовом варианте уже есть заголовки страниц, бот сохраняет эту разбивку и текст без переразметки.
   После AI-редактирования тему можно подтвердить, отредактировать ещё раз или начать выбор заново.
5. Бот просит указать количество страниц, от 10 и больше.
6. Бот генерирует сказку по страницам и предлагает «Редактировать» или «Подтвердить сказку».
7. После подтверждения пользователь загружает фотографии всех персонажей и важных объектов.
8. Бот создаёт общий character/correction prompt, Scene Blueprint, финальные промпты и показывает меню страниц.
9. Для каждой страницы можно создать иллюстрацию через NVIDIA Flux.1-Dev,
   запросить три варианта одного иллюстрируемого момента, выбрать сцену
   и свободно отредактировать финальный промпт текстом или голосом.

## Команды

- `/start` — начать новую книгу.
- `/new` — начать заново.
- `/help` — показать подсказку.
- `/commands` — показать список всех команд.
- `/id` — показать Telegram user ID для `ADMIN_USER_IDS`.
- `/cancel` — сбросить текущий сценарий.

## Данные

SQLite-база создаётся автоматически. Основные сущности соответствуют ТЗ:

- `Book`
- `StoryPage`
- `SceneBlueprint`
- `CharacterPrompt`
- `StyleTemplate`
- `FinalPrompt`

Финальный промпт содержит только конкретную сцену выбранной страницы и короткие требования к изображению.
Он показывается прямо в сообщении и копируется стандартным нажатием Telegram.
