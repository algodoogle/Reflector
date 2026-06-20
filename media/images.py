"""Pure, in-memory image and animated-GIF compression to WebP."""
import io

from PIL import Image

# Static images: walk scales (largest first) then qualities, returning the
# first encoding that fits. Animated images vary quality only - resizing
# animated frames is complex for marginal benefit; they fall back to the CDN
# link if quality reduction alone is insufficient.
_SCALES = (1.0, 0.75, 0.5, 0.35, 0.25)
_QUALITIES = (85, 70, 55, 40)

# Cap the working resolution before encoding. This bounds encode time (full
# resolution WebP encodes can take many seconds each) and keeps every encode
# under WebP's 16383px hard per-side limit. 2560px is ample for a backup copy
# of an image that is already too large to store at full size.
MAX_DIMENSION = 2560

# WebP effort level. method=6 (max) is ~2x slower than method=4 for a <2%
# size gain, which matters when compression runs inline during history sync.
_WEBP_METHOD = 4


def _webp_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem}.webp"


def _encode(image: Image.Image, quality: int, save_all: bool) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="WEBP", quality=quality, method=_WEBP_METHOD, save_all=save_all)
    return buf.getvalue()


def _compress_static(img: Image.Image, target: int) -> bytes | None:
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    width, height = img.size
    for scale in _SCALES:
        frame = (
            img
            if scale == 1.0
            else img.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        )
        for quality in _QUALITIES:
            out = _encode(frame, quality, save_all=False)
            if len(out) <= target:
                return out
    return None


def _compress_animated(img: Image.Image, target: int) -> bytes | None:
    for quality in _QUALITIES:
        out = _encode(img, quality, save_all=True)
        if len(out) <= target:
            return out
        img.seek(0)  # reset frame pointer for the next attempt
    return None


def compress_image_under(
    data: bytes, target: int, filename: str
) -> tuple[bytes, str] | None:
    """Compress image bytes to a WebP no larger than ``target`` bytes.

    Returns ``(webp_bytes, new_filename)`` or ``None`` if the data is not a
    decodable image or cannot be made to fit.
    """
    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        return None

    # Arbitrary user uploads can fail to decode or encode in many ways (RAW
    # formats, truncated data, decompression bombs, unsupported modes). Any
    # failure means "can't compress" -> caller links the original.
    try:
        if getattr(img, "is_animated", False):
            out = _compress_animated(img, target)
        else:
            out = _compress_static(img, target)
    except Exception:
        return None

    if out is None:
        return None
    return out, _webp_filename(filename)
