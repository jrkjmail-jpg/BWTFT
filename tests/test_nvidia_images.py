import base64

from bwtft_bot.keyboards import page_actions_keyboard
from bwtft_bot.nvidia_images import _build_payload, _find_image_bytes, _find_image_url


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


def test_nvidia_payload_without_reference_uses_text_endpoint():
    endpoint, payload = _build_payload("draw a warm scene")

    assert endpoint.endswith("/flux.1-dev")
    assert payload["prompt"] == "draw a warm scene"
    assert "image" not in payload


def test_nvidia_payload_with_reference_uses_kontext_image_endpoint():
    endpoint, payload = _build_payload("draw a warm scene", [(b"image-bytes", "image/jpeg")])

    assert endpoint.endswith("/flux.1-kontext-dev")
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert "Use the input image only as a visual reference" in payload["prompt"]
