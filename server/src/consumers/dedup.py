"""Alert deduplication — prevents duplicate alerts from flooding the system."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.alert import Alert

logger = logging.getLogger(__name__)


async def find_existing(
    db: AsyncSession,
    title: str,
    source: str,
    window_minutes: int = 5,
) -> Alert | None:
    """Find an existing alert with same title+source within the time window."""
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
    result = await db.execute(
        select(Alert)
        .where(
            Alert.title == title,
            Alert.source == source,
            Alert.created_at >= cutoff,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()
