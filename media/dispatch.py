"""Attachment routing and the bot-facing compression entry points."""
import asyncio
import io
import logging
import os
import tempfile
from dataclasses import dataclass

import discord

from media import config
from media.ffmpeg import compress_audio, compress_video, ffmpeg_available
from media.images import compress_image_under

log = logging.getLogger("reflector.media")

_IMAGE, _VIDEO, _AUDIO = "image", "video", "audio"

_EXT_MAP = {
    "png": _IMAGE, "jpg": _IMAGE, "jpeg": _IMAGE, "webp": _IMAGE,
    "bmp": _IMAGE, "tiff": _IMAGE, "tif": _IMAGE, "gif": _IMAGE,
    "mp4": _VIDEO, "mov": _VIDEO, "mkv": _VIDEO, "webm": _VIDEO, "avi": _VIDEO,
    "mp3": _AUDIO, "wav": _AUDIO, "flac": _AUDIO, "ogg": _AUDIO, "m4a": _AUDIO,
}


@dataclass
class CompressedFile:
    fp: io.BytesIO
    filename: str


def route_attachment(content_type: str | None, filename: str) -> str | None:
    """Classify an attachment as image/video/audio, or None (-> CDN link)."""
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return _IMAGE
    if ct.startswith("video/"):
        return _VIDEO
    if ct.startswith("audio/"):
        return _AUDIO
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _EXT_MAP.get(ext)


def _swap_ext(filename: str, new_ext: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem}{new_ext}"


async def _shrink_media(attachment, kind: str, target: int) -> tuple[bytes, str] | None:
    """Download a video/audio attachment to a temp file and encode it."""
    suffix = os.path.splitext(attachment.filename)[1]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        await attachment.save(tmp)
        if kind == _VIDEO:
            out = await compress_video(tmp, target, config.FFMPEG_TIMEOUT_SECONDS)
            return (out, _swap_ext(attachment.filename, ".mp4")) if out else None
        out = await compress_audio(tmp, target, config.FFMPEG_TIMEOUT_SECONDS)
        return (out, _swap_ext(attachment.filename, ".mp3")) if out else None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def shrink_attachment(attachment, target: int) -> CompressedFile | None:
    """Compress one oversized attachment under ``target`` bytes, or return
    None to signal the caller to use the Discord CDN link."""
    kind = route_attachment(getattr(attachment, "content_type", None), attachment.filename)
    if kind is None:
        return None
    if attachment.size > config.MAX_COMPRESS_INPUT_BYTES:
        return None
    try:
        if kind == _IMAGE:
            data = await attachment.read()
            result = await asyncio.to_thread(
                compress_image_under, data, target, attachment.filename
            )
        else:
            if not ffmpeg_available():
                return None
            result = await _shrink_media(attachment, kind, target)
        if result is None:
            return None
        out_bytes, name = result
        return CompressedFile(io.BytesIO(out_bytes), name)
    except Exception as exc:  # never let attachment handling drop a message
        log.warning("Compression failed for '%s': %s", attachment.filename, exc)
        return None


async def prepare_message_files(
    attachments,
    *,
    max_upload: int | None = None,
    target: int | None = None,
) -> tuple[list[discord.File], list[str]]:
    """Turn a message's attachments into (files_to_upload, cdn_link_urls).

    Attachments at or under ``max_upload`` are re-uploaded unchanged. Oversized
    ones are compressed when possible; otherwise their CDN url is returned to be
    appended to the message body.
    """
    max_upload = config.MAX_UPLOAD_BYTES if max_upload is None else max_upload
    target = config.COMPRESS_TARGET if target is None else target

    files: list[discord.File] = []
    links: list[str] = []
    for attachment in attachments:
        if attachment.size <= max_upload:
            files.append(await attachment.to_file())
            continue
        compressed = await shrink_attachment(attachment, target)
        if compressed is not None:
            files.append(discord.File(compressed.fp, filename=compressed.filename))
        else:
            log.warning(
                "Attachment '%s' (%d B) exceeds %d B and could not be "
                "compressed - linking instead",
                attachment.filename, attachment.size, max_upload,
            )
            links.append(attachment.url)
    return files, links
