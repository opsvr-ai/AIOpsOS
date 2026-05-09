"""SQLAlchemy model for the ``wiki_compile_log`` table.

Matches migration ``202605041820_add_wiki_compile_log.py``. Backs the
idempotency check in :func:`src.services.kb.compile_logic.compile_wiki_async`.

``raw_path`` is the primary key (not a surrogate UUID) because each
raw file has exactly one log row — lookups are by path, and upserts use
``ON CONFLICT (raw_path) DO UPDATE``.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class WikiCompileLog(Base):
    __tablename__ = "wiki_compile_log"

    raw_path: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    last_compiled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    wiki_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
