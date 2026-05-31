from bwtft_bot.telegram_text import split_message


def test_split_message_keeps_short_text_intact():
    assert split_message("hello") == ["hello"]


def test_split_message_splits_long_text():
    text = "a" * 5000
    chunks = split_message(text)
    assert len(chunks) == 2
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == text
