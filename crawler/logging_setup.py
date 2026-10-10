import logging

from .json_logging import JsonLineFormatter


def configure_logging(config):
    """Ajoute les gestionnaires console et JSONL au logger racine.

    `config` doit contenir `log_path` (chemin) et `log_level` (nom standard
    d'un niveau `logging`). Le répertoire du journal est créé si nécessaire.
    Les gestionnaires sont retournés pour permettre à l'appelant de les retirer
    et fermer après le crawl.
    """
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