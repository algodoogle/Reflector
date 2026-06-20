"""Media compression for oversized Discord attachments."""
from media.dispatch import (
    CompressedFile,
    prepare_message_files,
    route_attachment,
    shrink_attachment,
)

__all__ = [
    "CompressedFile",
    "prepare_message_files",
    "route_attachment",
    "shrink_attachment",
]
