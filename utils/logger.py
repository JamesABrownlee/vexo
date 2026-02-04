import logging
from pathlib import Path

# Default logging level
LOGGING_LEVEL = logging.INFO

# Log file path
LOG_FILE_PATH = Path("data/vexo.log")


class LoggingFormatter(logging.Formatter):
    def __init__(self):
        super().__init__()
        self.default_time_format = "%Y-%m-%d %H:%M:%S"
        self.black = "\x1b[30m"
        self.red = "\x1b[31m"
        self.green = "\x1b[32m"
        self.yellow = "\x1b[33m"
        self.blue = "\x1b[34m"
        self.gray = "\x1b[38m"
        self.purple = "\x1b[35m\x1b[34m"
        self.cyan = "\x1b[36m"
        self.reset = "\x1b[0m"
        self.bold = "\x1b[1m"
        self.COLORS = {
            logging.DEBUG: self.gray + self.bold,
            logging.INFO: self.blue + self.bold,
            logging.WARNING: self.yellow + self.bold,
            logging.ERROR: self.red,
            logging.CRITICAL: self.red + self.bold,
        }

    def format(self, record):
        log_color = self.COLORS[record.levelno]
        format = "(black){asctime}(reset) (levelcolor){levelname:<8}(reset) (green){name}(reset)  (cyan){message}"
        format = format.replace("(black)", self.black + self.bold)
        format = format.replace("(reset)", self.reset)
        format = format.replace("(levelcolor)", log_color)
        format = format.replace("(green)", self.green + self.bold)
        format = format.replace("(cyan)", self.cyan)
        formatter = logging.Formatter(format, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)


class PlainFormatter(logging.Formatter):
    """Plain text formatter for file logging (no ANSI codes)."""
    def __init__(self):
        super().__init__(
            fmt="{asctime} {levelname:<8} {name}  {message}",
            datefmt="%Y-%m-%d %H:%M:%S",
            style="{"
        )


def set_logger(logger, filename=None):
    logger.setLevel(LOGGING_LEVEL)
    logger.propagate = False  # ⛔ Prevent propagation to root logger

    if not logger.handlers:  # ✅ Prevent multiple handler attachments
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(LoggingFormatter())
        logger.addHandler(console_handler)
        
        # File handler (shared log file)
        LOG_FILE_PATH.parent.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(LOG_FILE_PATH, encoding='utf-8')
        file_handler.setFormatter(PlainFormatter())
        logger.addHandler(file_handler)

    return logger


def get_last_log_lines(count: int = 500) -> str:
    """Read the last N lines from the log file."""
    if not LOG_FILE_PATH.exists():
        return "No log file found."
    
    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return ''.join(lines[-count:])
    except Exception as e:
        return f"Error reading logs: {e}"

