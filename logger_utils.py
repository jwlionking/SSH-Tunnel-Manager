import logging
import os
import sys
from logging.handlers import RotatingFileHandler

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(APP_DIR, "tunnel_manager.log")

_log_file_handler = None


def init_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handlers = [logging.StreamHandler()] if debug else [logging.NullHandler()]
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger().setLevel(level)


def setup_file_logging() -> None:
    global _log_file_handler
    try:
        if _log_file_handler is None:
            _log_file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            _log_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            logging.getLogger().addHandler(_log_file_handler)
            logging.info("File logging initialized")
    except Exception as exc:
        print(f"Warning: Could not setup file logging: {exc}")


def close_file_logging() -> None:
    global _log_file_handler
    try:
        if _log_file_handler:
            logging.getLogger().removeHandler(_log_file_handler)
            _log_file_handler.close()
            _log_file_handler = None
    except Exception as exc:
        print(f"Warning: Could not close file logging: {exc}")
