import base64

from bwtft_bot.keyboards import page_actions_keyboard
from bwtft_bot.nvidia_images import _find_image_bytes, _find_image_url


def test_nvidia_image_parser_accepts_artifact_base64():
    image_bytes = b"fake-png-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")

    assert _find_image_bytes({"artifacts": [{"base64": encoded}]}) == image_bytes


def test_nvidia_image_parser_accepts_data_url():
    image_bytes = b"fake-png-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")

    assert _find_image_bytes({"image": f"data:image/png;base64,{encoded}"}) == image_bytes


def test_page_actions_include_create_illustration_button():
    keyboard = page_actions_keyboard().keyboard
    labels = [button.text for row in keyboard for button in row]

    assert "Создать иллюстрацию" in labels


def test_nvidia_image_parser_accepts_nested_image_url():
    assert _find_image_url({"artifacts": [{"url": "https://example.com/image.png"}]}) == (
        "https://example.com/image.png"
    )
