import logging
import os
from logging.handlers import RotatingFileHandler

_shared_file_handler = None
_shared_console_handler = None

def _get_shared_handlers(log_dir="logs"):
    global _shared_file_handler, _shared_console_handler
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if _shared_file_handler is None:
        log_file = os.path.join(log_dir, "trading_bot.log")
        _shared_file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5, delay=True
        )
        _shared_file_handler.setFormatter(formatter)

    if _shared_console_handler is None:
        _shared_console_handler = logging.StreamHandler()
        _shared_console_handler.setFormatter(formatter)

    return _shared_file_handler, _shared_console_handler


def setup_logger(name="TradingBot", log_dir="logs"):
    """
    Sets up a logger with shared Console and File handlers.
    Safe against Windows file locking issues.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        file_handler, console_handler = _get_shared_handlers(log_dir)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "TradingBot"):
    """Convenience alias for getting a configured logger."""
    return setup_logger(name)


# Create a default instance for easy import
logger = setup_logger()
