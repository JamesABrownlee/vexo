import logging
from .settings import LOGGING_LEVEL


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


def set_logger(logger, filename=None):
    logger.setLevel(LOGGING_LEVEL)
    logger.propagate = False  # ⛔ Prevent propagation to root logger

    if not logger.handlers:  # ✅ Prevent multiple handler attachments
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(LoggingFormatter())
        logger.addHandler(console_handler)


    return logger
