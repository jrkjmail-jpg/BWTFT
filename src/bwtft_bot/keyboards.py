from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bwtft_bot.schemas import StoryThemeOption


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def theme_options_keyboard(options: list[StoryThemeOption]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=f"Выбрать {option.number}")]
        for option in options
    ]
    rows.append([KeyboardButton(text="Ещё варианты")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def story_review_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Редактировать"),
                KeyboardButton(text="Дальше"),
            ]
        ],
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
            [KeyboardButton(text="Скопировать промпт")],
            [KeyboardButton(text="К меню страниц")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
