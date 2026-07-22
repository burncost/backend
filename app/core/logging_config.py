"""
Centralized logging configuration for Burncost.
- Console output (human-readable) for development
- JSON structured output for production
- File output with rotation
- Request ID correlation
- Sensitive data masking
"""
import logging
import logging.handlers
import json
import os
import uuid
from datetime import datetime
from contextvars import ContextVar
from typing import Any, Dict

# ── Request Context (correlation across async calls) ──
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
vendor_id_var: ContextVar[str] = ContextVar("vendor_id", default="-")


class ContextFilter(logging.Filter):
    """Injects request_id, user_id, vendor_id into every log record."""

    def filter(self, record):
        record.request_id = request_id_var.get("-")
        record.user_id = user_id_var.get("-")
        record.vendor_id = vendor_id_var.get("-")
        return True


class SensitiveDataFilter(logging.Filter):
    """Masks sensitive data in log messages."""

    def filter(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            msg = record.msg
            for key in ("password", "token", "secret", "api_key", "authorization", "cookie"):
                if key in msg.lower():
                    idx = msg.lower().find(key)
                    end = min(len(msg), idx + len(key) + 50)
                    record.msg = msg[:idx + len(key)] + "=***" + msg[end:]
        return True


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "vendor_id": getattr(record, "vendor_id", "-"),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        return json.dumps(log_entry, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter for development."""

    def format(self, record):
        req_id = getattr(record, "request_id", "-")[:8]
        user = getattr(record, "user_id", "-")[:8]
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return (
            f"{timestamp} [{record.levelname:<8}] "
            f"[{req_id}] [{user}] "
            f"{record.name}:{record.funcName}:{record.lineno} "
            f"— {record.getMessage()}"
        )


def setup_logging(debug: bool = False, log_dir: str = "logs"):
    """Configure logging for the application."""
    os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    context_filter = ContextFilter()
    sensitive_filter = SensitiveDataFilter()

    # ── Console Handler ──
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.addFilter(context_filter)
    console.addFilter(sensitive_filter)
    console.setFormatter(ConsoleFormatter() if debug else JSONFormatter())
    root_logger.addHandler(console)

    # ── File Handler (rotating) ──
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "burncost.log"),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(context_filter)
    file_handler.addFilter(sensitive_filter)
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)

    # ── Error File Handler (errors only) ──
    error_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "burncost_errors.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.addFilter(context_filter)
    error_handler.addFilter(sensitive_filter)
    error_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(error_handler)

    # ── Suppress noisy loggers ──
    for noisy in ("pymongo", "urllib3", "httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root_logger.info(
        "Logging configured",
        extra={"extra_data": {"debug": debug, "log_dir": log_dir}},
    )