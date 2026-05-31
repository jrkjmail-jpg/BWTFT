import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.client.default import DefaultBotProperties

from bwtft_bot.config import settings
from bwtft_bot.db import async_session, init_db
from bwtft_bot.keyboards import page_actions_keyboard, pages_keyboard, story_review_keyboard
from bwtft_bot.llm import create_character_prompt, generate_book, generate_story, revise_story, transcribe_voice
from bwtft_bot.repository import get_book, get_page_payload, save_book
from bwtft_bot.schemas import StoryDraft
from bwtft_bot.telegram_text import html_escape, split_message


class BookFlow(StatesGroup):
    waiting_child_info = State()
    waiting_photo = State()
    waiting_pages_count = State()
    reviewing_story = State()
    waiting_story_revision = State()


router = Router()
ALBUM_WAIT_SECONDS = 1.5
CHARACTER_PROMPT_TIMEOUT_SECONDS = 45
BOOK_GENERATION_TIMEOUT_SECONDS = 240
VOICE_TRANSCRIPTION_TIMEOUT_SECONDS = 150
logger = logging.getLogger(__name__)


@dataclass
class PhotoAlbumBuffer:
    messages: list[Message] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


photo_album_buffers: dict[str, PhotoAlbumBuffer] = {}


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


def format_story(story: StoryDraft) -> str:
    return "\n\n".join(
        f"Страница {page.page_number}\n{page.page_text}" for page in story.pages
    )


async def send_story_review(message: Message, story: StoryDraft) -> None:
    await message.answer("Сказка готова. Ниже текст, разбитый по страницам.")
    chunks = split_message(format_story(story))
    for chunk in chunks:
        await message.answer(chunk, parse_mode=None)
    await message.answer(
        "Проверьте сказку. Если нужны правки, нажмите «Редактировать». "
        "Если всё устраивает, нажмите «Дальше», и я создам Scene Blueprint и промпты.",
        reply_markup=story_review_keyboard(),
    )


async def finish_character_prompt(
    message: Message,
    state: FSMContext,
    bot: Bot,
    photo_messages: list[Message],
) -> None:
    current_state = await state.get_state()
    if current_state != BookFlow.waiting_photo.state:
        return

    photos = []
    for photo_message in photo_messages:
        downloaded = await download_message_photo(photo_message, bot)
        if downloaded is not None:
            photos.append(downloaded)

    if not photos:
        await message.answer("Не удалось скачать фото. Попробуйте отправить их ещё раз.")
        return

    await message.answer(
        f"Получил фото: {len(photos)}. Создаю одно постоянное описание внешности персонажа..."
    )
    try:
        character_prompt = await asyncio.wait_for(
            create_character_prompt(photos),
            timeout=CHARACTER_PROMPT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.exception("Timed out while creating character prompt")
        await message.answer(
            "OpenAI слишком долго не отвечает на описание фото.\n\n"
            "Чаще всего это из-за имени модели или сетевого доступа на хостинге. "
            "Проверьте OPENAI_VISION_MODEL. Для проверки лучше временно поставить "
            "gpt-5.2 или gpt-4.1-mini и отправить фото ещё раз."
        )
        return
    except Exception as exc:
        logger.exception("Failed to create character prompt")
        await message.answer(
            "Не получилось создать описание внешности через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Проверьте OPENAI_API_KEY и точное имя модели в OPENAI_VISION_MODEL, "
            "затем отправьте фото ещё раз."
        )
        return

    await state.update_data(character_prompt=character_prompt)
    await state.set_state(BookFlow.waiting_pages_count)
    await message.answer(
        "Описание персонажа готово. Сколько страниц сделать в книге? "
        "Введите число от 10 и больше, например 12, 16, 20 или 24."
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
    await finish_character_prompt(album.messages[-1], state, bot, album.messages)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BookFlow.waiting_child_info)
    await message.answer(
        "Привет! Отправьте одним сообщением всё, что хотите учесть о ребёнке: "
        "имя, возраст, город, семью, любимые игрушки, интересы, мечты, страхи "
        "и любые пожелания к сказке."
    )


@router.message(BookFlow.waiting_child_info, F.text)
async def collect_child_info(message: Message, state: FSMContext) -> None:
    await state.update_data(child_info=message.text)
    await state.set_state(BookFlow.waiting_photo)
    await message.answer("Отлично. Теперь загрузите фотографию ребёнка.")


@router.message(BookFlow.waiting_photo, F.photo)
async def collect_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        album_key = f"{message.chat.id}:{message.media_group_id}"
        album = photo_album_buffers.setdefault(album_key, PhotoAlbumBuffer())
        album.messages.append(message)
        if album.task is not None:
            album.task.cancel()
        album.task = asyncio.create_task(process_album_after_delay(album_key, state, bot))
        return

    await finish_character_prompt(message, state, bot, [message])


@router.message(BookFlow.waiting_photo)
async def collect_photo_fallback(message: Message) -> None:
    await message.answer(
        "На этом шаге отправьте одну фотографию или альбом из нескольких фотографий ребёнка."
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
    character_prompt = data["character_prompt"]

    await message.answer("Генерирую сказку и разбиваю её по страницам. Это может занять немного времени.")
    try:
        story = await asyncio.wait_for(
            generate_story(child_info, character_prompt, pages_count),
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


@router.callback_query(BookFlow.reviewing_story, F.data == "story:edit")
async def request_story_revision(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookFlow.waiting_story_revision)
    await callback.message.answer(
        "Напишите правки к сказке текстом или отправьте голосовое сообщение. "
        "Например: «сделай финал смешнее» или «добавь больше динозавров»."
    )
    await callback.answer()


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
                data["character_prompt"],
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
    await send_story_review(message, revised)


@router.callback_query(BookFlow.reviewing_story, F.data == "story:approve")
async def approve_story(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    story = StoryDraft.model_validate_json(data["story_json"])
    child_info = data["child_info"]
    character_prompt = data["character_prompt"]

    await callback.message.answer(
        "Отлично. Создаю Scene Blueprint и финальные промпты для каждой страницы..."
    )
    await callback.answer()
    try:
        generated = await asyncio.wait_for(
            generate_book(child_info, character_prompt, story),
            timeout=BOOK_GENERATION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("Failed to generate prompts")
        await callback.message.answer(
            "Не получилось создать Scene Blueprint и промпты через OpenAI.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}\n\n"
            "Нажмите «Дальше» ещё раз или попробуйте уменьшить количество страниц."
        )
        return

    async with async_session() as session:
        book = await save_book(
            session=session,
            user_id=callback.from_user.id,
            generated=generated,
            character_prompt_text=character_prompt,
        )

    await state.clear()
    await callback.message.answer(
        f"Книга готова: {book.pages_count} страниц. Выберите страницу:",
        reply_markup=pages_keyboard(book.id, book.pages_count),
    )


@router.callback_query(F.data.startswith("menu:"))
async def show_menu(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    async with async_session() as session:
        book = await get_book(session, book_id)
    if not book:
        await callback.answer("Книга не найдена.", show_alert=True)
        return
    await callback.message.edit_text(
        "Выберите страницу:",
        reply_markup=pages_keyboard(book.id, book.pages_count),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("page:"))
async def show_page(callback: CallbackQuery) -> None:
    _, book_id_raw, page_raw = callback.data.split(":")
    book_id = int(book_id_raw)
    page_number = int(page_raw)

    async with async_session() as session:
        payload = await get_page_payload(session, book_id, page_number)
    if not payload:
        await callback.answer("Страница не найдена.", show_alert=True)
        return

    page_text, scene_blueprint, prompt = payload
    text = (
        f"<b>Текст страницы {page_number}</b>\n\n"
        f"{html_escape(page_text)}\n\n"
        f"<b>Scene Blueprint</b>\n\n"
        f"{html_escape(scene_blueprint)}\n\n"
        f"<b>Финальный промпт</b>\n\n"
        f"{html_escape(prompt)}"
    )
    if len(text) > 3900:
        text = text[:3800] + "\n\n...Промпт слишком длинный для одного сообщения. Нажмите «Скопировать промпт»."
    await callback.message.edit_text(
        text,
        reply_markup=page_actions_keyboard(book_id, page_number),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("copy:"))
async def copy_prompt(callback: CallbackQuery) -> None:
    _, book_id_raw, page_raw = callback.data.split(":")
    book_id = int(book_id_raw)
    page_number = int(page_raw)

    async with async_session() as session:
        payload = await get_page_payload(session, book_id, page_number)
    if not payload:
        await callback.answer("Промпт не найден.", show_alert=True)
        return
    prompt = payload[2]
    for chunk in split_message(f"Промпт для страницы {page_number}:\n\n{prompt}"):
        await callback.message.answer(chunk, parse_mode=None)
    await callback.answer("Промпт отправлен отдельным сообщением.")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Нажмите /start, чтобы начать создание новой книги.")


async def create_dispatcher() -> Dispatcher:
    await init_db()
    dp = Dispatcher()
    dp.include_router(router)
    return dp


async def run_bot() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = await create_dispatcher()
    await dp.start_polling(bot)
