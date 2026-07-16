from bwtft_bot.custom_story import (
    append_text_to_story,
    extract_append_text,
    parse_prescribed_pages,
    split_plain_text_into_pages,
)
from bwtft_bot.prompts import custom_story_split_prompt
from bwtft_bot.schemas import GeneratedPage, StoryDraft, StoryDraftPage


def test_custom_story_split_prompt_preserves_author_text():
    prompt = custom_story_split_prompt("Аня, 5 лет", "Аня нашла светящуюся ракушку.", 10)

    assert "не переписывай сказку" in prompt
    assert "не добавляй новые события" in prompt
    assert "максимально дословно" in prompt


def test_short_custom_story_pages_are_valid():
    draft_page = StoryDraftPage(page_number=1, page_text="Аня улыбнулась.")
    generated_page = GeneratedPage(
        page_number=1,
        page_text="Аня улыбнулась.",
        scene_blueprint="Аня стоит у окна в тёплой комнате, мягко улыбается. "
        "Свет падает сбоку, рядом лежит светящаяся ракушка, атмосфера спокойная и добрая.",
    )

    assert draft_page.page_text == "Аня улыбнулась."
    assert generated_page.page_text == "Аня улыбнулась."


def test_prescribed_pages_are_split_without_rewriting():
    text = """Страница 1
Аня проснулась и увидела свет на подоконнике.

Страница 2
Свет оказался маленькой звёздочкой."""

    story = parse_prescribed_pages(text)

    assert story is not None
    assert [page.page_number for page in story.pages] == [1, 2]
    assert story.pages[0].page_text == "Аня проснулась и увидела свет на подоконнике."
    assert story.pages[1].page_text == "Свет оказался маленькой звёздочкой."


def test_prescribed_inline_page_headers_are_split_without_rewriting():
    text = """Страница №1: Аня нашла ракушку.
Страница №2: Ракушка тихо засияла."""

    story = parse_prescribed_pages(text)

    assert story is not None
    assert story.pages[0].page_text == "Аня нашла ракушку."
    assert story.pages[1].page_text == "Ракушка тихо засияла."


def test_prescribed_numbered_pages_are_split_without_rewriting():
    text = """1. Аня открыла окно.
2. В комнату влетел тёплый свет."""

    story = parse_prescribed_pages(text)

    assert story is not None
    assert story.pages[0].page_text == "Аня открыла окно."
    assert story.pages[1].page_text == "В комнату влетел тёплый свет."


def test_prescribed_number_sign_pages_are_split_without_rewriting():
    text = """№1
Аня держит книгу.

№2
На странице светятся котята."""

    story = parse_prescribed_pages(text)

    assert story is not None
    assert story.pages[0].page_text == "Аня держит книгу."
    assert story.pages[1].page_text == "На странице светятся котята."


def test_plain_custom_story_is_split_without_rewriting_text():
    text = "Аня открыла окно; свет улыбнулся. Потом она взяла книгу: тихо-тихо."

    story = split_plain_text_into_pages(text, 2)

    assert story.pages[0].page_text + " " + story.pages[1].page_text == text
    assert ";" in story.pages[0].page_text
    assert ":" in story.pages[1].page_text


def test_append_text_command_preserves_added_text():
    revision = "Продолжи сказку этим текстом: Аня сказала: «Я вернусь»; и улыбнулась."

    assert extract_append_text(revision) == "Аня сказала: «Я вернусь»; и улыбнулась."


def test_append_text_to_story_adds_page_without_rewriting():
    story = StoryDraft(
        pages=[StoryDraftPage(page_number=1, page_text="Аня открыла окно.")]
    )
    append_text = "Аня сказала: «Я вернусь»; и улыбнулась."

    updated = append_text_to_story(story, append_text)

    assert [page.page_number for page in updated.pages] == [1, 2]
    assert updated.pages[1].page_text == append_text
