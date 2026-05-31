import base64
import json
from collections.abc import Sequence

from openai import AsyncOpenAI

from bwtft_bot.config import settings
from bwtft_bot.prompts import CHARACTER_STYLE, book_generation_prompt
from bwtft_bot.schemas import GeneratedBook


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


async def generate_book(child_info: str, character_prompt: str, pages_count: int) -> GeneratedBook:
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
                "content": book_generation_prompt(child_info, character_prompt, pages_count),
            },
        ],
        max_completion_tokens=12000,
    )
    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    generated = GeneratedBook.model_validate(data)
    if len(generated.pages) != pages_count:
        raise ValueError(f"Expected {pages_count} pages, got {len(generated.pages)}")
    return generated
