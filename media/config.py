"""Environment-driven configuration for media compression.

Imported by both bot.py and the media package. Has no side effects beyond
reading environment variables, so it is safe to import in tests.
"""
import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Attachments larger than this (bytes) are considered oversized and are
# compressed or linked. Also the basis for the compression target. A fixed
# value is used instead of guild.filesize_limit because discord.py 2.3.2 can
# report a stale 25 MB limit for a non-boosted guild.
MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)

# Compression aims here, ~5% under the limit to absorb container overhead.
COMPRESS_TARGET = int(MAX_UPLOAD_BYTES * 0.95)

# Kill an ffmpeg encode that runs longer than this (seconds) -> CDN link.
FFMPEG_TIMEOUT_SECONDS = _int_env("FFMPEG_TIMEOUT_SECONDS", 300)

# Do not even attempt to download/compress inputs larger than this (bytes).
MAX_COMPRESS_INPUT_BYTES = _int_env("MAX_COMPRESS_INPUT_BYTES", 500 * 1024 * 1024)

# Bitrate constants (bits per second).
VIDEO_AUDIO_BITRATE = 128_000   # AAC track inside re-encoded video
VIDEO_MIN_BITRATE = 125_000     # below this, a video can't sanely fit -> link
AUDIO_MIN_BITRATE = 32_000      # standalone-audio floor -> link below this
AUDIO_MAX_BITRATE = 192_000     # standalone-audio ceiling
