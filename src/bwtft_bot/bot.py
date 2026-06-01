import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, Message
from aiogram.client.default import DefaultBotProperties

from bwtft_bot.config import settings
from bwtft_bot.db import async_session, init_db
from bwtft_bot.keyboards import (
    page_actions_keyboard,
    pages_keyboard,
    photos_done_keyboard,
    remove_keyboard,
    story_review_keyboard,
    theme_options_keyboard,
)
from bwtft_bot.llm import (
    create_character_prompt,
    generate_book,
    generate_story,
    generate_theme_options,
    revise_story,
    transcribe_voice,
)
from bwtft_bot.repository import get_page_payload, save_book
from bwtft_bot.schemas import StoryDraft, StoryThemeOption, StoryThemeOptions
from bwtft_bot.telegram_text import html_escape, split_message


class BookFlow(StatesGroup):
    waiting_child_info = State()
    choosing_theme = State()
    waiting_custom_theme = State()
    waiting_pages_count = State()
    reviewing_story = State()
    waiting_story_revision = State()
    waiting_character_photos = State()
    browsing_pages = State()


router = Router()
ALBUM_WAIT_SECONDS = 1.5
CHARACTER_PROMPT_TIMEOUT_SECONDS = 45
BOOK_GENERATION_TIMEOUT_SECONDS = 240
THEME_GENERATION_TIMEOUT_SECONDS = 120
VOICE_TRANSCRIPTION_TIMEOUT_SECONDS = 150
logger = logging.getLogger(__name__)


@dataclass
class PhotoAlbumBuffer:
    messages: list[Message] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


photo_album_buffers: dict[str, PhotoAlbumBuffer] = {}


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.text and event.text.startswith("/id"):
            return await handler(event, data)

        allowed_user_ids = settings.allowed_user_ids
        if not allowed_user_ids:
            return await handler(event, data)
        if event.from_user and event.from_user.id in allowed_user_ids:
            return await handler(event, data)

        logger.info(
            "Blocked unauthorized user id=%s username=%s",
            event.from_user.id if event.from_user else None,
            event.from_user.username if event.from_user else None,
        )
        await event.answer("Доступ к этому боту ограничен. Обратитесь к администратору.")
        return None


async def download_message_photo(message: Message, bot: Bot) -> tuple[bytes, str] | None:
    if not message.photo:
        return None
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buffer = await bot.download_file(file.file_path)
    if buffer is None:
        return None
    buffer.seek(0)
    return buffer.read(), "image/jpeg"


async def download_voice(message: Message, bot: Bot) -> bytes | None:
    if not message.voice:
        return None
    file = await bot.get_file(message.voice.file_id)
    buffer = await bot.download_file(file.file_path)
    if buffer is None:
        return None
    buffer.seek(0)
    return buffer.read()


async def add_photos_to_state(
    state: FSMContext,
    bot: Bot,
    photo_messages: list[Message],
) -> int:
    data = await state.get_data()
    photos: list[tuple[bytes, str]] = data.get("character_photos", [])
    added = 0
    for photo_message in photo_messages:
        downloaded = await download_message_photo(photo_message, bot)
        if downloaded is not None:
            photos.append(downloaded)
            added += 1
    await state.update_data(character_photos=photos)
    return added


def format_story(story: StoryDraft) -> str:
    return "\n\n".join(
        f"Страница {page.page_number}\n{page.page_text}" for page in story.pages
    )


def theme_to_text(option: StoryThemeOption) -> str:
    summary = "\n".join(f"- {sentence}" for sentence in option.summary)
    return f"{option.number}. {option.title}\n{summary}"


def format_theme_options(options: list[StoryThemeOption]) -> str:
    return "\n\n".join(theme_to_text(option) for option in options)


def selected_theme_text(option: StoryThemeOption) -> str:
    return f"{option.title}\n" + "\n".join(option.summary)


async def send_theme_options(message: Message, options: list[StoryThemeOption]) -> None:
    await message.answer(
        "Я предложил 5 тематик сказки на основе информации о ребёнке. "
        "Выберите вариант или нажмите «Ещё варианты».",
    )
    await message.answer(
        format_theme_options(options),
        reply_markup=theme_options_keyboard(options),
        parse_mode=None,
    )


async def send_story_review(message: Message, story: StoryDraft) -> None:
    await message.answer("Сказка готова. Ниже текст, разбитый по страницам.")
    chunks = split_message(format_story(story))
    for chunk in chunks:
        await message.answer(chunk, parse_mode=None)
    await message.answer(
        "Проверьте сказку. Если нужны правки, нажмите «Редактировать». "
        "Если всё устраивает, нажмите «Подтвердить сказку».",
        reply_markup=story_review_keyboard(),
    )


async def collect_character_photos(
    message: Message,
    state: FSMContext,
    bot: Bot,
    photo_messages: list[Message],
) -> None:
    current_state = await state.get_state()
    if current_state != BookFlow.waiting_character_photos.state:
        return

    added = await add_photos_to_state(state, bot, photo_messages)
    if added == 0:
        await message.answer("Не удалось скачать фото. Попробуйте отправить их ещё раз.")
        return

    data = await state.get_data()
    total = len(data.get("character_photos", []))
    await message.answer(
        f"Получил фото: {added}. Всего загружено: {total}.\n"
        "Можно загрузить ещё фотографии персонажей, животных, игрушек или нажать «Готово, создать промпты».",
        reply_markup=photos_done_keyboard(),
    )


async def process_album_after_delay(
    album_key: str,
    state: FSMContext,
    bot: Bot,
) -> None:
    try:
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
    except asyncio.CancelledError:
        return
    album = photo_album_buffers.pop(album_key, None)
    if album is None or not album.messages:
        return
    await collect_character_photos(album.messages[-1], state, bot, album.messages)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BookFlow.waiting_child_info)
    await message.answer(
        "Привет! Отправьте одним сообщением всё, что хотите учесть о ребёнке: "
        "имя, возраст, город, семью, любимые игрушки, интересы, мечты, страхи "
        "и любые пожелания к сказке.",
        reply_markup=remove_keyboard(),
    )


@router.message(Command("new"))
@router.message(F.text.startswith("/new"))
async def new_book(message: Message, state: FSMContext) -> None:
    await start(message, state)


@router.message(Command("help"))
@router.message(F.text.startswith("/help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Команды бота:\n\n"
        "/start — начать новую книгу\n"
        "/new — начать заново\n"
        "/cancel — сбросить текущий сценарий\n"
        "/id — показать ваш Telegram user ID\n"
        "/help — показать эту подсказку\n\n"
        "Сценарий: описание ребёнка → выбор темы → количество страниц → сказка → редактура → "
        "подтверждение → фото персонажей → промпты страниц.",
    )


@router.message(Command("id"))
@router.message(F.text.startswith("/id"))
async def id_command(message: Message) -> None:
    if not message.from_user:
        await message.answer("Не удалось определить Telegram user ID.")
        return

    await message.answer(
        f"Ваш Telegram user ID: <code>{message.from_user.id}</code>\n\n"
        "Этот ID можно добавить в ADMIN_USER_IDS для доступа к боту."
    )


@router.message(Command("cancel"))
@router.message(F.text.startswith("/cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Текущий сценарий сброшен. Чтобы начать новую книгу, нажмите /start.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_child_info, F.text)
async def collect_child_info(message: Message, state: FSMContext) -> None:
    await state.update_data(child_info=message.text)
    await message.answer(
        "Отлично. Сейчас предложу 5 вариантов тематики сказки на основе этой информации."
    )
    try:
        options = await asyncio.wait_for(
            generate_theme_options(message.text),
            timeout=THEME_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to generate theme options")
        await message.answer(
            "Не получилось придумать варианты тематики через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Попробуйте отправить информацию о ребёнке ещё раз."
        )
        return

    await state.update_data(
        theme_options_json=options.model_dump_json(),
        theme_excluded_titles=[option.title for option in options.options],
    )
    await state.set_state(BookFlow.choosing_theme)
    await send_theme_options(message, options.options)


@router.message(BookFlow.choosing_theme, F.text == "Ещё варианты")
async def more_theme_options(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    child_info = data["child_info"]
    excluded_titles = data.get("theme_excluded_titles", [])
    await message.answer("Придумываю ещё 5 вариантов тематики...")
    try:
        options = await asyncio.wait_for(
            generate_theme_options(child_info, excluded_titles),
            timeout=THEME_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to generate more theme options")
        await message.answer(
            "Не получилось придумать ещё варианты.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
        return

    await state.update_data(
        theme_options_json=options.model_dump_json(),
        theme_excluded_titles=excluded_titles + [option.title for option in options.options],
    )
    await send_theme_options(message, options.options)


@router.message(BookFlow.choosing_theme, F.text.startswith("Выбрать "))
async def select_theme(message: Message, state: FSMContext) -> None:
    try:
        selected_number = int(message.text.removeprefix("Выбрать ").strip())
    except ValueError:
        await message.answer("Выберите тематику кнопкой в меню.")
        return
    data = await state.get_data()
    options = StoryThemeOptions.model_validate_json(data["theme_options_json"])
    selected = next((option for option in options.options if option.number == selected_number), None)
    if selected is None:
        await message.answer("Вариант не найден. Нажмите «Ещё варианты» или выберите другой.")
        return

    await state.update_data(selected_theme=selected_theme_text(selected))
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        f"Выбрана тематика:\n\n{theme_to_text(selected)}\n\n"
        "Сколько страниц сделать в книге? Введите число от 10 и больше, например 12, 16, 20 или 24.",
        parse_mode=None,
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.choosing_theme, F.text == "Своя тема")
async def request_custom_theme(message: Message, state: FSMContext) -> None:
    await state.set_state(BookFlow.waiting_custom_theme)
    await message.answer(
        "Напишите свою тему сказки в свободной форме. Можно коротко или подробно: "
        "жанр, герои, настроение, конфликт, финал, важные пожелания.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_custom_theme, F.text)
async def collect_custom_theme(message: Message, state: FSMContext) -> None:
    custom_theme = message.text.strip()
    if len(custom_theme) < 3:
        await message.answer("Опишите тему чуть подробнее.")
        return

    await state.update_data(selected_theme=f"Своя тема заказчика:\n{custom_theme}")
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        f"Принял свою тему:\n\n{custom_theme}\n\n"
        "Сколько страниц сделать в книге? Введите число от 10 и больше, например 12, 16, 20 или 24.",
        parse_mode=None,
    )


@router.message(BookFlow.waiting_custom_theme)
async def custom_theme_fallback(message: Message) -> None:
    await message.answer("Отправьте свою тему сказки обычным текстом.")


@router.message(BookFlow.choosing_theme)
async def choose_theme_fallback(message: Message) -> None:
    await message.answer("Выберите тематику кнопкой в меню, нажмите «Своя тема» или «Ещё варианты».")


@router.message(BookFlow.waiting_pages_count, F.text)
async def collect_pages_count(message: Message, state: FSMContext) -> None:
    try:
        pages_count = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число страниц цифрами, например 16.")
        return

    if pages_count < 10:
        await message.answer("Минимальное количество страниц — 10. Введите число от 10 и больше.")
        return

    data = await state.get_data()
    child_info = data["child_info"]
    selected_theme = data["selected_theme"]

    await message.answer("Генерирую сказку и разбиваю её по страницам. Это может занять немного времени.")
    try:
        story = await asyncio.wait_for(
            generate_story(child_info, selected_theme, pages_count),
            timeout=BOOK_GENERATION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.exception("Timed out while generating story")
        await message.answer(
            "OpenAI слишком долго не отвечает на генерацию сказки.\n\n"
            "Попробуйте меньше страниц или проверьте OPENAI_TEXT_MODEL. "
            "Для проверки лучше временно поставить gpt-5.2 или gpt-4.1-mini."
        )
        return
    except Exception as exc:
        logger.exception("Failed to generate story")
        await message.answer(
            "Не получилось сгенерировать сказку через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Проверьте OPENAI_API_KEY и точное имя модели в OPENAI_TEXT_MODEL, "
            "затем введите количество страниц ещё раз."
        )
        return

    await state.update_data(story_json=story.model_dump_json())
    await state.set_state(BookFlow.reviewing_story)
    await send_story_review(message, story)


@router.message(BookFlow.reviewing_story, F.text == "Редактировать")
async def request_story_revision(message: Message, state: FSMContext) -> None:
    await state.set_state(BookFlow.waiting_story_revision)
    await message.answer(
        "Напишите правки к сказке текстом или отправьте голосовое сообщение. "
        "Можно просить любые изменения: сильно сократить, переписать сюжет, убрать персонажа, "
        "изменить финал, сделать проще или полностью поменять настроение.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_story_revision, F.text)
async def collect_story_revision_text(message: Message, state: FSMContext) -> None:
    await apply_story_revision(message, state, message.text)


@router.message(BookFlow.waiting_story_revision, F.voice)
async def collect_story_revision_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    voice_bytes = await download_voice(message, bot)
    if voice_bytes is None:
        await message.answer("Не удалось скачать голосовое сообщение. Попробуйте ещё раз.")
        return
    await message.answer("Расшифровываю голосовые правки...")
    try:
        revision_text = await asyncio.wait_for(
            transcribe_voice(voice_bytes),
            timeout=VOICE_TRANSCRIPTION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to transcribe voice revision")
        await message.answer(
            "Не получилось распознать голосовое сообщение.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Можно отправить правки обычным текстом."
        )
        return
    await message.answer(f"Понял правки:\n\n{revision_text}")
    await apply_story_revision(message, state, revision_text)


@router.message(BookFlow.waiting_story_revision)
async def collect_story_revision_fallback(message: Message) -> None:
    await message.answer("Отправьте правки текстом или голосовым сообщением.")


async def apply_story_revision(message: Message, state: FSMContext, revision_text: str) -> None:
    data = await state.get_data()
    story = StoryDraft.model_validate_json(data["story_json"])
    await message.answer("Вношу правки в сказку...")
    try:
        revised = await asyncio.wait_for(
            revise_story(
                data["child_info"],
                data["selected_theme"],
                story,
                revision_text,
            ),
            timeout=BOOK_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to revise story")
        await message.answer(
            "Не получилось отредактировать сказку через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Попробуйте сформулировать правки короче."
        )
        return
    await state.update_data(story_json=revised.model_dump_json())
    await state.set_state(BookFlow.reviewing_story)
    await message.answer(
        "Готово. Я буду анализировать персонажей и делать промпты уже по этой последней версии сказки."
    )
    await send_story_review(message, revised)


@router.message(BookFlow.reviewing_story, F.text == "Подтвердить сказку")
async def approve_story(message: Message, state: FSMContext) -> None:
    await state.set_state(BookFlow.waiting_character_photos)
    await state.update_data(character_photos=[])
    await message.answer(
        "Сказка подтверждена.\n\n"
        "Загрузите фотографии персонажей и важных объектов этой сказки: ребёнка, животных, игрушек, "
        "членов семьи или других референсов. Можно отправить одну фотографию, альбом или несколько сообщений.\n\n"
        "Когда всё загрузите, нажмите «Готово, создать промпты».",
        reply_markup=photos_done_keyboard(),
    )


@router.message(BookFlow.reviewing_story)
async def reviewing_story_fallback(message: Message) -> None:
    await message.answer("Выберите «Редактировать» или «Подтвердить сказку» кнопкой в меню.")


@router.message(BookFlow.waiting_character_photos, F.photo)
async def collect_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        album_key = f"{message.chat.id}:{message.media_group_id}"
        album = photo_album_buffers.setdefault(album_key, PhotoAlbumBuffer())
        album.messages.append(message)
        if album.task is not None:
            album.task.cancel()
        album.task = asyncio.create_task(process_album_after_delay(album_key, state, bot))
        return

    await collect_character_photos(message, state, bot, [message])


@router.message(BookFlow.waiting_character_photos, F.text == "Готово, создать промпты")
async def finish_photos_and_create_prompts(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = data.get("character_photos", [])
    if not photos:
        await message.answer("Сначала загрузите хотя бы одну фотографию персонажа или важного объекта.")
        return

    story = StoryDraft.model_validate_json(data["story_json"])
    child_info = data["child_info"]
    selected_theme = data["selected_theme"]

    await message.answer(
        "Создаю общий character/correction prompt по фотографиям персонажей...",
        reply_markup=remove_keyboard(),
    )
    try:
        character_prompt = await asyncio.wait_for(
            create_character_prompt(photos, child_info, selected_theme, story),
            timeout=CHARACTER_PROMPT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.exception("Timed out while creating character prompt")
        await message.answer(
            "OpenAI слишком долго не отвечает на анализ фотографий.\n\n"
            "Проверьте OPENAI_VISION_MODEL или попробуйте загрузить меньше фотографий."
        )
        return
    except Exception as exc:
        logger.exception("Failed to create character prompt")
        await message.answer(
            "Не получилось создать character/correction prompt через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
        return

    await message.answer("Создаю Scene Blueprint и финальные промпты для каждой страницы...")
    try:
        generated = await asyncio.wait_for(
            generate_book(child_info, character_prompt, selected_theme, story),
            timeout=BOOK_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to generate prompts")
        await message.answer(
            "Не получилось создать Scene Blueprint и промпты через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Нажмите «Готово, создать промпты» ещё раз или попробуйте уменьшить количество страниц."
        )
        return

    async with async_session() as session:
        book = await save_book(
            session=session,
            user_id=message.from_user.id,
            generated=generated,
            character_prompt_text=character_prompt,
        )

    await state.set_state(BookFlow.browsing_pages)
    await state.update_data(
        current_book_id=book.id,
        current_pages_count=book.pages_count,
        character_photos=[],
    )
    await message.answer(
        f"Промпты готовы: {book.pages_count} страниц. Выберите страницу:",
        reply_markup=pages_keyboard(book.pages_count),
    )


@router.message(BookFlow.waiting_character_photos)
async def collect_photo_fallback(message: Message) -> None:
    await message.answer(
        "Загрузите фотографии персонажей/объектов или нажмите «Готово, создать промпты».",
        reply_markup=photos_done_keyboard(),
    )


@router.message(BookFlow.browsing_pages, F.text == "К меню страниц")
async def show_menu(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pages_count = data["current_pages_count"]
    await message.answer(
        "Выберите страницу:",
        reply_markup=pages_keyboard(pages_count),
    )


@router.message(BookFlow.browsing_pages, F.text.startswith("Страница "))
async def show_page(message: Message, state: FSMContext) -> None:
    try:
        page_number = int(message.text.removeprefix("Страница ").strip())
    except ValueError:
        await message.answer("Выберите страницу кнопкой в меню.")
        return

    data = await state.get_data()
    book_id = data["current_book_id"]

    async with async_session() as session:
        payload = await get_page_payload(session, book_id, page_number)
    if not payload:
        await message.answer("Страница не найдена.")
        return

    page_text, _scene_blueprint, prompt = payload
    text = (
        f"<b>Текст страницы {page_number}</b>\n\n"
        f"{html_escape(page_text)}\n\n"
        f"<b>Финальный промпт</b>\n\n"
        f"{html_escape(prompt)}"
    )
    if len(text) > 3900:
        text = text[:3800] + "\n\n...Промпт слишком длинный для одного сообщения."
    await state.update_data(current_page_number=page_number)
    await message.answer(
        text,
        reply_markup=page_actions_keyboard(),
    )


@router.message(BookFlow.browsing_pages)
async def browsing_pages_fallback(message: Message) -> None:
    await message.answer("Выберите страницу кнопкой в меню.")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Нажмите /start, чтобы начать создание новой книги.")


async def create_dispatcher() -> Dispatcher:
    await init_db()
    dp = Dispatcher()
    dp.message.middleware(AccessMiddleware())
    dp.include_router(router)
    return dp


async def run_bot() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать новую книгу"),
            BotCommand(command="new", description="Начать заново"),
            BotCommand(command="help", description="Помощь и сценарий работы"),
            BotCommand(command="id", description="Показать ваш Telegram user ID"),
            BotCommand(command="cancel", description="Сбросить текущий сценарий"),
        ]
    )
    dp = await create_dispatcher()
    await dp.start_polling(bot)
