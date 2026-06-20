"""Video/audio compression via ffmpeg, plus the pure bitrate arithmetic."""
import asyncio
import functools
import os
import shutil
import tempfile

from media.config import (
    AUDIO_MAX_BITRATE,
    AUDIO_MIN_BITRATE,
    VIDEO_AUDIO_BITRATE,
    VIDEO_MIN_BITRATE,
)


def target_video_bitrate(target_bytes: int, duration: float) -> int | None:
    """Video bitrate (bps) to land ``target_bytes`` over ``duration`` seconds,
    reserving room for the audio track. ``None`` if below the usable floor."""
    if duration <= 0:
        return None
    total = (target_bytes * 8) / duration
    video = total - VIDEO_AUDIO_BITRATE
    if video < VIDEO_MIN_BITRATE:
        return None
    return int(video)


def target_audio_bitrate(target_bytes: int, duration: float) -> int | None:
    """Audio bitrate (bps) to land ``target_bytes`` over ``duration`` seconds,
    clamped to [AUDIO_MIN_BITRATE, AUDIO_MAX_BITRATE]. ``None`` if below floor."""
    if duration <= 0:
        return None
    rate = (target_bytes * 8) / duration
    if rate < AUDIO_MIN_BITRATE:
        return None
    return int(min(rate, AUDIO_MAX_BITRATE))


@functools.lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    """True only if both ffmpeg and ffprobe are on PATH. Cached for the process."""
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def _probe_duration(input_path: str) -> float | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
        return None


async def _run(cmd: list[str], timeout: int) -> bool:
    """Run an ffmpeg command non-blocking. Returns True on exit code 0.
    On timeout, kills the process and returns False."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return proc.returncode == 0


async def compress_video(input_path: str, target: int, timeout: int) -> bytes | None:
    """Two-pass H.264/AAC encode targeting ``target`` bytes. ``None`` on
    failure, timeout, or if the file can't fit at an acceptable bitrate."""
    duration = await _probe_duration(input_path)
    if not duration:
        return None
    base_vb = target_video_bitrate(target, duration)
    if base_vb is None:
        return None

    with tempfile.TemporaryDirectory() as work:
        log_prefix = os.path.join(work, "ff2pass")
        out_path = os.path.join(work, "out.mp4")
        for vb in (base_vb, int(base_vb * 0.9)):
            pass1 = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:v", "libx264", "-b:v", str(vb),
                "-pass", "1", "-passlogfile", log_prefix,
                "-an", "-f", "null", os.devnull,
            ]
            pass2 = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:v", "libx264", "-b:v", str(vb),
                "-maxrate", str(int(vb * 1.2)), "-bufsize", str(vb * 2),
                "-pass", "2", "-passlogfile", log_prefix,
                "-c:a", "aac", "-b:a", str(VIDEO_AUDIO_BITRATE),
                "-movflags", "+faststart", out_path,
            ]
            if not await _run(pass1, timeout):
                return None
            if not await _run(pass2, timeout):
                return None
            if os.path.exists(out_path) and os.path.getsize(out_path) <= target:
                with open(out_path, "rb") as fh:
                    return fh.read()
        return None


async def compress_audio(input_path: str, target: int, timeout: int) -> bytes | None:
    """Re-encode to mp3 targeting ``target`` bytes. ``None`` on failure or if
    the required bitrate is below the audio floor."""
    duration = await _probe_duration(input_path)
    if not duration:
        return None
    base_ab = target_audio_bitrate(target, duration)
    if base_ab is None:
        return None

    # MP3 CBR snaps the requested bitrate up to the nearest standard rate
    # (128/160/192...), which can overshoot the byte target. Verify the output
    # and retry at a lower bitrate until it fits or drops below the floor.
    with tempfile.TemporaryDirectory() as work:
        out_path = os.path.join(work, "out.mp3")
        for factor in (1.0, 0.85, 0.7):
            ab = int(base_ab * factor)
            if ab < AUDIO_MIN_BITRATE:
                break
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-vn", "-c:a", "libmp3lame", "-b:a", str(ab),
                out_path,
            ]
            if not await _run(cmd, timeout):
                return None
            if os.path.exists(out_path) and os.path.getsize(out_path) <= target:
                with open(out_path, "rb") as fh:
                    return fh.read()
        return None
