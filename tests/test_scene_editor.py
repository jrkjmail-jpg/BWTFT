from bwtft_bot.prompts import final_prompt_revision_prompt, scene_options_prompt


def test_scene_options_require_one_visual_moment():
    prompt = scene_options_prompt(
        "Даша проснулась, почистила зубы и пошла гулять с собакой.",
        "Даша утром дома.",
    )

    assert "ровно один выразительный момент" in prompt
    assert "не пытайся объединить их в один кадр" in prompt


def test_prompt_revision_preserves_single_scene_rule():
    prompt = final_prompt_revision_prompt(
        "Девочка гуляет с собакой в парке.",
        "Добавь воздушные шары.",
    )

    assert "одна иллюстрация — один момент" in prompt
    assert "приложенные фотографии" in prompt
