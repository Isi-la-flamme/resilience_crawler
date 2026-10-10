import argparse
import logging
from pathlib import Path

from crawler.cleaner import clean_database


PROJECT_ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Extrait le texte exploitable des pages, retire le head et les blocs "
            "répétés, exclut les textes de moins de 500 caractères et crée une "
            "nouvelle base SQLite."
        )
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=PROJECT_ROOT / "crawl.db",
        help="Base SQLite d’origine (défaut : crawl.db)",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=PROJECT_ROOT / "crawl-clean.db",
        help="Nouvelle base SQLite avec le texte brut (défaut : crawl-clean.db)",
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
