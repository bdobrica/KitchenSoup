"""Idle service placeholders, with startup checks and graceful termination."""

import argparse
import logging
import signal
from pathlib import Path
from threading import Event

from app.config import Settings
from app.dependencies import check_dependencies

HEARTBEAT = Path("/tmp/kitchensoup-heartbeat")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("worker", "reconciler"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    check_dependencies(Settings())
    logging.info("%s placeholder ready; job processing is not implemented", args.role)
    try:
        while not stopped.is_set():
            HEARTBEAT.touch()
            stopped.wait(5)
    finally:
        HEARTBEAT.unlink(missing_ok=True)
        logging.info("%s placeholder stopped", args.role)


if __name__ == "__main__":
    main()
