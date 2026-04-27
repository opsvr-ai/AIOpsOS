import logging
import sys


def setup_logging(level: str = "DEBUG") -> None:
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    # quiet noisy libs
    for lib in ("httpx", "httpcore", "urllib3", "asyncio",
                "langchain", "langgraph", "sqlalchemy.engine"):
        logging.getLogger(lib).setLevel(logging.WARNING)
