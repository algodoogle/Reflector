import asyncio
import io

from PIL import Image

from media.dispatch import (
    route_attachment,
    shrink_attachment,
    prepare_message_files,
    CompressedFile,
)
from media import config
from tests.conftest import FakeAttachment, make_noise_png, make_solid_png


def test_route_by_content_type():
    assert route_attachment("image/png", "a.png") == "image"
    assert route_attachment("image/gif", "a.gif") == "image"
    assert route_attachment("video/mp4", "a.mp4") == "video"
    assert route_attachment("audio/mpeg", "a.mp3") == "audio"
    assert route_attachment("application/zip", "a.zip") is None


def test_route_falls_back_to_extension():
    assert route_attachment(None, "a.PNG") == "image"
    assert route_attachment(None, "a.mov") == "video"
    assert route_attachment(None, "a.flac") == "audio"
    assert route_attachment(None, "a.zip") is None
    assert route_attachment(None, "noext") is None


def test_shrink_image_returns_compressed_file():
    data = make_noise_png((800, 800))
    att = FakeAttachment(data, "photo.png", "image/png")
    result = asyncio.run(shrink_attachment(att, target=50_000))
    assert isinstance(result, CompressedFile)
    assert result.filename == "photo.webp"
    out = result.fp.read()
    assert len(out) <= 50_000
    assert Image.open(io.BytesIO(out)).format == "WEBP"


def test_shrink_returns_none_for_unsupported_type():
    att = FakeAttachment(b"PK\x03\x04zipdata", "a.zip", "application/zip")
    assert asyncio.run(shrink_attachment(att, target=50_000)) is None


def test_shrink_returns_none_when_input_too_large():
    data = make_noise_png((64, 64))
    att = FakeAttachment(
        data, "photo.png", "image/png", size=config.MAX_COMPRESS_INPUT_BYTES + 1
    )
    assert asyncio.run(shrink_attachment(att, target=50_000)) is None


def test_prepare_uploads_small_attachment_unchanged():
    att = FakeAttachment(b"small", "note.txt", "text/plain", size=10)
    files, links = asyncio.run(
        prepare_message_files([att], max_upload=1000, target=900)
    )
    assert len(files) == 1
    assert links == []


def test_prepare_compresses_oversized_image():
    data = make_solid_png((128, 128))
    att = FakeAttachment(data, "pic.png", "image/png", size=20_000_000)
    files, links = asyncio.run(
        prepare_message_files([att], max_upload=100, target=5_000_000)
    )
    assert len(files) == 1
    assert files[0].filename == "pic.webp"
    assert links == []


def test_prepare_links_oversized_non_media():
    att = FakeAttachment(
        b"PK\x03\x04", "archive.zip", "application/zip",
        size=20_000_000, url="http://cdn/archive.zip",
    )
    files, links = asyncio.run(
        prepare_message_files([att], max_upload=100, target=5_000_000)
    )
    assert files == []
    assert links == ["http://cdn/archive.zip"]
