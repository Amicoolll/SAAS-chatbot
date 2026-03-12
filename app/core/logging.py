"""Structured logging for enterprise operations. Replace with your logging backend later."""
import logging
import sys
from typing import Any

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def log_operation(logger: logging.Logger, operation: str, **kwargs: Any) -> None:
    parts = [f"op={operation}"]
    for k, v in kwargs.items():
        if v is not None:
            parts.append(f"{k}={v}")
    logger.info(" | ".join(parts))
