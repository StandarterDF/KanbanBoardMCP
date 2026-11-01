"""Store images attached to comments / reviews.

The browser sends a base64 data URL (JSON), the server validates it as a real
image, writes it to ``static/uploads`` and returns a public URL that is kept in
the comment row. Using JSON avoids the extra ``python-multipart`` dependency.
"""

import base64
import binascii
import os
import re
import uuid
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")

MAX_BYTES = 12 * 1024 * 1024  # 12 MB decoded

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

_DATA_URL_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def _looks_like_image(mime: str, data: bytes) -> bool:
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime in ("image/jpeg", "image/jpg"):
        return data.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime == "image/bmp":
        return data.startswith(b"BM")
    return False


def save_data_url(data_url: str, filename: Optional[str] = None) -> dict:
    """Validate a base64 image data URL and save it under ``static/uploads``.

    Raises ``ValueError`` with a user-facing message on bad input.
    """
    match = _DATA_URL_RE.match((data_url or "").strip())
    if not match:
        raise ValueError("Ожидается изображение в формате data:image/...;base64,...")

    mime = match.group(1).lower()
    ext = _MIME_EXT.get(mime)
    if not ext:
        raise ValueError("Поддерживаются PNG, JPEG, GIF, WebP и BMP")

    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Не удалось декодировать изображение") from exc

    if not raw:
        raise ValueError("Пустой файл изображения")
    if len(raw) > MAX_BYTES:
        raise ValueError("Изображение слишком большое (максимум 12 МБ)")
    if not _looks_like_image(mime, raw):
        raise ValueError("Содержимое файла не является корректным изображением")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as fh:
        fh.write(raw)

    return {
        "url": f"/static/uploads/{name}",
        "filename": filename or name,
        "size": len(raw),
    }
