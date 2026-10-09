import argparse
import json
import logging
from pathlib import Path

from urllib.error import HTTPError, URLError

from crawler import Crawler, CrawlDatabase
from crawler.config import DEFAULT_CONFIG_PATH, load_config
from crawler.excel_export import export_database_to_excel
from crawler.logging_setup import configure_logging


def _positive_int(value):
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("doit être un entier positif.") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("doit être un entier positif.")
    return parsed


def _non_negative_int(value):
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("doit être un entier positif ou nul.") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("doit être un entier positif ou nul.")
    return parsed


def main(argv=None):
    cli = argparse.ArgumentParser(description="Explore les URL définies dans config.json.")
    cli.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Chemin du fichier de configuration")
    cli.add_argument(
        "--max-pages",
        type=_positive_int,
        help="Nombre maximal de pages traitées par site (remplace la configuration)",
    )
    cli.add_argument(
        "--workers",
        type=_positive_int,
        help="Nombre de workers (remplace la configuration)",
    )
    cli.add_argument(
        "--retries",
        type=_non_negative_int,
        help="Nombre de retries après l’essai initial (remplace la configuration)",
    )
    cli.add_argument(
        "--refresh",
        action="store_true",
        help="Vérifie si les pages déjà enregistrées ont changé",
    )
    cli.add_argument(
        "--export-excel",
        metavar="PATH",
        help="Exporte pages, images et documents en Excel sans lancer de crawl",
    )
    args = cli.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.export_excel:
            output_path = Path(args.export_excel)
            if not output_path.is_absolute():
                output_path = config["database_path"].parent / output_path
            counts = export_database_to_excel(
                config["database_path"],
                output_path,
            )
            print(
                f"Export Excel créé : {output_path.resolve()} "
                f"({counts['pages']} pages, {counts['images']} images, "
                f"{counts['documents']} documents)."
            )
            return

        for option in ("max_pages", "workers", "retries"):
            value = getattr(args, option)
            if value is not None:
                config[option] = value

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
                    image_batch_size=config["image_batch_size"],
                    image_dir=config["images_path"],
                    documents_dir=config["documents_path"],
                    crawler_name=config["crawler_name"],
                    database_flush_interval=config["database_flush_interval"],
                    refresh=args.refresh,
                    database=database,
                )
                for start_url in config["start_urls"]
            ]
            previous_summary = database.summary()
            results = Crawler.crawl_many(crawlers)
            summary = database.summary()
        new_page_count = sum(len(result["pages"]) for result in results)
        unchanged_page_count = sum(
            result.get("unchanged_pages", 0) for result in results
        )
        new_image_count = summary["images"] - previous_summary["images"]
        new_document_count = summary["documents"] - previous_summary["documents"]
        page_summary = (
            f"{new_page_count} pages nouvelles ou actualisées, "
            f"{unchanged_page_count} inchangées"
            if args.refresh
            else f"{new_page_count} nouvelles pages"
        )
        print(
            "Crawl terminé : "
            f"{page_summary} "
            f"({summary['pages']} au total), "
            f"{new_image_count} nouvelles images ({summary['images']} au total), "
            f"{new_document_count} nouveaux documents ({summary['documents']} au total)."
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