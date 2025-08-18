import logging
import os
import sys

# Determine app directory (same logic as tunnel_manager)
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(APP_DIR, 'tunnel_manager.log')

# Track file handler so we can close it for file ops
_log_file_handler = None

def init_logging(debug: bool = False):
    """Initialize base logging handlers.
    - Console logs only when debug is True
    - No file handler here; use setup_file_logging() after app init
    """
    handlers = [logging.StreamHandler()] if debug else [logging.NullHandler()]
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers,
    )


def setup_file_logging():
    """Attach file handler to root logger if not already attached."""
    global _log_file_handler
    try:
        if _log_file_handler is None:
            _log_file_handler = logging.FileHandler(LOG_FILE)
            _log_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            logging.getLogger().addHandler(_log_file_handler)
            logging.info("File logging initialized")
    except Exception as e:
        print(f"Warning: Could not setup file logging: {e}")


def close_file_logging():
    """Detach and close file handler to allow file operations."""
    global _log_file_handler
    try:
        if _log_file_handler:
            logging.getLogger().removeHandler(_log_file_handler)
            _log_file_handler.close()
            _log_file_handler = None
            logging.info("File logging closed")
    except Exception as e:
        print(f"Warning: Could not close file logging: {e}")
