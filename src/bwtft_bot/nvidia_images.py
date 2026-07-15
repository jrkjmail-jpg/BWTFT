import base64
from typing import Any

import httpx

from bwtft_bot.config import settings


class NvidiaImageError(RuntimeError):
    pass


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


async def generate_image(prompt: str) -> bytes:
    if not settings.nvidia_api_key:
        raise NvidiaImageError("NVIDIA_API_KEY is not configured")

    payload: dict[str, Any] = {
        "prompt": prompt,
        "mode": "base",
        "width": settings.nvidia_image_width,
        "height": settings.nvidia_image_height,
    }

    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            settings.nvidia_image_endpoint,
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
