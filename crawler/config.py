import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml


# Configuration par défaut résolue indépendamment du répertoire de lancement.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path=DEFAULT_CONFIG_PATH):
    """Charge, valide et complète la configuration du crawler.

    Les fichiers `.json` sont lus comme JSON ; tous les autres suffixes sont
    interprétés comme YAML. Les chemins de sortie relatifs sont résolus par
    rapport au dossier du fichier de configuration, pas au répertoire courant.

    Args:
        path: Fichier JSON ou YAML. Par défaut, `config.yaml` à la racine du projet.

    Returns:
        Dictionnaire de configuration validé, avec les chemins absolus ajoutés
        sous `database_path`, `images_path`, `documents_path` et `log_path`.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
        ValueError: Si le document, une valeur ou un chemin requis est invalide.
        json.JSONDecodeError: Si un fichier JSON est mal formé.
    """
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as config_file:
        if config_path.suffix.lower() == ".json":
            config = json.load(config_file)
        else:
            try:
                config = yaml.safe_load(config_file)
            except yaml.YAMLError as error:
                raise ValueError(f"Configuration YAML invalide : {error}") from error

    if not isinstance(config, dict):
        raise ValueError("La configuration doit être un objet YAML ou JSON.")

    start_urls = config.get("start_urls")
    if not isinstance(start_urls, list) or not start_urls:
        raise ValueError("start_urls doit être une liste non vide.")
    if any(not isinstance(url, str) or not url.strip() for url in start_urls):
        raise ValueError("Chaque entrée de start_urls doit être une URL non vide.")

    for key in ("max_pages", "workers", "max_response_bytes"):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} doit être un entier positif.")

    image_batch_size = config.get("image_batch_size", 1)
    if (
        not isinstance(image_batch_size, int)
        or isinstance(image_batch_size, bool)
        or image_batch_size < 1
    ):
        raise ValueError("image_batch_size doit être un entier positif.")
    config["image_batch_size"] = image_batch_size

    for key in ("delay", "timeout"):
        value = config.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{key} doit être un nombre.")
    if config["delay"] < 0 or config["timeout"] <= 0:
        raise ValueError("delay doit être positif ou nul et timeout strictement positif.")

    flush_interval = config.get("database_flush_interval", 30)
    if (
        not isinstance(flush_interval, (int, float))
        or isinstance(flush_interval, bool)
        or flush_interval <= 0
    ):
        raise ValueError("database_flush_interval doit être un nombre strictement positif.")
    config["database_flush_interval"] = flush_interval

    retries = config.get("retries")
    if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
        raise ValueError("retries doit être un entier positif ou nul.")

    config.setdefault("documents_dir", "documents")

    for key in ("images_dir", "documents_dir", "log_file"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} doit être un chemin non vide.")

    log_level = config.get("log_level", "INFO")
    if not isinstance(log_level, str) or log_level.upper() not in {
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    }:
        raise ValueError("log_level doit être DEBUG, INFO, WARNING, ERROR ou CRITICAL.")
    config["log_level"] = log_level.upper()
    crawler_name = config.get("crawler_name", "Yennenga Crawl")
    if not isinstance(crawler_name, str) or not crawler_name.strip():
        raise ValueError("crawler_name doit être un nom non vide.")
    config["crawler_name"] = crawler_name.strip()
    config["database_file"] = database_filename_for_urls(start_urls)
    config["database_path"] = config_path.resolve().parent / config["database_file"]
    config["images_path"] = config_path.resolve().parent / config["images_dir"]
    config["documents_path"] = config_path.resolve().parent / config["documents_dir"]
    config["log_path"] = config_path.resolve().parent / config["log_file"]
    return config


def database_filename_for_urls(start_urls):
    """Construit un nom stable depuis les ministères des URL de départ."""
    ministries = set()
    for url in start_urls:
        try:
            hostname = urlsplit(url).hostname
        except ValueError as error:
            raise ValueError(f"URL de départ invalide : {url}") from error
        if not hostname:
            raise ValueError(f"URL de départ sans hôte valide : {url}")

        hostname = hostname.rstrip(".").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        labels = hostname.split(".")
        if len(labels) >= 3 and labels[-2:] == ["gov", "bf"]:
            ministry = "-".join(labels[:-2])
        else:
            ministry = "-".join(labels)
        ministry = re.sub(r"[^a-z0-9-]+", "-", ministry).strip("-")
        if not ministry:
            raise ValueError(f"Impossible de déduire le ministère depuis : {url}")
        ministries.add(ministry)

    return f"crawl_{'-'.join(sorted(ministries))}.db"
