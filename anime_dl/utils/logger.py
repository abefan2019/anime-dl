import logging
import sys
from logging.config import dictConfig

from anime_dl.utils.config_loader import ConfigLoader

config_loader = ConfigLoader()
logging_level = int(config_loader.get(section="LOGGING", key="level"))
webui_log = config_loader.get(section="WEBUI", key="log")


def make_console_streams_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass


make_console_streams_safe()

logging_config = {
    "version": 1,
    "handlers": {
        "console_handler": {
            "class": "logging.StreamHandler",   # console
            "level": logging_level,
            "formatter": "console_formatter",
        },
        "file_handler": {
            "class": "logging.FileHandler",
            "filename": webui_log,
            "encoding": "utf-8",
            "errors": "backslashreplace",
            "level": logging_level,
            "formatter": "file_formatter",
        },
    },
    "formatters": {
        "console_formatter": {"format": "[%(asctime)s] %(name)s [%(levelname)-8s]: %(message)s"},
        "file_formatter": {"format": "[%(asctime)s][%(levelname)-8s]: %(message)s"},
    },
    "loggers": {
        "": {
            "handlers": ["console_handler", "file_handler"],
            "level": "DEBUG",
            "propagate": True,
        }
    },
}
dictConfig(logging_config)


class LoggerMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]


class Logger(metaclass=LoggerMeta):
    def __init__(self) -> None:
        self.logger = logging.getLogger("ANIME-DL")

    def info(self, *messages) -> None:
        for message in messages:
            self.logger.info(message)

    def warning(self, *messages) -> None:
        for message in messages:
            self.logger.warning(message)

    def error(self, *messages) -> None:
        for message in messages:
            self.logger.error(message)

    def reset_webui_log(self) -> None:
        with open(webui_log, "w", encoding="utf-8") as file:
            file.truncate(0)

    def read_webui_log(self) -> str:
        with open(webui_log, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-15:])
