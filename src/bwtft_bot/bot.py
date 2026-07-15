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
from aiogram.types import BotCommand, BufferedInputFile, Message
from aiogram.client.default import DefaultBotProperties

from bwtft_bot.config import settings
from bwtft_bot.custom_story import parse_prescribed_pages
from bwtft_bot.db import async_session, init_db
from bwtft_bot.keyboards import (
    custom_theme_review_keyboard,
    page_actions_keyboard,
    pages_keyboard,
    photos_done_keyboard,
    remove_keyboard,
    scene_options_keyboard,
    story_review_keyboard,
    theme_options_keyboard,
)
from bwtft_bot.llm import (
    create_character_prompt,
    generate_book,
    generate_custom_theme,
    generate_scene_options,
    generate_story,
    generate_theme_options,
    revise_final_prompt,
    revise_story,
    split_custom_story,
    transcribe_voice,
)
from bwtft_bot.nvidia_images import NvidiaImageError, generate_image
from bwtft_bot.prompts import final_prompt
from bwtft_bot.repository import (
    get_book_reference_photos,
    get_page_payload,
    save_book,
    update_final_prompt,
    update_page_scene,
)
from bwtft_bot.schemas import SceneOptions, StoryDraft, StoryThemeOption, StoryThemeOptions
from bwtft_bot.telegram_text import split_message


class BookFlow(StatesGroup):
    waiting_child_info = State()
    choosing_theme = State()
    waiting_own_story = State()
    waiting_custom_theme = State()
    reviewing_custom_theme = State()
    waiting_pages_count = State()
    reviewing_story = State()
    waiting_story_revision = State()
    waiting_character_photos = State()
    browsing_pages = State()
    choosing_page_scene = State()
    waiting_prompt_revision = State()


router = Router()
ALBUM_WAIT_SECONDS = 1.5
CHARACTER_PROMPT_TIMEOUT_SECONDS = 45
BOOK_GENERATION_TIMEOUT_SECONDS = 240
THEME_GENERATION_TIMEOUT_SECONDS = 120
VOICE_TRANSCRIPTION_TIMEOUT_SECONDS = 150
IMAGE_GENERATION_TIMEOUT_SECONDS = 210
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
        "Выберите вариант, нажмите «Свой вариант», «Предложить ещё» или «Редактировать тему».",
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


async def send_page_prompt(
    message: Message,
    page_number: int,
    prompt: str,
) -> None:
    chunks = split_message(f"Промпт страницы {page_number}:\n\n{prompt}", limit=3900)
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            parse_mode=None,
            reply_markup=page_actions_keyboard() if index == len(chunks) - 1 else None,
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
@router.message(Command("commands"))
@router.message(F.text.startswith("/commands"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Команды бота:\n\n"
        "/start — начать новую книгу\n"
        "/new — начать заново\n"
        "/cancel — сбросить текущий сценарий\n"
        "/id — показать ваш Telegram user ID\n"
        "/commands — показать список всех команд\n"
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


@router.message(BookFlow.choosing_theme, F.text == "Предложить ещё")
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
        await message.answer("Вариант не найден. Нажмите «Предложить ещё» или выберите другой.")
        return

    await state.update_data(
        selected_theme=selected_theme_text(selected),
        own_story_text=None,
    )
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        f"Выбрана тематика:\n\n{theme_to_text(selected)}\n\n"
        "Сколько страниц сделать в книге? Введите число от 10 и больше, например 12, 16, 20 или 24.",
        parse_mode=None,
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.choosing_theme, F.text == "Свой вариант")
async def request_own_story(message: Message, state: FSMContext) -> None:
    await state.update_data(own_story_text=None)
    await state.set_state(BookFlow.waiting_own_story)
    await message.answer(
        "Отправьте свой вариант сказки текстом или голосовым сообщением.\n\n"
        "Если отправите текст — я возьму его как готовую основу без переписывания "
        "и только разобью по страницам.\n"
        "Если отправите голосовое — я использую его как вводные и построю сказку вокруг них.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_own_story, F.text)
async def collect_own_story_text(message: Message, state: FSMContext) -> None:
    own_story_text = message.text.strip()
    if len(own_story_text) < 10:
        await message.answer("Пришлите текст сказки чуть подробнее.")
        return

    prescribed_story = parse_prescribed_pages(own_story_text)
    if prescribed_story is not None:
        await state.update_data(
            own_story_text=own_story_text,
            selected_theme="Свой текст пользователя с готовой разбивкой по страницам.",
            story_json=prescribed_story.model_dump_json(),
        )
        await state.set_state(BookFlow.reviewing_story)
        await message.answer(
            "Вижу, что в тексте уже прописаны страницы. "
            "Оставляю текст как есть и просто разделяю его по этим страницам."
        )
        await send_story_review(message, prescribed_story)
        return

    await state.update_data(
        own_story_text=own_story_text,
        selected_theme="Свой текст пользователя. Использовать как готовую основу сказки.",
    )
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        "Принял ваш текст как готовую основу сказки.\n\n"
        "Сколько страниц сделать в книге? Введите число от 10 и больше, например 12, 16, 20 или 24.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_own_story, F.voice)
async def collect_own_story_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    voice_bytes = await download_voice(message, bot)
    if voice_bytes is None:
        await message.answer("Не удалось скачать голосовое сообщение. Попробуйте ещё раз.")
        return

    await message.answer("Расшифровываю ваш вариант сказки...")
    try:
        user_request = await asyncio.wait_for(
            transcribe_voice(voice_bytes),
            timeout=VOICE_TRANSCRIPTION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to transcribe own story")
        await message.answer(
            "Не получилось распознать голосовое сообщение.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Можно отправить свой вариант обычным текстом."
        )
        return

    await state.update_data(
        own_story_text=None,
        selected_theme=f"Свой голосовой вариант пользователя:\n{user_request}",
    )
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        f"Понял голосовые вводные:\n\n{user_request}\n\n"
        "Сколько страниц сделать в книге? Введите число от 10 и больше, например 12, 16, 20 или 24.",
        parse_mode=None,
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_own_story)
async def own_story_fallback(message: Message) -> None:
    await message.answer("Отправьте свой вариант сказки текстом или голосовым сообщением.")


@router.message(BookFlow.choosing_theme, F.text == "Редактировать тему")
@router.message(BookFlow.choosing_theme, F.text == "Своя тема")
async def request_custom_theme(message: Message, state: FSMContext) -> None:
    await state.update_data(custom_theme_draft=None)
    await state.set_state(BookFlow.waiting_custom_theme)
    await message.answer(
        "Опишите тему сказки текстом или голосовым сообщением: какие нужны персонажи, "
        "что должно произойти, настроение, приключение и финал. AI соберёт из этого одну тему.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_custom_theme, F.text)
async def collect_custom_theme(message: Message, state: FSMContext) -> None:
    user_request = message.text.strip()
    if len(user_request) < 3:
        await message.answer("Опишите тему чуть подробнее.")
        return
    await build_custom_theme(message, state, user_request)


@router.message(BookFlow.waiting_custom_theme, F.voice)
async def collect_custom_theme_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    voice_bytes = await download_voice(message, bot)
    if voice_bytes is None:
        await message.answer("Не удалось скачать голосовое сообщение. Попробуйте ещё раз.")
        return
    await message.answer("Расшифровываю пожелания к теме...")
    try:
        user_request = await asyncio.wait_for(
            transcribe_voice(voice_bytes),
            timeout=VOICE_TRANSCRIPTION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to transcribe custom theme")
        await message.answer(
            "Не получилось распознать голосовое сообщение.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Можно отправить пожелания обычным текстом."
        )
        return
    await message.answer(f"Понял пожелания:\n\n{user_request}")
    await build_custom_theme(message, state, user_request)


async def build_custom_theme(message: Message, state: FSMContext, user_request: str) -> None:
    data = await state.get_data()
    current_draft = data.get("custom_theme_draft")
    if current_draft:
        user_request = (
            f"Текущий черновик темы:\n{current_draft}\n\n"
            f"Новые пожелания пользователя:\n{user_request}\n\n"
            "Переработай текущий черновик с учётом новых пожеланий."
        )
    await message.answer("Собираю одну тематику на основе ваших пожеланий...")
    try:
        theme = await asyncio.wait_for(
            generate_custom_theme(data["child_info"], user_request),
            timeout=THEME_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to generate custom theme")
        await message.answer(
            "Не получилось собрать тему через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Попробуйте описать пожелания ещё раз."
        )
        return

    theme_text = selected_theme_text(theme)
    await state.update_data(
        selected_theme=theme_text,
        own_story_text=None,
        custom_theme_draft=theme_text,
        custom_theme_json=theme.model_dump_json(),
    )
    await state.set_state(BookFlow.reviewing_custom_theme)
    await message.answer(
        f"Черновик темы:\n\n{theme_to_text(theme)}\n\n"
        "Подтвердите тему, отредактируйте её ещё раз или начните выбор заново.",
        parse_mode=None,
        reply_markup=custom_theme_review_keyboard(),
    )


@router.message(BookFlow.waiting_custom_theme)
async def custom_theme_fallback(message: Message) -> None:
    await message.answer("Отправьте пожелания к теме текстом или голосовым сообщением.")


@router.message(BookFlow.reviewing_custom_theme, F.text == "Подтвердить тему")
async def approve_custom_theme(message: Message, state: FSMContext) -> None:
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        "Тема подтверждена.\n\n"
        "Сколько страниц сделать в книге? Введите число от 10 и больше, например 12, 16, 20 или 24.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.reviewing_custom_theme, F.text == "Редактировать ещё")
async def edit_custom_theme_again(message: Message, state: FSMContext) -> None:
    await state.set_state(BookFlow.waiting_custom_theme)
    await message.answer(
        "Отправьте новые правки к теме текстом или голосовым сообщением. "
        "AI сохранит текущий черновик и переработает его по вашим пожеланиям.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.reviewing_custom_theme, F.text == "Начать выбор заново")
async def restart_theme_choice(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    options = StoryThemeOptions.model_validate_json(data["theme_options_json"])
    await state.update_data(
        selected_theme=None,
        own_story_text=None,
        custom_theme_draft=None,
        custom_theme_json=None,
    )
    await state.set_state(BookFlow.choosing_theme)
    await send_theme_options(message, options.options)


@router.message(BookFlow.reviewing_custom_theme)
async def reviewing_custom_theme_fallback(message: Message) -> None:
    await message.answer(
        "Выберите в меню: «Подтвердить тему», «Редактировать ещё» или «Начать выбор заново»."
    )


@router.message(BookFlow.choosing_theme)
async def choose_theme_fallback(message: Message) -> None:
    await message.answer(
        "Выберите тематику кнопкой в меню, нажмите «Свой вариант», "
        "«Редактировать тему» или «Предложить ещё»."
    )


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
    own_story_text = data.get("own_story_text")

    if own_story_text:
        prescribed_story = parse_prescribed_pages(own_story_text)
        if prescribed_story is not None:
            await state.update_data(story_json=prescribed_story.model_dump_json())
            await state.set_state(BookFlow.reviewing_story)
            await message.answer(
                "В тексте уже есть готовая разбивка по страницам. "
                "Оставляю её без переразметки."
            )
            await send_story_review(message, prescribed_story)
            return
        progress_text = "Разбиваю ваш текст на страницы без переписывания. Это может занять немного времени."
    else:
        progress_text = "Генерирую сказку и разбиваю её по страницам. Это может занять немного времени."
    await message.answer(progress_text)
    try:
        if own_story_text:
            story = await asyncio.wait_for(
                split_custom_story(child_info, own_story_text, pages_count),
                timeout=BOOK_GENERATION_TIMEOUT_SECONDS,
            )
        else:
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
            "Не получилось подготовить сказку через OpenAI.\n\n"
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
            reference_photos=photos,
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

    _page_text, _scene_blueprint, prompt = payload
    await state.update_data(current_page_number=page_number)
    await send_page_prompt(message, page_number, prompt)


@router.message(BookFlow.browsing_pages, F.text == "Варианты сцены")
async def request_scene_options(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    page_number = data.get("current_page_number")
    if not page_number:
        await message.answer("Сначала выберите страницу.")
        return

    async with async_session() as session:
        payload = await get_page_payload(session, data["current_book_id"], page_number)
    if not payload:
        await message.answer("Страница не найдена.")
        return

    page_text, current_scene, _prompt = payload
    await message.answer(
        "Анализирую страницу и выбираю три разных одиночных момента для иллюстрации...",
        reply_markup=remove_keyboard(),
    )
    try:
        options = await asyncio.wait_for(
            generate_scene_options(page_text, current_scene),
            timeout=THEME_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to generate scene options")
        await message.answer(
            "Не получилось создать варианты сцены.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
        return

    await state.update_data(scene_options_json=options.model_dump_json())
    await state.set_state(BookFlow.choosing_page_scene)
    text = "\n\n".join(
        f"{option.number}. {option.title}\n{option.scene_description}"
        for option in options.options
    )
    await message.answer(
        f"Выберите один момент для иллюстрации:\n\n{text}",
        parse_mode=None,
        reply_markup=scene_options_keyboard(options.options),
    )


@router.message(BookFlow.choosing_page_scene, F.text.startswith("Выбрать сцену "))
async def select_page_scene(message: Message, state: FSMContext) -> None:
    try:
        selected_number = int(message.text.removeprefix("Выбрать сцену ").strip())
    except ValueError:
        await message.answer("Выберите сцену кнопкой в меню.")
        return

    data = await state.get_data()
    options = SceneOptions.model_validate_json(data["scene_options_json"])
    selected = next(
        (option for option in options.options if option.number == selected_number),
        None,
    )
    if selected is None:
        await message.answer("Вариант сцены не найден.")
        return

    page_number = data["current_page_number"]
    prompt = final_prompt("", selected.scene_description, page_number)
    async with async_session() as session:
        await update_page_scene(
            session,
            data["current_book_id"],
            page_number,
            selected.scene_description,
            prompt,
        )

    await state.set_state(BookFlow.browsing_pages)
    await state.update_data(scene_options_json=None)
    await message.answer(f"Выбрана сцена: {selected.title}")
    await send_page_prompt(message, page_number, prompt)


@router.message(BookFlow.choosing_page_scene, F.text == "Назад к промпту")
async def return_to_page_prompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    page_number = data["current_page_number"]
    async with async_session() as session:
        payload = await get_page_payload(session, data["current_book_id"], page_number)
    if not payload:
        await message.answer("Страница не найдена.")
        return
    await state.set_state(BookFlow.browsing_pages)
    await send_page_prompt(message, page_number, payload[2])


@router.message(BookFlow.choosing_page_scene)
async def choosing_page_scene_fallback(message: Message) -> None:
    await message.answer("Выберите вариант сцены или нажмите «Назад к промпту».")


@router.message(BookFlow.browsing_pages, F.text == "Создать иллюстрацию")
async def create_page_illustration(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    page_number = data.get("current_page_number")
    if not page_number:
        await message.answer("Сначала выберите страницу.")
        return

    async with async_session() as session:
        payload = await get_page_payload(session, data["current_book_id"], page_number)
        reference_photos = await get_book_reference_photos(session, data["current_book_id"])
    if not payload:
        await message.answer("Страница не найдена.")
        return

    _page_text, _scene_blueprint, prompt = payload
    if reference_photos:
        status = (
            "Создаю иллюстрацию через NVIDIA Flux с загруженной референс-картинкой персонажа. "
            "Это может занять немного времени..."
        )
    else:
        status = "Создаю иллюстрацию через NVIDIA Flux. Это может занять немного времени..."
    await message.answer(status)
    try:
        image_bytes, reference_fallback_used = await asyncio.wait_for(
            generate_image(prompt, reference_photos),
            timeout=IMAGE_GENERATION_TIMEOUT_SECONDS,
        )
    except NvidiaImageError as exc:
        logger.exception("NVIDIA image generation failed")
        if "NVIDIA_API_KEY" in str(exc):
            await message.answer(
                "NVIDIA API ещё не настроен.\n\n"
                "Добавьте переменную NVIDIA_API_KEY в окружение BotHost и перезапустите бота."
            )
        else:
            await message.answer(
                "Не получилось создать иллюстрацию через NVIDIA.\n\n"
                f"Техническая ошибка: {exc}"
            )
        return
    except TimeoutError:
        logger.exception("Timed out while generating NVIDIA image")
        await message.answer(
            "NVIDIA слишком долго создаёт иллюстрацию. Попробуйте нажать «Создать иллюстрацию» ещё раз."
        )
        return
    except Exception as exc:
        logger.exception("Unexpected image generation error")
        await message.answer(
            "Не получилось создать иллюстрацию.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
        return

    await message.answer_photo(
        BufferedInputFile(image_bytes, filename=f"page_{page_number}_illustration.png"),
        caption=f"Иллюстрация страницы {page_number}",
        reply_markup=page_actions_keyboard(),
    )
    if reference_fallback_used:
        await message.answer(
            "NVIDIA preview API не принял загруженное фото как референс "
            "(этот endpoint ждёт только встроенный example_id), поэтому я автоматически "
            "создал иллюстрацию по текстовому промпту без загрузки картинки.",
            reply_markup=page_actions_keyboard(),
        )


@router.message(BookFlow.browsing_pages, F.text == "Редактировать промпт")
async def request_prompt_revision(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("current_page_number"):
        await message.answer("Сначала выберите страницу.")
        return
    await state.set_state(BookFlow.waiting_prompt_revision)
    await message.answer(
        "Опишите правки к иллюстрации текстом или голосовым сообщением. "
        "Можно изменить действие, персонажей, детали, окружение, композицию или стиль.",
        reply_markup=remove_keyboard(),
    )


@router.message(BookFlow.waiting_prompt_revision, F.text)
async def collect_prompt_revision_text(message: Message, state: FSMContext) -> None:
    await apply_prompt_revision(message, state, message.text)


@router.message(BookFlow.waiting_prompt_revision, F.voice)
async def collect_prompt_revision_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    voice_bytes = await download_voice(message, bot)
    if voice_bytes is None:
        await message.answer("Не удалось скачать голосовое сообщение. Попробуйте ещё раз.")
        return
    await message.answer("Расшифровываю правки к промпту...")
    try:
        revision_text = await asyncio.wait_for(
            transcribe_voice(voice_bytes),
            timeout=VOICE_TRANSCRIPTION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to transcribe prompt revision")
        await message.answer(
            "Не получилось распознать голосовое сообщение.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
        return
    await message.answer(f"Понял правки:\n\n{revision_text}")
    await apply_prompt_revision(message, state, revision_text)


async def apply_prompt_revision(
    message: Message,
    state: FSMContext,
    revision_text: str,
) -> None:
    data = await state.get_data()
    page_number = data["current_page_number"]
    async with async_session() as session:
        payload = await get_page_payload(session, data["current_book_id"], page_number)
    if not payload:
        await message.answer("Страница не найдена.")
        return

    await message.answer("Редактирую финальный промпт...")
    try:
        revised_prompt = await asyncio.wait_for(
            revise_final_prompt(payload[2], revision_text),
            timeout=THEME_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to revise final prompt")
        await message.answer(
            "Не получилось отредактировать промпт.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
        return

    async with async_session() as session:
        await update_final_prompt(
            session,
            data["current_book_id"],
            page_number,
            revised_prompt,
        )
    await state.set_state(BookFlow.browsing_pages)
    await send_page_prompt(message, page_number, revised_prompt)


@router.message(BookFlow.waiting_prompt_revision)
async def prompt_revision_fallback(message: Message) -> None:
    await message.answer("Отправьте правки к промпту текстом или голосовым сообщением.")


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
            BotCommand(command="commands", description="Показать все команды"),
            BotCommand(command="id", description="Показать ваш Telegram user ID"),
            BotCommand(command="cancel", description="Сбросить текущий сценарий"),
        ]
    )
    dp = await create_dispatcher()
    await dp.start_polling(bot)
