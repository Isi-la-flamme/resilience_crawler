import argparse
import logging
from pathlib import Path

from crawler.cleaner import clean_database
from crawler.config import DEFAULT_CONFIG_PATH, load_config


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_DATABASE = load_config(DEFAULT_CONFIG_PATH)["database_path"]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Extrait le texte exploitable des pages, retire le head et les blocs "
            "répétés, exclut les textes de moins de 500 caractères et ajoute les "
            "nouvelles pages à la base SQLite."
        )
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=DEFAULT_SOURCE_DATABASE,
        help=f"Base SQLite d’origine (défaut : {DEFAULT_SOURCE_DATABASE.name})",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=PROJECT_ROOT / "clean_crawl.db",
        help="Base SQLite nettoyée (défaut : clean_crawl.db)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    counts = clean_database(args.source_db, args.output_db)
    print(
        f"Base nettoyée créée : {args.output_db.resolve()} "
        f"({counts['pages']} pages, {counts['boilerplate_blocks']} blocs répétés, "
        f"{counts['site_report']} sites)."
    )


if __name__ == "__main__":
    main()
