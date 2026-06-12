from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bwtft_bot.schemas import SceneOption, StoryThemeOption


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def theme_options_keyboard(options: list[StoryThemeOption]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=f"Выбрать {option.number}")]
        for option in options
    ]
    rows.append([KeyboardButton(text="Редактировать тему")])
    rows.append([KeyboardButton(text="Предложить ещё")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def custom_theme_review_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подтвердить тему")],
            [KeyboardButton(text="Редактировать ещё")],
            [KeyboardButton(text="Начать выбор заново")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def story_review_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Редактировать"),
                KeyboardButton(text="Подтвердить сказку"),
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def photos_done_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Готово, создать промпты")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def pages_keyboard(pages_count: int) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for page_number in range(1, pages_count + 1):
        row.append(KeyboardButton(text=f"Страница {page_number}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def page_actions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Варианты сцены")],
            [KeyboardButton(text="Редактировать промпт")],
            [KeyboardButton(text="К меню страниц")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def scene_options_keyboard(options: list[SceneOption]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=f"Выбрать сцену {option.number}")]
        for option in options
    ]
    rows.append([KeyboardButton(text="Назад к промпту")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )
