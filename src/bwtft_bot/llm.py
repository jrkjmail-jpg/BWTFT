import base64
import json
from io import BytesIO
from collections.abc import Sequence

from openai import AsyncOpenAI

from bwtft_bot.config import settings
from bwtft_bot.prompts import (
    characters_prompt,
    custom_theme_prompt,
    final_prompt_revision_prompt,
    scene_options_prompt,
    scene_blueprints_prompt,
    story_generation_prompt,
    story_revision_prompt,
    theme_options_prompt,
)
from bwtft_bot.schemas import (
    GeneratedBook,
    SceneOptions,
    StoryDraft,
    StoryThemeOption,
    StoryThemeOptions,
)


client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0)


PhotoInput = tuple[bytes, str]


async def create_character_prompt(
    photos: Sequence[PhotoInput],
    child_info: str,
    selected_theme: str,
    story: StoryDraft,
) -> str:
    image_items = []
    for photo_bytes, mime_type in photos:
        encoded = base64.b64encode(photo_bytes).decode("ascii")
        image_items.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                    "detail": "low",
                },
            }
        )

    response = await client.with_options(timeout=60.0).chat.completions.create(
        model=settings.openai_vision_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты создаёшь нейтральный character/correction prompt для "
                    "персонажей и визуальных референсов детской книги. "
                    "Не идентифицируй личности. Не делай чувствительных выводов. "
                    "Пиши по-русски."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": characters_prompt(
                            child_info,
                            selected_theme,
                            json.dumps(story.model_dump(), ensure_ascii=False),
                        ),
                    },
                    *image_items,
                ],
            },
        ],
        max_completion_tokens=1500,
    )
    description = response.choices[0].message.content or ""
    return description.strip()


async def generate_theme_options(
    child_info: str,
    excluded_titles: list[str] | None = None,
) -> StoryThemeOptions:
    response = await client.with_options(timeout=120.0).chat.completions.create(
        model=settings.openai_text_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты креативный редактор детских книг. "
                    "Предлагай разные, добрые и визуально богатые тематики."
                ),
            },
            {
                "role": "user",
                "content": theme_options_prompt(child_info, excluded_titles),
            },
        ],
        max_completion_tokens=3000,
    )
    raw = response.choices[0].message.content or "{}"
    return StoryThemeOptions.model_validate(json.loads(raw))


async def generate_custom_theme(child_info: str, user_request: str) -> StoryThemeOption:
    response = await client.with_options(timeout=120.0).chat.completions.create(
        model=settings.openai_text_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты креативный редактор детских книг. Создай одну тему, "
                    "строго следуя пожеланиям пользователя."
                ),
            },
            {
                "role": "user",
                "content": custom_theme_prompt(child_info, user_request),
            },
        ],
        max_completion_tokens=1200,
    )
    raw = response.choices[0].message.content or "{}"
    return StoryThemeOption.model_validate(json.loads(raw))


async def generate_story(
    child_info: str,
    selected_theme: str,
    pages_count: int,
) -> StoryDraft:
    response = await client.with_options(timeout=240.0).chat.completions.create(
        model=settings.openai_text_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты профессиональный автор детских книг и арт-директор. "
                    "Строго соблюдай JSON-схему и количество страниц."
                ),
            },
            {
                "role": "user",
                "content": story_generation_prompt(
                    child_info,
                    selected_theme,
                    pages_count,
                ),
            },
        ],
        max_completion_tokens=8000,
    )
    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    generated = StoryDraft.model_validate(data)
    if len(generated.pages) != pages_count:
        raise ValueError(f"Expected {pages_count} pages, got {len(generated.pages)}")
    return generated


async def revise_story(
    child_info: str,
    selected_theme: str,
    current_story: StoryDraft,
    revision_request: str,
) -> StoryDraft:
    current_story_json = json.dumps(current_story.model_dump(), ensure_ascii=False)
    response = await client.with_options(timeout=240.0).chat.completions.create(
        model=settings.openai_text_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты профессиональный редактор детских книг. "
                    "Пожелания автора имеют высший приоритет: можно сильно сокращать, "
                    "переписывать сюжет и менять структуру. Строго соблюдай JSON-схему."
                ),
            },
            {
                "role": "user",
                "content": story_revision_prompt(
                    child_info,
                    selected_theme,
                    current_story_json,
                    revision_request,
                ),
            },
        ],
        max_completion_tokens=8000,
    )
    raw = response.choices[0].message.content or "{}"
    revised = StoryDraft.model_validate(json.loads(raw))
    return revised


async def generate_scene_options(page_text: str, current_scene: str) -> SceneOptions:
    response = await client.with_options(timeout=120.0).chat.completions.create(
        model=settings.openai_text_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты режиссёр иллюстрированной детской книги. "
                    "Каждый вариант должен быть одним ясным кадром, а не монтажом событий."
                ),
            },
            {
                "role": "user",
                "content": scene_options_prompt(page_text, current_scene),
            },
        ],
        max_completion_tokens=2500,
    )
    raw = response.choices[0].message.content or "{}"
    return SceneOptions.model_validate(json.loads(raw))


async def revise_final_prompt(current_prompt: str, revision_request: str) -> str:
    response = await client.with_options(timeout=120.0).chat.completions.create(
        model=settings.openai_text_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты редактор промптов для книжных иллюстраций. "
                    "Верни только один короткий готовый промпт."
                ),
            },
            {
                "role": "user",
                "content": final_prompt_revision_prompt(current_prompt, revision_request),
            },
        ],
        max_completion_tokens=1800,
    )
    prompt = (response.choices[0].message.content or "").strip().strip('"')
    if len(prompt) > 2800:
        prompt = prompt[:2800].rsplit(". ", 1)[0].strip() + "."
    return prompt


async def generate_book(
    child_info: str,
    character_prompt: str,
    selected_theme: str,
    story: StoryDraft,
) -> GeneratedBook:
    story_json = json.dumps(story.model_dump(), ensure_ascii=False)
    response = await client.with_options(timeout=240.0).chat.completions.create(
        model=settings.openai_text_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты профессиональный арт-директор детских книг. "
                    "Строго соблюдай JSON-схему и не меняй текст страниц."
                ),
            },
            {
                "role": "user",
                "content": scene_blueprints_prompt(
                    child_info,
                    character_prompt,
                    selected_theme,
                    story_json,
                ),
            },
        ],
        max_completion_tokens=12000,
    )
    raw = response.choices[0].message.content or "{}"
    generated = GeneratedBook.model_validate(json.loads(raw))
    if len(generated.pages) != len(story.pages):
        raise ValueError(f"Expected {len(story.pages)} pages, got {len(generated.pages)}")
    return generated


async def transcribe_voice(voice_bytes: bytes) -> str:
    voice_file = BytesIO(voice_bytes)
    voice_file.name = "revision.ogg"
    transcription = await client.with_options(timeout=120.0).audio.transcriptions.create(
        model=settings.openai_transcribe_model,
        file=voice_file,
        language="ru",
    )
    return transcription.text.strip()
