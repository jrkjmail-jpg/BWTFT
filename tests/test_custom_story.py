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
