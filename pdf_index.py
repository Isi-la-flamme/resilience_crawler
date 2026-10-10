import argparse
import logging
from pathlib import Path

from crawler.pdf_index import build_pdf_index, search_pdf_index


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_DATABASE = PROJECT_ROOT / "crawl.db"
DEFAULT_INDEX_DATABASE = PROJECT_ROOT / "pdf-index.db"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extraire et rechercher le texte des PDF collectés."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    index_parser = commands.add_parser("index", help="Créer l’index de recherche PDF")
    index_parser.add_argument(
        "--source-db",
        type=Path,
        default=DEFAULT_SOURCE_DATABASE,
        help="Base SQLite source (défaut : crawl.db)",
    )
    index_parser.add_argument(
        "--index-db",
        type=Path,
        default=DEFAULT_INDEX_DATABASE,
        help="Base d’index nouvelle (défaut : pdf-index.db)",
    )

    search_parser = commands.add_parser("search", help="Rechercher dans les PDF indexés")
    search_parser.add_argument("query", help="Mots à rechercher")
    search_parser.add_argument(
        "--index-db",
        type=Path,
        default=DEFAULT_INDEX_DATABASE,
        help="Base d’index PDF (défaut : pdf-index.db)",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Nombre maximal de résultats (défaut : 20)",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.command == "index":
        counts = build_pdf_index(args.source_db, args.index_db)
        print(
            f"Index PDF créé : {counts['output']} "
            f"({counts['indexed']} indexés, {counts['searchable']} avec texte, "
            f"{counts['failed']} erreurs)."
        )
        return

    results = search_pdf_index(args.index_db, args.query, args.limit)
    if not results:
        print("Aucun résultat.")
        return
    for title, site_name, url, local_path, page_count, status, excerpt in results:
        print(f"\n{title} — {site_name} ({page_count} pages, {status})")
        print(url)
        print(local_path)
        print(excerpt)


if __name__ == "__main__":
    main()
