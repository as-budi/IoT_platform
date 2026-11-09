"""
Thumbnail snapshot (lebar THUMB_WIDTH piksel), dibuat dengan Pillow.

Pillow bersifat opsional: tanpa Pillow (atau bila gambar rusak) make_thumbnail()
mengembalikan None dan tampilan memakai gambar aslinya, diperkecil oleh CSS.
"""
import io
import logging

logger = logging.getLogger(__name__)

THUMB_WIDTH = 320
THUMB_QUALITY = 70


def available():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def make_thumbnail(data, width=None):
    """JPEG `data` -> JPEG kecil (bytes), atau None bila tidak perlu/tidak bisa dibuat."""
    width = width or THUMB_WIDTH
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width <= width:
                return None          # gambar asli sudah kecil
            height = max(1, round(image.height * width / image.width))
            small = image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
            out = io.BytesIO()
            small.save(out, "JPEG", quality=THUMB_QUALITY, optimize=True)
            return out.getvalue()
    except Exception as exc:     # gambar rusak/terpotong: cukup tanpa thumbnail
        logger.info("No thumbnail: %s", exc)
        return None
