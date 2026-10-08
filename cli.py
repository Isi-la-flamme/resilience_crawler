import argparse
import json
import logging

from urllib.error import HTTPError, URLError

from config import DEFAULT_CONFIG_PATH, load_config
from constants import USER_AGENT  # noqa: F401  (conservé pour compat imports externes)
from crawler import Crawler
from database import CrawlDatabase
from logging_setup import configure_logging


def main():
    cli = argparse.ArgumentParser(description="Explore les URL définies dans config.json.")
    cli.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Chemin du fichier de configuration")
    args = cli.parse_args()

    try:
        config = load_config(args.config)
        log_handlers = configure_logging(config)
        with CrawlDatabase(config["database_path"]) as database:
            crawlers = [
                Crawler(
                    start_url,
                    max_pages=config["max_pages"],
                    delay=config["delay"],
                    timeout=config["timeout"],
                    retries=config["retries"],
                    max_response_bytes=config["max_response_bytes"],
                    workers=config["workers"],
                    image_dir=config["images_path"],
                    documents_dir=config["documents_path"],
                    crawler_name=config["crawler_name"],
                    database_flush_interval=config["database_flush_interval"],
                    database=database,
                )
                for start_url in config["start_urls"]
            ]
            Crawler.crawl_many(crawlers)
            summary = database.summary()
        print(
            "Crawl terminé : "
            f"{summary['pages']} pages enregistrées, "
            f"{summary['images']} images téléchargées, "
            f"{summary['documents']} documents téléchargés."
        )
    except KeyboardInterrupt:
        cli.exit(130, "Crawler interrompu ; file enregistrée dans SQLite.\n")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
        cli.exit(1, f"Erreur de configuration, réseau ou enregistrement : {error}\n")
    finally:
        if "log_handlers" in locals():
            for handler in log_handlers:
                logging.getLogger().removeHandler(handler)
                handler.close()


if __name__ == "__main__":
    main()