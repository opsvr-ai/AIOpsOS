"""Schemas for KB monitoring endpoints."""

from pydantic import BaseModel


class WatchedFile(BaseModel):
    path: str
    status: str  # "unchanged" | "changed" | "new" | "deleted"
    last_modified: str | None = None
    size: int = 0


class ProcessResult(BaseModel):
    file: str
    status: str  # "processed" | "skipped" | "error"
    wiki_pages_updated: list[str] = []
    message: str = ""


class ProcessAllResult(BaseModel):
    total: int
    processed: int
    skipped: int
    errors: int
    results: list[ProcessResult] = []


class MonitorStatus(BaseModel):
    enabled: bool
    running: bool
    watched_files: int
    last_check: str | None = None
    poll_interval_seconds: int = 30
