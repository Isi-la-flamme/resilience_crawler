import argparse
import json
import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from config import DEFAULT_CONFIG_PATH, load_config
from database import CrawlDatabase
from document_downloader import DocumentDownloader, document_extension
from fetcher import Fetcher
from image_downloader import ImageDownloader
from json_logging import JsonLineFormatter
from parser import PageParser
from ping import MISSING_STATUS_CODES, PingChecker

logger = logging.getLogger(__name__)
USER_AGENT = "YennengaCrawl/1.0"


class Crawler:
    def __init__(
        self,
        start_url,
        max_pages=1000,
        delay=1.0,
        timeout=10,
        retries=2,
        max_response_bytes=5 * 1024 * 1024,
        workers=4,
        image_dir="img",
        documents_dir="documents",
        crawler_name="Yennenga Crawl",
        database_flush_interval=30,
        sleep=time.sleep,
        fetcher=None,
        database=None,
    ):
        if max_pages < 1 or workers < 1:
            raise ValueError("max_pages et workers doivent être positifs.")
        if delay < 0 or timeout <= 0 or retries < 0:
            raise ValueError("delay doit être positif ou nul, timeout positif et retries positif ou nul.")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes doit être positif.")
        if database_flush_interval <= 0:
            raise ValueError("database_flush_interval doit être strictement positif.")

        self.start_url = self._normalize_url(start_url)
        self.origin = self._origin(self.start_url)
        self.max_pages = max_pages
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.max_response_bytes = max_response_bytes
        self.workers = workers
        self.crawler_name = crawler_name
        self.database_flush_interval = database_flush_interval
        self.sleep = sleep
        self._next_request_at = 0.0
        self._rate_limit_lock = threading.Lock()
        self.fetcher = fetcher or Fetcher(
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            user_agent=USER_AGENT,
        )
        self._owns_database = database is None
        self.database = database or CrawlDatabase(":memory:")
        self.site_name = urlsplit(self.start_url).hostname
        self.image_downloader = ImageDownloader(
            self.fetcher,
            Path(image_dir) / self.site_name,
            retries=retries,
            before_request=self._wait_for_delay,
        )
        self.document_downloader = DocumentDownloader(documents_dir)
        self.ping_checker = PingChecker(
            timeout=timeout,
            user_agent=USER_AGENT,
            before_request=self._wait_for_delay,
        )
        self._robots = self._load_robots()

    def crawl(self):
        try:
            return self._crawl()
        finally:
            if self._owns_database:
                self.database.close()

    def _crawl(self):
        return self.crawl_many([self])[0]

    @classmethod
    def crawl_many(cls, crawlers):
        crawlers_by_start_url = {}
        for crawler in crawlers:
            crawlers_by_start_url.setdefault(crawler.start_url, crawler)
        crawlers = list(crawlers_by_start_url.values())
        if not crawlers:
            return []
        if any(crawler.database is not crawlers[0].database for crawler in crawlers):
            raise ValueError("Tous les crawlers doivent partager la même base SQLite.")

        states = []
        for crawler in crawlers:
            crawler.database.enqueue(crawler.start_url, [crawler.start_url])
            states.append({
                "crawler": crawler,
                "pages": [],
                "errors": [],
                "pending_pages": [],
                "active_count": 0,
                "last_flush": time.monotonic(),
            })
            logger.info(
                "CRAWL start %s (limit=%d shared_workers=%d)",
                crawler.start_url,
                crawler.max_pages,
                max(item.workers for item in crawlers),
            )

        max_workers = max(crawler.workers for crawler in crawlers)
        cursor = 0

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="crawler") as executor:
            active = {}
            while True:
                while len(active) < max_workers:
                    scheduled = False
                    consumed_url = False
                    for _ in range(len(states)):
                        state = states[cursor]
                        cursor = (cursor + 1) % len(states)
                        crawler = state["crawler"]
                        if (
                            crawler.database.page_count(crawler.start_url)
                            + len(state["pending_pages"])
                            + state["active_count"]
                            >= crawler.max_pages
                        ):
                            continue

                        url = crawler.database.claim_next(crawler.start_url)
                        if url is None:
                            continue
                        consumed_url = True
                        if not crawler._robots.can_fetch(USER_AGENT, url):
                            crawler.database.set_url_status(
                                crawler.start_url, url, "blocked"
                            )
                            logger.info("Ignoring blocked URL per robots.txt: %s", url)
                            continue
                        try:
                            ping_status = crawler.ping_checker.ping(url)
                        except (HTTPError, URLError, TimeoutError, OSError) as error:
                            crawler.database.set_url_status(
                                crawler.start_url, url, "failed", str(error)
                            )
                            state["errors"].append({"url": url, "error": str(error)})
                            logger.warning("URL check failed for %s: %s", url, error)
                            continue
                        if ping_status in MISSING_STATUS_CODES:
                            crawler.database.set_url_status(
                                crawler.start_url, url, "skipped"
                            )
                            logger.info(
                                "COLLECTOR skipped missing URL HTTP %d: %s",
                                ping_status,
                                url,
                            )
                            continue

                        logger.info(
                            "COLLECTOR queued live URL HTTP %d: %s",
                            ping_status,
                            url,
                        )
                        future = executor.submit(crawler._process_url, url)
                        active[future] = (state, url)
                        state["active_count"] += 1
                        scheduled = True
                        break
                    if not scheduled:
                        if consumed_url:
                            continue
                        break

                now = time.monotonic()
                for state in states:
                    pending_pages = state["pending_pages"]
                    crawler = state["crawler"]
                    if pending_pages and (
                        now - state["last_flush"] >= crawler.database_flush_interval
                    ):
                        crawler._flush_pages(pending_pages)
                        state["last_flush"] = now

                if not active:
                    pending_states = [state for state in states if state["pending_pages"]]
                    if pending_states:
                        for state in pending_states:
                            state["crawler"]._flush_pages(state["pending_pages"])
                            state["last_flush"] = time.monotonic()
                        continue
                    break

                flush_deadlines = [
                    max(
                        0,
                        state["crawler"].database_flush_interval
                        - (now - state["last_flush"]),
                    )
                    for state in states
                    if state["pending_pages"]
                ]
                timeout = min(flush_deadlines) if flush_deadlines else None
                completed, _ = wait(
                    active,
                    timeout=timeout,
                    return_when=FIRST_COMPLETED,
                )
                if not completed:
                    continue

                for future in completed:
                    state, url = active.pop(future)
                    crawler = state["crawler"]
                    state["active_count"] -= 1
                    try:
                        page, discovered_urls = future.result()
                    except HTTPError as error:
                        if error.code in MISSING_STATUS_CODES:
                            crawler.database.set_url_status(
                                crawler.start_url, url, "skipped"
                            )
                            logger.info(
                                "Ignoring missing resource HTTP %d: %s",
                                error.code,
                                url,
                            )
                        else:
                            crawler.database.set_url_status(
                                crawler.start_url, url, "failed", str(error)
                            )
                            state["errors"].append({"url": url, "error": str(error)})
                            logger.warning("Failed fetch for %s: %s", url, error)
                        continue
                    except (URLError, TimeoutError, OSError, ValueError) as error:
                        crawler.database.set_url_status(
                            crawler.start_url, url, "failed", str(error)
                        )
                        state["errors"].append({"url": url, "error": str(error)})
                        logger.warning(
                            "Failed fetch for %s after retries: %s",
                            url,
                            error,
                        )
                        continue

                    if page is None:
                        if not crawler.database.has_document(crawler.start_url, url):
                            crawler.database.set_url_status(
                                crawler.start_url, url, "skipped"
                            )
                        continue

                    crawler.database.enqueue(crawler.start_url, discovered_urls)
                    state["pending_pages"].append((page, discovered_urls))
                    state["pages"].append(page)
                    logger.info(
                        "PAGE %d/%d %s (links=%d images=%d)",
                        crawler.database.page_count(crawler.start_url)
                        + len(state["pending_pages"]),
                        crawler.max_pages,
                        url,
                        len(page["links"]),
                        len(page["images"]),
                    )

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
            })
        return results

    def _flush_pages(self, pending_pages):
        count = len(pending_pages)
        self.database.save_pages(self.start_url, pending_pages)
        pending_pages.clear()
        logger.info("SQLite batch saved (%d pages)", count)

    def _process_url(self, url):
        self._wait_for_delay()
        body, content_type, charset = self.fetcher.fetch(url, retries=self.retries)
        extension = document_extension(url, content_type)
        if extension is not None:
            document = self.document_downloader.save(
                url,
                content_type,
                body,
                self.site_name,
            )
            document["url"] = url
            self.database.save_document(self.start_url, document)
            return None, []
        if content_type != "text/html":
            logger.info("Skipping non-html resource: %s", url)
            return None, []

        html = body.decode(charset or "utf-8", errors="replace")
        title, raw_links, raw_images, published_at, content = PageParser.parse_html(html)
        links = self._normalize_resources(url, raw_links, same_origin=True)
        image_urls = self._normalize_resources(url, raw_images, same_origin=False)
        downloaded_images = []

        for image_url in image_urls:
            if not self.database.claim_image(image_url):
                image_path = self.database.image_path(image_url)
                if image_path is not None:
                    downloaded_images.append(str(image_path))
                continue
            try:
                image_path = self.image_downloader.download(image_url)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                self.database.fail_image(image_url, error)
                logger.warning("Image download failed for %s: %s", image_url, error, exc_info=True)
            else:
                self.database.finish_image(image_url, image_path)
                downloaded_images.append(str(image_path))

        page = {
            "url": url,
            "title": title,
            "published_at": published_at,
            "content": content,
            "html": html,
            "crawler_name": self.crawler_name,
            "links": links,
            "images": image_urls,
            "downloaded_images": downloaded_images,
        }
        return page, links

    def _normalize_resources(self, page_url, resources, same_origin):
        normalized_urls = []
        for resource in resources:
            try:
                url = self._normalize_url(urljoin(page_url, resource), same_origin=same_origin)
            except ValueError:
                logger.debug("Ignoring unsupported or out-of-scope URL: %s", resource)
                continue
            if url not in normalized_urls:
                normalized_urls.append(url)
        return normalized_urls

    def _load_robots(self):
        parts = urlsplit(self.start_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            self._wait_for_delay()
            body, _, charset = self.fetcher.fetch(robots_url, retries=self.retries)
        except HTTPError as error:
            if error.code != 404:
                raise
            logger.info("No robots.txt at %s", robots_url)
            parser.parse([])
        except (URLError, TimeoutError, OSError, ValueError) as error:
            logger.warning("robots fetch failed for %s: %s", robots_url, error)
            parser.parse([])
        else:
            parser.parse(body.decode(charset or "utf-8", errors="replace").splitlines())
        return parser

    def _wait_for_delay(self):
        with self._rate_limit_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._next_request_at - now)
            if wait_seconds:
                self.sleep(wait_seconds)
            self._next_request_at = time.monotonic() + self.delay

    def _normalize_url(self, url, same_origin=True):
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError("L'URL doit utiliser HTTP ou HTTPS.")
        normalized = urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        ))
        if (
            same_origin
            and hasattr(self, "origin")
            and self._origin(normalized) != self.origin
        ):
            raise ValueError("Les liens hors du domaine de départ ne sont pas autorisés.")
        return normalized

    @staticmethod
    def _origin(url):
        parts = urlsplit(url)
        return parts.scheme.lower(), parts.netloc.lower()


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
            f"{summary['images']} images t?l?charg?es, "
            f"{summary['documents']} documents t?l?charg?s."
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
