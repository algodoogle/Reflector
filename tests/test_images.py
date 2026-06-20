import io

from PIL import Image

from media import images
from media.images import compress_image_under
from tests.conftest import make_noise_png, make_solid_png, make_animated_gif


def test_huge_image_is_downscaled_not_dropped():
    # Wider than WebP's 16383px hard limit. Must be downscaled and still
    # produced (regression: previously the encode error aborted the whole
    # grid and returned None, and full-res encodes were pathologically slow).
    data = make_solid_png((18000, 4000))
    result = compress_image_under(data, target=5_000_000, filename="huge.png")
    assert result is not None
    out_bytes, name = result
    out_img = Image.open(io.BytesIO(out_bytes))
    assert max(out_img.size) <= images.MAX_DIMENSION
    assert name == "huge.webp"


def test_compresses_static_image_under_target():
    data = make_noise_png((800, 800))
    result = compress_image_under(data, target=50_000, filename="photo.png")
    assert result is not None
    out_bytes, name = result
    assert len(out_bytes) <= 50_000
    assert name == "photo.webp"
    # output is a valid WebP image
    img = Image.open(io.BytesIO(out_bytes))
    assert img.format == "WEBP"


def test_returns_none_when_cannot_fit():
    data = make_noise_png((800, 800))
    result = compress_image_under(data, target=1, filename="photo.png")
    assert result is None


def test_compresses_animated_gif_to_animated_webp():
    data = make_animated_gif(frames=5)
    result = compress_image_under(data, target=5_000_000, filename="clip.gif")
    assert result is not None
    out_bytes, name = result
    assert name == "clip.webp"
    img = Image.open(io.BytesIO(out_bytes))
    assert img.format == "WEBP"
    assert getattr(img, "is_animated", False)


def test_returns_none_for_non_image_bytes():
    assert compress_image_under(b"this is not an image", 1_000_000, "x.png") is None


def test_filename_without_extension_gets_webp():
    data = make_solid_png()
    result = compress_image_under(data, target=1_000_000, filename="noext")
    assert result is not None
    _, name = result
    assert name == "noext.webp"


def test_uppercase_extension_is_replaced():
    data = make_solid_png()
    result = compress_image_under(data, target=1_000_000, filename="IMG.PNG")
    assert result is not None
    _, name = result
    assert name == "IMG.webp"
