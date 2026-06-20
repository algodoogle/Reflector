import asyncio
import os
import subprocess
import tempfile

import pytest

from media.ffmpeg import (
    target_video_bitrate,
    target_audio_bitrate,
    ffmpeg_available,
    compress_video,
    compress_audio,
)

requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH"
)


def test_video_bitrate_subtracts_audio_track():
    # 10 MB over 60 s: total = 10*1024*1024*8/60 ≈ 1_398_101 bps;
    # minus 128_000 audio ≈ 1_270_101.
    vb = target_video_bitrate(10 * 1024 * 1024, 60)
    assert vb == int((10 * 1024 * 1024 * 8) / 60) - 128_000


def test_video_bitrate_none_below_floor():
    # A 2-hour video into 10 MB is far below the 125 kbps video floor.
    assert target_video_bitrate(10 * 1024 * 1024, 7200) is None


def test_video_bitrate_none_for_zero_duration():
    assert target_video_bitrate(10 * 1024 * 1024, 0) is None


def test_audio_bitrate_clamped_to_ceiling():
    # Short clip wants a very high bitrate; clamp to 192 kbps.
    assert target_audio_bitrate(10 * 1024 * 1024, 5) == 192_000


def test_audio_bitrate_in_range():
    # 10 MB over 600 s ≈ 139_810 bps, between 32k and 192k → unclamped.
    ab = target_audio_bitrate(10 * 1024 * 1024, 600)
    assert ab == int((10 * 1024 * 1024 * 8) / 600)


def test_audio_bitrate_none_below_floor():
    # 10 MB over 8 hours is below the 32 kbps audio floor.
    assert target_audio_bitrate(10 * 1024 * 1024, 28800) is None


def test_audio_bitrate_none_for_zero_duration():
    assert target_audio_bitrate(10 * 1024 * 1024, 0) is None


def test_ffmpeg_available_returns_bool():
    assert isinstance(ffmpeg_available(), bool)


def _make_test_video(path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x480:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
            "-shortest", "-c:v", "libx264", "-c:a", "aac", path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _make_test_audio(path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@requires_ffmpeg
def test_compress_video_returns_bytes_under_target():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.mp4")
        _make_test_video(src)
        out = asyncio.run(compress_video(src, target=200_000, timeout=120))
        assert out is not None
        assert 0 < len(out) <= 200_000


@requires_ffmpeg
def test_compress_audio_returns_bytes_under_target():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.wav")
        _make_test_audio(src)
        out = asyncio.run(compress_audio(src, target=100_000, timeout=120))
        assert out is not None
        assert 0 < len(out) <= 100_000


@requires_ffmpeg
def test_compress_video_none_when_target_impossible():
    # Target so small the required video bitrate is below the floor.
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.mp4")
        _make_test_video(src)
        out = asyncio.run(compress_video(src, target=100, timeout=120))
        assert out is None
