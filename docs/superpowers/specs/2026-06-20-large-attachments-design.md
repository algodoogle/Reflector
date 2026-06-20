# Large Attachment Handling — Design

- **Date:** 2026-06-20
- **Branch:** `large_Attachments_Fix`
- **Status:** Approved for planning
- **Component:** `bot.py` message copier (`mirror_message`) + new `media/` package

## Problem

The bot mirrors Server A → Server B as a live backup. It re-uploads attachments
to Server B via webhook. Because uploads go through the bot (no Nitro) and are
bound by Server B's per-guild upload limit, files larger than that limit cannot
be re-uploaded — Discord rejects them with `413 Payload Too Large`.

The current behavior (`bot.py:397–433`) handles this by appending the original
attachment's Discord CDN URL (`attachment.url`) to the mirrored message and
skipping the upload. That link is a **signed CDN URL that expires ~24 hours
after it is issued**, so for a backup it is short-lived; once the Server A
message is deleted, the file is unrecoverable. We want oversized files preserved
as real, durable content in Server B wherever feasible.

## Goal

When the message copier encounters an attachment that exceeds the upload limit,
**compress it under the limit and re-upload the compressed copy** as a normal
Server B attachment. When compression is not feasible, fall back to the existing
CDN-link behavior.

## Non-goals

- Re-hosting files on external/self-hosted storage (considered and dropped).
- Compressing files that are already compressed or non-media: archives
  (zip/7z/rar), PDFs, office documents, plain data files. These take the
  CDN-link path unchanged.
- Preserving exact fidelity. Compression is lossy; for a backup, a smaller
  faithful-enough copy beats an expiring link.
- Changing behavior for attachments that already fit under the limit. They are
  re-uploaded exactly as today.

## Key decisions

1. **Upload limit is a fixed, configurable value** `MAX_UPLOAD_BYTES`, default
   `10 * 1024 * 1024` (10 MB), used both to detect "oversized" and as the
   compression target. This **replaces** `destination.guild.filesize_limit`,
   because discord.py 2.3.2 can report a stale 25 MB limit for a non-boosted
   guild while Discord actually rejects anything over 10 MB — trusting the
   reported value risks a 413 at upload time.
2. **Compression target** = `MAX_UPLOAD_BYTES * 0.95` (a ~5% safety margin so
   container/muxing overhead does not push the result back over the limit).
3. **ffmpeg is optional and graceful.** The bot runs without it; if `ffmpeg`/
   `ffprobe` are not on `PATH`, video and audio simply fall back to the CDN
   link. Presence is checked once at startup.
4. **Output formats:** images and GIF → WebP; video → mp4 (H.264 video + AAC
   audio); audio → mp3.
5. **Universal fallback:** any failure, timeout, unsupported type, or
   "cannot fit under target" results in the current CDN-link behavior. The bot
   never drops a message because of attachment handling.

## Behavior & routing

In `mirror_message`, the per-attachment loop becomes:

```
for attachment in message.attachments:
    if attachment.size <= MAX_UPLOAD_BYTES:
        files.append(await attachment.to_file())   # unchanged
        continue

    compressed = await shrink_attachment(attachment, target=COMPRESS_TARGET)
    if compressed is not None:
        files.append(discord.File(compressed.fp, filename=compressed.filename))
        continue

    oversized_links.append(attachment.url)          # unchanged fallback
```

`shrink_attachment` dispatches by MIME type (`attachment.content_type`, with the
file extension as a fallback when content_type is missing):

| Detected type           | Tool   | Output         |
|-------------------------|--------|----------------|
| `image/*` (not gif)     | Pillow | WebP           |
| `image/gif`, APNG       | Pillow | animated WebP  |
| `video/*`               | ffmpeg | mp4 (H.264/AAC)|
| `audio/*`               | ffmpeg | mp3            |
| anything else           | —      | `None` → CDN link |

Returns a `CompressedFile(fp: io.BytesIO, filename: str)` on success, or `None`
to signal the caller to use the CDN link.

## Per-format compression

### Images and GIF (Pillow, in memory)

1. Open the downloaded bytes with Pillow. If it cannot be opened, return `None`.
2. Walk a grid of `(scale, quality)` steps — e.g. scale ∈ {1.0, 0.75, 0.5,
   0.35, 0.25}, quality ∈ {85, 70, 55, 40}. At each step resize and re-encode to
   WebP; return the first encoding whose size ≤ target.
3. Animated images (`getattr(img, "is_animated", False)`) are saved as animated
   WebP (`save_all=True`), same scale/quality search.
4. If no step fits, return `None`.
5. On success the filename keeps its stem and gets a `.webp` extension
   (`screenshot.png` → `screenshot.webp`).
6. Guard against decode bombs: catch `Image.DecompressionBombError` and any
   `OSError`/`ValueError` from Pillow → `None`.

### Video (ffmpeg, temp files)

1. `ffprobe` the input for duration (seconds). If duration is unavailable,
   return `None`.
2. Compute `target_total_bitrate = (target_bytes * 8) / duration`.
   `video_bitrate = target_total_bitrate - VIDEO_AUDIO_BITRATE` (the embedded
   AAC track, 128 kbps).
3. If `video_bitrate < VIDEO_MIN_BITRATE` (≈125 kbps — e.g. a 2-hour video
   cannot sanely fit in 10 MB), return `None` → CDN link.
4. **Two-pass** libx264 encode at the computed `-b:v`, with `-maxrate` and
   `-bufsize` set, audio re-encoded to AAC at `VIDEO_AUDIO_BITRATE`, output mp4.
5. Verify the output size ≤ target. If it overshoots (rare with two-pass),
   retry once at 90% of the bitrate. If it still overshoots, return `None`.
6. Filename keeps its stem with a `.mp4` extension.

### Audio (ffmpeg, temp files)

1. `ffprobe` for duration. Compute `target_bitrate = (target_bytes * 8) /
   duration`, clamped to `[AUDIO_MIN_BITRATE (32 kbps), AUDIO_MAX_BITRATE
   (192 kbps)]`.
2. If the required bitrate is below `AUDIO_MIN_BITRATE`, return `None` → CDN link.
3. Re-encode to mp3 (libmp3lame) at the chosen bitrate. Filename → `.mp3`.

## Async, performance, and resource handling

- ffmpeg runs via `asyncio.create_subprocess_exec` so it never blocks the
  discord.py event loop or heartbeat. Pillow work runs in `asyncio.to_thread`
  (CPU-bound). A long encode delays only the message that owns the attachment;
  history sync is already sequential.
- The input is downloaded to a temp file (ffmpeg path) or read into memory
  (Pillow path). All temp files — input, two-pass logs, output — are removed in
  a `finally` block, including on timeout or error.
- **Guards:**
  - Per-encode **timeout** `FFMPEG_TIMEOUT_SECONDS` (default 300). On timeout the
    subprocess is killed and the attachment falls back to the CDN link.
  - **Max input size** `MAX_COMPRESS_INPUT_BYTES` (default 500 MB). Larger inputs
    are not downloaded/attempted; they go straight to the CDN link.
  - **ffmpeg availability** is resolved once at startup (`shutil.which("ffmpeg")`
    and `"ffprobe"`) and cached; when absent, video/audio routing returns `None`.

## Module structure

A new `media/` package keeps the testable core out of the 1300-line `bot.py`:

- `media/dispatch.py` — `async def shrink_attachment(attachment, target) ->
  CompressedFile | None`; MIME/extension routing; the `CompressedFile`
  dataclass; ffmpeg availability check.
- `media/images.py` — pure Pillow compression (`compress_image_under`,
  `is_compressible_image`). No Discord, no I/O beyond bytes in/out.
- `media/ffmpeg.py` — video and audio encoding via async subprocess, temp-file
  lifecycle, bitrate math (`target_video_bitrate`, `target_audio_bitrate` are
  pure functions).

`bot.py` imports `shrink_attachment` and calls it from `mirror_message`. The
`oversized_links` content-append logic stays as is.

## Dependencies

- **Pillow** — pip dependency (image/GIF compression).
- **ffmpeg / ffprobe** — system binaries, documented as an *optional*
  prerequisite for video/audio compression. Without them the bot still runs and
  video/audio fall back to CDN links.
- Add a `requirements.txt` (`discord.py`, `python-dotenv`, `Pillow`) and update
  the README install instructions and the prerequisites section to mention
  ffmpeg.

## Configuration (environment variables)

| Variable                  | Default            | Purpose                                              |
|---------------------------|--------------------|------------------------------------------------------|
| `MAX_UPLOAD_BYTES`        | `10485760` (10 MB) | Oversized threshold and compression target basis     |
| `FFMPEG_TIMEOUT_SECONDS`  | `300`              | Kill an encode that runs longer; fall back to link   |
| `MAX_COMPRESS_INPUT_BYTES`| `524288000` (500 MB)| Don't attempt compression above this; use link       |

Internal constants (not env, but named in code): `COMPRESS_TARGET =
MAX_UPLOAD_BYTES * 0.95`, `VIDEO_AUDIO_BITRATE = 128k` (AAC track inside
re-encoded video), `VIDEO_MIN_BITRATE ≈ 125k`, `AUDIO_MIN_BITRATE = 32k`,
`AUDIO_MAX_BITRATE = 192k` (the last two bound the standalone-audio path).
Documented in `.env.example` and README.

## Testing strategy

The repo currently has no tests; this introduces a pytest suite covering the
pure, dependency-light core. ffmpeg-dependent tests are gated.

- **Type routing** — given fake attachments with various `content_type`/
  extensions, assert the correct branch (image / video / audio / CDN-link).
- **Image compression** — generate a synthetic large image (e.g. 4000×4000
  noise), assert result is non-`None`, valid, ≤ target, filename ends `.webp`.
- **GIF** — synthetic animated GIF → non-`None`, valid animated WebP.
- **Image that cannot fit** — assert returns `None` (CDN-link path).
- **Bitrate math** — `target_video_bitrate` / `target_audio_bitrate` for known
  duration/target; assert below-floor inputs yield `None`.
- **Filename rewriting** — extension swaps for each output format.
- **ffmpeg integration** — encode a tiny generated clip; `pytest.mark.skipif`
  when ffmpeg is not on `PATH`.

## Edge cases

- **Multiple attachments**, mixed sizes/types: each handled independently; some
  uploaded, some compressed, some linked, in one message.
- **content_type missing**: fall back to extension; if still unknown, treat as
  non-media → CDN link.
- **Compression makes the file larger** (already-optimized small-but-oversized
  media): the size check at each step prevents uploading a larger file; if
  nothing beats the target, CDN link.
- **ffmpeg present but encode fails** (corrupt input, unsupported codec): caught,
  logged, CDN link.
- **Re-sync idempotency**: `mirror_state.json` prevents reprocessing already
  mirrored messages; a fresh re-sync simply recompresses — no special handling
  needed.

## Backward compatibility

Fully backward compatible. With no new env vars set, the threshold defaults to
10 MB and behavior for non-compressible / failed cases is identical to today
(CDN link appended). The only observable change is that oversized **media** now
arrives in Server B as a real, smaller attachment instead of a link.
