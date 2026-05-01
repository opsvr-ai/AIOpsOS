import asyncio
import logging
import os

logger = logging.getLogger(__name__)

PARSABLE_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-powerpoint",
    "application/vnd.ms-excel",
    "text/html",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "text/plain",
    "text/markdown",
}

SYNC_PARSE_SIZE_LIMIT = 100 * 1024  # 100KB


def parse_document_sync(file_path: str, mime_type: str | None = None) -> str | None:
    """Parse a document with markitdown. Returns markdown text or None on failure."""
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(file_path)
        if result and result.text_content:
            return result.text_content.strip()
    except ImportError:
        logger.warning("markitdown not available, skipping document parse")
    except Exception:
        logger.exception("markitdown parse failed: %s", file_path)
    return None


def is_parsable(mime_type: str | None) -> bool:
    return mime_type in PARSABLE_MIME_TYPES if mime_type else False


async def parse_document_async(file_path: str, mime_type: str | None = None) -> str | None:
    """Parse document in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(parse_document_sync, file_path, mime_type)
