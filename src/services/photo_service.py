from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 16_000_000


def accepted_photo(attachment) -> bool:
    return (
        (attachment.content_type or "").lower() in {"image/jpeg", "image/png", "image/webp"}
        and 0 < attachment.size <= MAX_PHOTO_BYTES
    )


def sanitize_photo(data: bytes) -> bytes:
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise ValueError("The photo must be no larger than 10 MB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Upload a static JPEG, PNG, or WebP photo.")
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise ValueError("The photo is too large. Use an image under 16 megapixels.")
            image.load()
            output = BytesIO()
            normalized = image.convert("RGB")
            normalized.info.clear()
            normalized.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The attachment is not a readable photo.") from exc
    result = output.getvalue()
    if len(result) > MAX_PHOTO_BYTES:
        raise ValueError("The photo is too large after processing. Upload a smaller screenshot.")
    return result
