from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bwtft_bot.db import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    child_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    story_text: Mapped[str] = mapped_column(Text, default="")
    pages_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pages: Mapped[list["StoryPage"]] = relationship(cascade="all, delete-orphan")
    blueprints: Mapped[list["SceneBlueprint"]] = relationship(cascade="all, delete-orphan")
    character_prompt: Mapped["CharacterPrompt"] = relationship(cascade="all, delete-orphan")
    style_template: Mapped["StyleTemplate"] = relationship(cascade="all, delete-orphan")
    final_prompts: Mapped[list["FinalPrompt"]] = relationship(cascade="all, delete-orphan")


class StoryPage(Base):
    __tablename__ = "story_pages"
    __table_args__ = (UniqueConstraint("book_id", "page_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    page_text: Mapped[str] = mapped_column(Text)


class SceneBlueprint(Base):
    __tablename__ = "scene_blueprints"
    __table_args__ = (UniqueConstraint("book_id", "page_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    scene_description: Mapped[str] = mapped_column(Text)


class CharacterPrompt(Base):
    __tablename__ = "character_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), unique=True)
    character_prompt: Mapped[str] = mapped_column(Text)


class StyleTemplate(Base):
    __tablename__ = "style_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), unique=True)
    style_template: Mapped[str] = mapped_column(Text)


class FinalPrompt(Base):
    __tablename__ = "final_prompts"
    __table_args__ = (UniqueConstraint("book_id", "page_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    final_prompt: Mapped[str] = mapped_column(Text)
