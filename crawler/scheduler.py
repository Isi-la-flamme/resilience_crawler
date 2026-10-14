"""Ordonnancement des requêtes et suivi des tâches d'un crawl multi-site."""

import logging
import time
from http.client import InvalidURL
from urllib.error import HTTPError, URLError

from .constants import USER_AGENT
from .fetcher import (
    DocumentDownloadDisabledError,
    DocumentTooLargeError,
    OutOfScopeRedirectError,
)

logger = logging.getLogger(__name__)


class CrawlScheduler:
    """Gère la file partagée, les workers et la persistance des pages."""

    @staticmethod
    def init_state(crawler):
        """Initialise la file et les compteurs d'exécution d'un crawler."""
        crawler.database.enqueue(crawler.start_url, [crawler.start_url])
        if crawler.refresh:
            crawler.database.queue_pages_for_refresh(crawler.start_url)
        logger.info(
            "CRAWL start %s (limit=%d shared_workers=%d)",
            crawler.start_url,
            crawler.max_pages,
            crawler.workers,
        )
        return {
            "crawler": crawler,
            "pages": [],
            "errors": [],
            "pending_pages": [],
            "active_count": 0,
            "processed_count": 0,
            "unchanged_count": 0,
            "last_flush": time.monotonic(),
        }

    @staticmethod
    def schedule_available(states, active, executor, max_workers):
        """Remplit le pool jusqu'à sa capacité ou l'absence d'URL admissible."""
        while len(active) < max_workers:
            if not CrawlScheduler._schedule_one_round(states, active, executor):
                break

    @staticmethod
    def _schedule_one_round(states, active, executor):
        """Réserve au plus une URL admissible et la soumet au pool."""
        progress = False
        for state in states:
            crawler = state["crawler"]
            page_count = (
                state["processed_count"]
                if crawler.refresh or not crawler.download_html
                else len(state["pages"])
            )
            if page_count + state["active_count"] >= crawler.max_pages:
                continue

            url = crawler.database.claim_next(
                crawler.start_url,
                include_existing=crawler.refresh,
            )
            if url is None:
                continue
            progress = True

            try:
                crawler._normalize_url(url)
            except ValueError:
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Ignoring out-of-scope URL: %s", url)
                continue

            if crawler._has_bare_host_path(url):
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Ignoring malformed URL ending in bare host: %s", url)
                continue

            if crawler._is_excluded_url(url):
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Ignoring excluded URL: %s", url)
                continue

            if not crawler._robots.can_fetch(USER_AGENT, url):
                crawler.database.set_url_status(crawler.start_url, url, "blocked")
                logger.info("Ignoring blocked URL per robots.txt: %s", url)
                continue

            logger.info("COLLECTOR queued URL: %s", url)
            future = executor.submit(crawler._process_url, url)
            active[future] = (state, url)
            state["active_count"] += 1
            return True

        return progress

    @staticmethod
    def flush_due(states, now):
        """Persiste les lots dont l'intervalle de vidage est écoulé."""
        for state in states:
            if (
                state["pending_pages"]
                and now - state["last_flush"] >= state["crawler"].database_flush_interval
            ):
                state["crawler"]._flush_pages(state["pending_pages"])
                state["last_flush"] = now

    @staticmethod
    def flush_all_pending(states):
        """Persiste immédiatement tous les lots en mémoire."""
        flushed = False
        for state in states:
            if state["pending_pages"]:
                state["crawler"]._flush_pages(state["pending_pages"])
                state["last_flush"] = time.monotonic()
                flushed = True
        return flushed

    @staticmethod
    def next_wait_timeout(states, now):
        """Retourne le délai avant le prochain vidage programmé."""
        deadlines = [
            max(0, state["crawler"].database_flush_interval - (now - state["last_flush"]))
            for state in states
            if state["pending_pages"]
        ]
        return min(deadlines) if deadlines else None

    @staticmethod
    def drain_completed(completed, active, states):
        """Traite les futures terminées et met à jour file et résultats."""
        for future in completed:
            state, url = active.pop(future)
            crawler = state["crawler"]
            state["active_count"] -= 1
            if crawler.refresh:
                state["processed_count"] += 1
            try:
                page, discovered_urls, validators, not_modified = future.result()
            except HTTPError as error:
                CrawlScheduler._handle_http_error(state, url, error)
                continue
            except DocumentTooLargeError as error:
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Skipping oversized document %s: %s", url, error)
                continue
            except DocumentDownloadDisabledError as error:
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Skipping document because downloads are disabled: %s", error)
                continue
            except InvalidURL as error:
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Skipping malformed URL %s: %s", url, error)
                continue
            except OutOfScopeRedirectError as error:
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("Skipping out-of-scope redirect for %s: %s", url, error)
                continue
            except (InvalidURL, URLError, TimeoutError, OSError, ValueError) as error:
                crawler.database.set_url_status(crawler.start_url, url, "failed", str(error))
                state["errors"].append({"url": url, "error": str(error)})
                logger.warning("Failed fetch for %s after retries: %s", url, error)
                continue

            if not_modified:
                state["unchanged_count"] += 1
                crawler.database.mark_page_unchanged(
                    crawler.start_url,
                    url,
                    validators,
                )
                logger.info("PAGE unchanged: %s", url)
                continue

            if page is None:
                if not crawler.database.has_document(crawler.start_url, url):
                    crawler.database.set_url_status(crawler.start_url, url, "skipped")
                continue

            crawler.database.enqueue(crawler.start_url, discovered_urls)
            if not crawler.download_html:
                crawler.database.set_url_status(crawler.start_url, url, "done")
                state["processed_count"] += 1
                logger.info(
                    "PAGE analyzed but not stored: %s (links=%d images=%d)",
                    url,
                    len(page["links"]),
                    len(page["images"]),
                )
                continue
            state["pending_pages"].append((page, discovered_urls))
            state["pages"].append(page)
            logger.info(
                "PAGE %d/%d %s (links=%d images=%d)",
                len(state["pages"]),
                crawler.max_pages,
                url,
                len(page["links"]),
                len(page["images"]),
            )

    @staticmethod
    def _handle_http_error(state, url, error):
        """Traduit un HTTPError en statut de file et éventuel résultat d'erreur."""
        crawler = state["crawler"]
        if error.code in {404, 410}:
            crawler.database.set_url_status(crawler.start_url, url, "skipped")
            logger.info("Ignoring missing resource HTTP %d: %s", error.code, url)
            return
        crawler.database.set_url_status(crawler.start_url, url, "failed", str(error))
        state["errors"].append({"url": url, "error": str(error)})
        logger.warning("Failed fetch for %s: %s", url, error)

    @staticmethod
    def collect_results(states):
        """Vide les dernières pages et construit un résultat par site."""
        results = []
        for state in states:
            if state["pending_pages"]:
                state["crawler"]._flush_pages(state["pending_pages"])
            logger.info(
                "CRAWL finished for %s (new_pages=%d, errors=%d)",
                state["crawler"].start_url,
                len(state["pages"]),
                len(state["errors"]),
            )
            results.append({
                "pages": state["pages"],
                "errors": state["errors"],
                "unchanged_pages": state["unchanged_count"],
            })
        return results
