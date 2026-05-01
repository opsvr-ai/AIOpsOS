"""Entry point for the Alert Consumer process.

Usage:
    python main_consumer.py              # Kafka mode (needs broker)
    python main_consumer.py --mock       # DB polling mode (no Kafka needed)
    python main_consumer.py --topic ops-critical  # specific Kafka topic
"""

import argparse
import asyncio
import logging
import signal

from src.consumers.alert_consumer import AlertConsumer
from src.core.logging import setup_logging

logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="AIOpsOS Alert Consumer")
    parser.add_argument("--mock", action="store_true", help="Use DB poller instead of Kafka")
    parser.add_argument("--topic", default="ops-events", help="Kafka topic (default: ops-events)")
    args = parser.parse_args()

    setup_logging("INFO")

    consumer = AlertConsumer(mock=args.mock, topic=args.topic)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(consumer.stop()))

    await consumer.start()

    # Keep running until stopped
    while consumer._running:
        await asyncio.sleep(1)

    logger.info("Consumer shut down")


if __name__ == "__main__":
    asyncio.run(main())
