import logging

from .json_logging import JsonLineFormatter


def configure_logging(config):
    config["log_path"].parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(config["log_path"], encoding="utf-8")
    file_handler.setFormatter(JsonLineFormatter())
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config["log_level"]))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    return file_handler, console_handler