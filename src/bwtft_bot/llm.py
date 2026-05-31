import base64
import json
from io import BytesIO
from collections.abc import Sequence

from openai import AsyncOpenAI

from bwtft_bot.config import settings
from bwtft_bot.prompts import (
    CHARACTER_STYLE,
    scene_blueprints_prompt,
    story_generation_prompt,
    story_revision_prompt,
)
from bwtft_bot.schemas import GeneratedBook, StoryDraft


client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0)


PhotoInput = tuple[bytes, str]


async def create_character_prompt(photos: Sequence[PhotoInput]) -> str:
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
                    "Ты создаёшь нейтральное описание внешности ребёнка для "
                    "персонажа детской книги. Не идентифицируй личность. "
                    "Не делай чувствительных выводов. Пиши по-русски."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "На фотографиях один и тот же ребёнок или несколько ракурсов. "
                            "Составь одно постоянное описание внешности персонажа, "
                            "используя только устойчивые черты, которые видны на фото: "
                            "примерный возраст, форма лица, волосы, глаза, выражение "
                            "лица, телосложение, заметные нейтральные особенности."
                        ),
                    },
                    *image_items,
                ],
            },
        ],
        max_completion_tokens=500,
    )
    description = response.choices[0].message.content or ""
    return f"{description.strip()}\n\n{CHARACTER_STYLE}"


async def generate_story(child_info: str, character_prompt: str, pages_count: int) -> StoryDraft:
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
                "content": story_generation_prompt(child_info, character_prompt, pages_count),
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
    character_prompt: str,
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
                    "Строго соблюдай JSON-схему и сохраняй количество страниц."
                ),
            },
            {
                "role": "user",
                "content": story_revision_prompt(
                    child_info,
                    character_prompt,
                    current_story_json,
                    revision_request,
                ),
            },
        ],
        max_completion_tokens=8000,
    )
    raw = response.choices[0].message.content or "{}"
    revised = StoryDraft.model_validate(json.loads(raw))
    if len(revised.pages) != len(current_story.pages):
        raise ValueError(f"Expected {len(current_story.pages)} pages, got {len(revised.pages)}")
    return revised


async def generate_book(child_info: str, character_prompt: str, story: StoryDraft) -> GeneratedBook:
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
                "content": scene_blueprints_prompt(child_info, character_prompt, story_json),
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
