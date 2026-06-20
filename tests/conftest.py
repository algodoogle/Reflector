"""Shared pytest fixtures and helpers for the media test suite."""
import io
import os

import discord
from PIL import Image


class FakeAttachment:
    """Duck-typed stand-in for discord.Attachment (no network)."""

    def __init__(self, data: bytes, filename: str, content_type, size=None, url="http://cdn/x"):
        self._data = data
        self.filename = filename
        self.content_type = content_type
        self.size = len(data) if size is None else size
        self.url = url

    async def read(self) -> bytes:
        return self._data

    async def to_file(self) -> "discord.File":
        return discord.File(io.BytesIO(self._data), filename=self.filename)

    async def save(self, path) -> None:
        with open(path, "wb") as fh:
            fh.write(self._data)


def make_noise_png(size=(800, 800)) -> bytes:
    """A poorly-compressible RGB noise image, encoded as PNG."""
    w, h = size
    img = Image.frombytes("RGB", size, os.urandom(w * h * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_solid_png(size=(64, 64), color=(10, 20, 30)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_animated_gif(frames=5, size=(64, 64)) -> bytes:
    imgs = [
        Image.new("RGB", size, (i * 40 % 256, 0, 0)) for i in range(frames)
    ]
    buf = io.BytesIO()
    imgs[0].save(
        buf, format="GIF", save_all=True, append_images=imgs[1:], duration=100, loop=0
    )
    return buf.getvalue()
