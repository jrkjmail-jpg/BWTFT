from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bwtft_bot.models import (
    Book,
    BookReferenceImage,
    CharacterPrompt,
    FinalPrompt,
    SceneBlueprint,
    StoryPage,
    StyleTemplate,
)
from bwtft_bot.prompts import STYLE_TEMPLATE, final_prompt
from bwtft_bot.schemas import GeneratedBook


async def save_book(
    session: AsyncSession,
    user_id: int,
    generated: GeneratedBook,
    character_prompt_text: str,
    reference_photos: list[tuple[bytes, str]] | None = None,
) -> Book:
    book = Book(
        user_id=user_id,
        child_name=generated.child_name,
        pages_count=len(generated.pages),
        story_text="\n\n".join(page.page_text for page in generated.pages),
    )
    session.add(book)
    await session.flush()

    session.add(CharacterPrompt(book_id=book.id, character_prompt=character_prompt_text))
    session.add(StyleTemplate(book_id=book.id, style_template=STYLE_TEMPLATE))

    for index, (image_bytes, mime_type) in enumerate(reference_photos or [], start=1):
        session.add(
            BookReferenceImage(
                book_id=book.id,
                order_index=index,
                mime_type=mime_type,
                image_bytes=image_bytes,
            )
        )

    for page in generated.pages:
        session.add(
            StoryPage(
                book_id=book.id,
                page_number=page.page_number,
                page_text=page.page_text,
            )
        )
        session.add(
            SceneBlueprint(
                book_id=book.id,
                page_number=page.page_number,
                scene_description=page.scene_blueprint,
            )
        )
        session.add(
            FinalPrompt(
                book_id=book.id,
                page_number=page.page_number,
                final_prompt=final_prompt(
                    character_prompt_text,
                    page.scene_blueprint,
                    page.page_number,
                ),
            )
        )

    await session.commit()
    await session.refresh(book)
    return book


async def get_page_payload(session: AsyncSession, book_id: int, page_number: int) -> tuple[str, str, str] | None:
    page = await session.scalar(
        select(StoryPage).where(
            StoryPage.book_id == book_id,
            StoryPage.page_number == page_number,
        )
    )
    blueprint = await session.scalar(
        select(SceneBlueprint).where(
            SceneBlueprint.book_id == book_id,
            SceneBlueprint.page_number == page_number,
        )
    )
    prompt = await session.scalar(
        select(FinalPrompt).where(
            FinalPrompt.book_id == book_id,
            FinalPrompt.page_number == page_number,
        )
    )
    if not page or not blueprint or not prompt:
        return None
    return page.page_text, blueprint.scene_description, prompt.final_prompt


async def get_book_reference_photos(session: AsyncSession, book_id: int) -> list[tuple[bytes, str]]:
    rows = (
        await session.scalars(
            select(BookReferenceImage)
            .where(BookReferenceImage.book_id == book_id)
            .order_by(BookReferenceImage.order_index)
        )
    ).all()
    return [(row.image_bytes, row.mime_type) for row in rows]


async def update_page_scene(
    session: AsyncSession,
    book_id: int,
    page_number: int,
    scene_description: str,
    final_prompt_text: str,
) -> None:
    blueprint = await session.scalar(
        select(SceneBlueprint).where(
            SceneBlueprint.book_id == book_id,
            SceneBlueprint.page_number == page_number,
        )
    )
    prompt = await session.scalar(
        select(FinalPrompt).where(
            FinalPrompt.book_id == book_id,
            FinalPrompt.page_number == page_number,
        )
    )
    if not blueprint or not prompt:
        raise ValueError("Page prompt not found")
    blueprint.scene_description = scene_description
    prompt.final_prompt = final_prompt_text
    await session.commit()


async def update_final_prompt(
    session: AsyncSession,
    book_id: int,
    page_number: int,
    final_prompt_text: str,
) -> None:
    prompt = await session.scalar(
        select(FinalPrompt).where(
            FinalPrompt.book_id == book_id,
            FinalPrompt.page_number == page_number,
        )
    )
    if not prompt:
        raise ValueError("Final prompt not found")
    prompt.final_prompt = final_prompt_text
    await session.commit()


async def get_book(session: AsyncSession, book_id: int) -> Book | None:
    return await session.get(Book, book_id)
