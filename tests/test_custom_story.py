from bwtft_bot.custom_story import parse_prescribed_pages
from bwtft_bot.prompts import custom_story_split_prompt
from bwtft_bot.schemas import GeneratedPage, StoryDraftPage


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
