import base64
from collections.abc import Sequence
from typing import Any

import httpx

from bwtft_bot.config import settings


class NvidiaImageError(RuntimeError):
    pass


PhotoInput = tuple[bytes, str]


def _image_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _reference_prompt(prompt: str) -> str:
    return (
        f"{prompt}\n\n"
        "Use the input image only as a visual reference for the characters and important objects. "
        "Preserve recognizable appearance, colors, proportions, and characteristic details from the reference, "
        "but create the new illustrated scene described above."
    )


def _build_payload(prompt: str, reference_images: Sequence[PhotoInput] = ()) -> tuple[str, dict[str, Any]]:
    if reference_images:
        image_bytes, mime_type = reference_images[0]
        return settings.nvidia_reference_image_endpoint, {
            "prompt": _reference_prompt(prompt),
            "image": _image_data_url(image_bytes, mime_type),
            "aspect_ratio": "1:1",
            "samples": 1,
        }

    return settings.nvidia_image_endpoint, {
        "prompt": prompt,
        "mode": "base",
        "width": settings.nvidia_image_width,
        "height": settings.nvidia_image_height,
        "samples": 1,
    }


def _decode_data_url(value: str) -> bytes | None:
    if not value.startswith("data:image/") or "," not in value:
        return None
    _header, encoded = value.split(",", 1)
    return base64.b64decode(encoded)


def _decode_base64_image(value: str) -> bytes | None:
    data_url = _decode_data_url(value)
    if data_url is not None:
        return data_url
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        return None


def _find_image_bytes(value: Any) -> bytes | None:
    if isinstance(value, str):
        return _decode_base64_image(value)
    if isinstance(value, list):
        for item in value:
            found = _find_image_bytes(item)
            if found is not None:
                return found
    if isinstance(value, dict):
        for key in ("base64", "b64_json", "image", "image_base64", "data", "url"):
            if key in value:
                found = _find_image_bytes(value[key])
                if found is not None:
                    return found
        for nested in value.values():
            found = _find_image_bytes(nested)
            if found is not None:
                return found
    return None


def _find_image_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for item in value:
            found = _find_image_url(item)
            if found is not None:
                return found
    if isinstance(value, dict):
        for key in ("url", "image_url", "asset_url"):
            if key in value:
                found = _find_image_url(value[key])
                if found is not None:
                    return found
        for nested in value.values():
            found = _find_image_url(nested)
            if found is not None:
                return found
    return None


async def generate_image(prompt: str, reference_images: Sequence[PhotoInput] = ()) -> bytes:
    if not settings.nvidia_api_key:
        raise NvidiaImageError("NVIDIA_API_KEY is not configured")

    endpoint, payload = _build_payload(
        prompt,
        reference_images[: settings.nvidia_reference_images_max],
    )

    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            endpoint,
            headers=headers,
            json=payload,
        )

    if response.status_code >= 400:
        raise NvidiaImageError(f"NVIDIA API returned {response.status_code}: {response.text[:500]}")

    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return response.content

    try:
        data = response.json()
    except ValueError as exc:
        raise NvidiaImageError("NVIDIA API did not return JSON or image bytes") from exc

    image_bytes = _find_image_bytes(data)
    if image_bytes is not None:
        return image_bytes

    image_url = _find_image_url(data)
    if image_url is not None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            image_response = await client.get(image_url)
        if image_response.status_code >= 400:
            raise NvidiaImageError(
                f"NVIDIA image URL returned {image_response.status_code}: {image_response.text[:500]}"
            )
        return image_response.content

    raise NvidiaImageError("NVIDIA API response does not contain image bytes")
