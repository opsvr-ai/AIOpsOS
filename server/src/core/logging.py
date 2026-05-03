import json
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from src.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
            "message": record.getMessage(),
        }, ensure_ascii=False) + "\n"


def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    level = getattr(logging, settings.log_level.upper(), logging.DEBUG)

    if settings.log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    root.setLevel(level)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)
    root.addHandler(stream_handler)

    log_dir = settings.log_dir
    os.makedirs(log_dir, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "server.log"),
        when="midnight",
        interval=1,
        backupCount=settings.log_retention_days,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # Capture uvicorn's internal loggers to the same file + stdout
    for uvicorn_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(uvicorn_name)
        uv_logger.handlers.clear()
        uv_logger.addHandler(stream_handler)
        uv_logger.addHandler(file_handler)
        uv_logger.setLevel(level)
        uv_logger.propagate = False

    for lib in (
        "httpx", "httpcore", "urllib3", "asyncio",
        "langchain", "langgraph", "sqlalchemy.engine",
    ):
        logging.getLogger(lib).setLevel(logging.WARNING)
