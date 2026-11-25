"""Logging setup (defect D-10: the app had no logging at all).

``--verbose`` or ``OZEMPDF_LOG=1`` raises the level to DEBUG. Timings and stage
counts are logged at DEBUG so normal runs stay silent.
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER_NAME = "ozempdf"


def configure_logging(verbose: bool = False) -> logging.Logger:
    """Configure stderr logging once and return the package logger."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    env = (os.environ.get("OZEMPDF_LOG") or "").strip().lower()
    enabled = verbose or env in {"1", "true", "yes", "on", "debug"}
    logger.setLevel(logging.DEBUG if enabled else logging.WARNING)
    return logger


def get_logger() -> logging.Logger:
    """Return the package logger, configuring it defensively if needed."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        return configure_logging(False)
    return logger
