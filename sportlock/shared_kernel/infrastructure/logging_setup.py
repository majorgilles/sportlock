"""Logging: a rotating file for the service (1 MB × 3), plus stderr for the journal."""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "sportlock"
LOG_PATH = STATE_DIR / "sportlock.log"


def setup_logging() -> logging.Logger:
    """File log for the service (1 MB × 3), plus stderr for the journal."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sportlock")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        file_handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(file_handler)
        logger.addHandler(stream)
    return logger
