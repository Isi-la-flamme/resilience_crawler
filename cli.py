"""Interface en ligne de commande pour le crawl et l'export Excel.

La configuration par défaut est `config.yaml`. Sans `--config` et sans URL
positionnelle, la commande reprend seulement les files SQLite en attente. Avec
`--config`, elle se limite aux URL configurées et reprend leur file avant de
compléter leurs points de départ. Les limites de portée du crawler restent
appliquées quel que soit le mode de lancement.
"""

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
    """Convertit une option CLI en entier strictement positif.

    Args:
        value: Texte fourni après une option numérique.

    Returns:
        La valeur convertie en `int`.

    Raises:
        argparse.ArgumentTypeError: Si la valeur n'est pas un entier positif.
    """
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("doit être un entier positif.") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("doit être un entier positif.")
    return parsed


def _non_negative_int(value):
    """Convertit une option CLI en entier positif ou nul.

    Args:
        value: Texte fourni après une option numérique.

    Returns:
        La valeur convertie en `int`.

    Raises:
        argparse.ArgumentTypeError: Si la valeur n'est pas un entier ou est négative.
    """
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("doit être un entier positif ou nul.") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("doit être un entier positif ou nul.")
    return parsed


def main(argv=None):
    """Analyse les options et exécute un crawl ou un export Excel.

    Les options `--max-pages`, `--workers` et `--retries` remplacent les valeurs
    du fichier de configuration. `--export-excel` exporte la base configurée
    puis termine sans lancer de crawl.

    La sélection des sites suit ces règles :

    - URL positionnelles : elles remplacent les `start_urls` du fichier.
    - `--config` explicite : seuls les sites demandés ou définis dans le fichier
      sont sélectionnés ; les URL en attente sont prioritaires, sinon le point
      de départ est initialisé depuis les liens déjà enregistrés ou le start URL.
    - Sans URL ni `--config` : toutes les files SQLite en attente sont reprises ;
      si elles sont vides, un rappel invite à lancer avec `--config`.
    - `--retry-failed` réactive explicitement les échecs dans le périmètre
      sélectionné ; `--refresh` revalide les pages déjà enregistrées.
    - `--no-images` conserve les URL d'images extraites mais désactive leur
      téléchargement et leur stockage local.
    - `--no-documents` ignore les documents reconnus sans les enregistrer.
    - `--no-html` analyse le HTML pour découvrir les liens et ressources, sans
      enregistrer les pages elles-mêmes.

    Les gestionnaires de logs créés sont toujours détachés et fermés en fin
    d'exécution. Les erreurs utilisateur, réseau et stockage sont converties
    en erreur CLI avec le code 1 ; une interruption clavier utilise le code 130.

    Args:
        argv: Arguments à analyser. Si `None`, utilise `sys.argv`.

    Returns:
        `None` après un export réussi, ou après un crawl achevé/sans travail.

    Raises:
        SystemExit: Pour une erreur d'arguments ou une erreur d'exécution CLI.
    """
    cli = argparse.ArgumentParser(
        description=(
            "Sans --config, reprend les sites présents dans la file SQLite. "
            "Avec --config, reprend les files des sites configurés ou redémarre "
            "depuis leurs URL de départ si aucune file n’est en attente."
        )
    )
    cli.add_argument(
        "--config",
        nargs="?",
        const="",
        help="Chemin du fichier de configuration (par défaut : config.yaml)",
    )
    cli.add_argument(
        "start_urls",
        nargs="*",
        metavar="URL",
        help="URL de départ à crawler ; si elles existent déjà, reprend leur file SQLite",
    )
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
        "--no-images",
        action="store_true",
        help="Désactive le téléchargement des images (leurs URL restent collectées)",
    )
    cli.add_argument(
        "--no-html",
        action="store_true",
        help="N'enregistre pas les pages HTML (les analyse pour découvrir liens et ressources)",
    )
    cli.add_argument(
        "--no-documents",
        action="store_true",
        help="Désactive le téléchargement et le stockage des documents",
    )
    cli.add_argument(
        "--retry-failed",
        action="store_true",
        help="Remet les URL en échec dans la file avant de reprendre le crawl",
    )
    cli.add_argument(
        "--export-excel",
        metavar="PATH",
        help="Exporte pages, images et documents en Excel sans lancer de crawl",
    )
    args = cli.parse_args(argv)
    config_path = args.config or DEFAULT_CONFIG_PATH

    try:
        config = load_config(config_path)
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

        if args.no_html and args.no_images and args.no_documents:
            cli.error(
                "HTML, images et documents sont tous désactivés ; "
                "réactivez au moins un type de contenu."
            )

        for option in ("max_pages", "workers", "retries"):
            value = getattr(args, option)
            if value is not None:
                config[option] = value

        log_handlers = configure_logging(config)
        with CrawlDatabase(config["database_path"]) as database:
            requested_start_urls = args.start_urls or (
                config["start_urls"] if args.config is not None else []
            )
            queue_only = not requested_start_urls and not args.refresh
            if args.retry_failed:
                retried_count = database.retry_failed_urls(
                    requested_start_urls if requested_start_urls else None
                )
                print(f"{retried_count} URL en échec remises dans la file.")
            queued_before_seeding = database.queued_start_urls(
                requested_start_urls if requested_start_urls else None
            )
            if requested_start_urls and not args.refresh:
                for start_url in requested_start_urls:
                    database.ensure_start_url_queued(start_url)
            queued_start_urls = database.queued_start_urls(
                requested_start_urls if requested_start_urls else None
            )
            if queue_only:
                if not queued_start_urls:
                    print(
                        "Aucun site avec des URL en attente dans la base. "
                        "Pour démarrer depuis les sites configurés, lancez : "
                        "python cli.py --config config.yaml"
                    )
                    return
                start_urls = queued_start_urls
                print(
                    f"Reprise de {len(start_urls)} site(s) en attente dans SQLite."
                )
            elif queued_start_urls:
                queued_first = [
                    start_url for start_url in queued_before_seeding
                    if start_url in queued_start_urls
                ]
                start_urls = queued_first + [
                    start_url for start_url in queued_start_urls
                    if start_url not in queued_first
                ]
                print(
                    f"Priorité aux URL en attente dans SQLite : "
                    f"{len(start_urls)} site(s)."
                )
            else:
                start_urls = requested_start_urls
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
                    download_html=not args.no_html,
                    download_images=not args.no_images,
                    download_documents=not args.no_documents,
                )
                for start_url in start_urls
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