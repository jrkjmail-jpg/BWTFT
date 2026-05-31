from pydantic import BaseModel, Field, field_validator


class GeneratedPage(BaseModel):
    page_number: int
    page_text: str = Field(min_length=100)
    scene_blueprint: str = Field(min_length=100)


class GeneratedBook(BaseModel):
    child_name: str | None = None
    pages: list[GeneratedPage]

    @field_validator("pages")
    @classmethod
    def validate_page_numbers(cls, pages: list[GeneratedPage]) -> list[GeneratedPage]:
        numbers = [page.page_number for page in pages]
        if numbers != list(range(1, len(pages) + 1)):
            raise ValueError("page_number values must be sequential from 1")
        return pages
