from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def pages_keyboard(book_id: int, pages_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for page_number in range(1, pages_count + 1):
        row.append(
            InlineKeyboardButton(
                text=f"Страница {page_number}",
                callback_data=f"page:{book_id}:{page_number}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_actions_keyboard(book_id: int, page_number: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Скопировать промпт",
                    callback_data=f"copy:{book_id}:{page_number}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="К меню страниц",
                    callback_data=f"menu:{book_id}",
                )
            ],
        ]
    )
