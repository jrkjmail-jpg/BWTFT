from bwtft_bot.prompts import STORY_ENGINE, story_generation_prompt, story_revision_prompt


def test_story_engine_contains_required_emotional_arc():
    required = [
        "Тепло",
        "Чудо",
        "Путешествие",
        "Помощь",
        "2–4 маленьких испытания",
        "Доброе решение",
        "Волшебный результат",
        "Возвращение к теплу",
    ]
    assert all(stage in STORY_ENGINE for stage in required)


def test_story_engine_is_used_for_generation_and_revision():
    generation = story_generation_prompt("ребёнок", "тема", 12)
    revision = story_revision_prompt("ребёнок", "тема", "{}", "сократи")

    assert STORY_ENGINE in generation
    assert STORY_ENGINE in revision
