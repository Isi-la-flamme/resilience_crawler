import logging
import threading
import time

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from pathlib import Path

from urllib.error import HTTPError, URLError

from urllib.parse import urljoin, urlsplit, urlunsplit

from urllib.robotparser import RobotFileParser


from constants import USER_AGENT
from metrics import metrics

from database import CrawlDatabase

from document_downloader import DocumentDownloader, document_extension

from fetcher import Fetcher

from image_downloader import ImageDownloader

from parser import PageParser

from ping import MISSING_STATUS_CODES, PingChecker


logger = logging.getLogger(__name__)



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
        self._next_request_at = {}
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

        states = [cls._init_state(crawler) for crawler in crawlers]
        max_workers = max(crawler.workers for crawler in crawlers)

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="crawler") as executor:
            active = {}
            while True:
                cls._schedule_available(states, active, executor, max_workers)

                now = time.monotonic()
                cls._flush_due(states, now)

                if not active:
                    if cls._flush_all_pending(states):
                        continue
                    break

                timeout = cls._next_wait_timeout(states, now)
                completed, _ = wait(
                    active,
                    timeout=timeout,
                    return_when=FIRST_COMPLETED,
                )
                if completed:
                    cls._drain_completed(completed, active, states)
        metrics.dump(Path("metrics.json"))

        return cls._collect_results(states)

    @staticmethod
    def _init_state(crawler):
        crawler.database.enqueue(crawler.start_url, [crawler.start_url])
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
            "last_flush": time.monotonic(),
        }

    @staticmethod
    def _schedule_available(states, active, executor, max_workers):
        """Remplit le pool tant qu'il reste de la place et des URLs éligibles."""
        while len(active) < max_workers:
            if not Crawler._schedule_one_round(states, active, executor):
                break

    @staticmethod
    def _schedule_one_round(states, active, executor):
        """Une passe sur tous les états. Retourne True si une URL a été soumise ou consommée."""
        progress = False
        for state in states:
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
            progress = True

            if not crawler._robots.can_fetch(USER_AGENT, url):
                crawler.database.set_url_status(crawler.start_url, url, "blocked")
                logger.info("Ignoring blocked URL per robots.txt: %s", url)
                continue

            try:
                ping_status = crawler.ping_checker.ping(url)
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                crawler.database.set_url_status(crawler.start_url, url, "failed", str(error))
                state["errors"].append({"url": url, "error": str(error)})
                logger.warning("URL check failed for %s: %s", url, error)
                continue

            if ping_status in MISSING_STATUS_CODES:
                crawler.database.set_url_status(crawler.start_url, url, "skipped")
                logger.info("COLLECTOR skipped missing URL HTTP %d: %s", ping_status, url)
                continue

            logger.info("COLLECTOR queued live URL HTTP %d: %s", ping_status, url)
            future = executor.submit(crawler._process_url, url)
            active[future] = (state, url)
            state["active_count"] += 1
            return True

        return progress

    @staticmethod
    def _flush_due(states, now):
        for state in states:
            if (
                state["pending_pages"]
                and now - state["last_flush"] >= state["crawler"].database_flush_interval
            ):
                state["crawler"]._flush_pages(state["pending_pages"])
                state["last_flush"] = now

    @staticmethod
    def _flush_all_pending(states):
        flushed = False
        for state in states:
            if state["pending_pages"]:
                state["crawler"]._flush_pages(state["pending_pages"])
                state["last_flush"] = time.monotonic()
                flushed = True
        return flushed

    @staticmethod
    def _next_wait_timeout(states, now):
        deadlines = [
            max(0, state["crawler"].database_flush_interval - (now - state["last_flush"]))
            for state in states
            if state["pending_pages"]
        ]
        return min(deadlines) if deadlines else None

    @staticmethod
    def _drain_completed(completed, active, states):
        for future in completed:
            state, url = active.pop(future)
            crawler = state["crawler"]
            state["active_count"] -= 1
            try:
                page, discovered_urls = future.result()
            except HTTPError as error:
                Crawler._handle_http_error(state, url, error)
                continue
            except (URLError, TimeoutError, OSError, ValueError) as error:
                crawler.database.set_url_status(crawler.start_url, url, "failed", str(error))
                state["errors"].append({"url": url, "error": str(error)})
                logger.warning("Failed fetch for %s after retries: %s", url, error)
                continue

            if page is None:
                if not crawler.database.has_document(crawler.start_url, url):
                    crawler.database.set_url_status(crawler.start_url, url, "skipped")
                continue

            crawler.database.enqueue(crawler.start_url, discovered_urls)
            state["pending_pages"].append((page, discovered_urls))
            state["pages"].append(page)
            logger.info(
                "PAGE %d/%d %s (links=%d images=%d)",
                crawler.database.page_count(crawler.start_url) + len(state["pending_pages"]),
                crawler.max_pages,
                url,
                len(page["links"]),
                len(page["images"]),
            )

    @staticmethod
    def _handle_http_error(state, url, error):
        crawler = state["crawler"]
        if error.code in MISSING_STATUS_CODES:
            crawler.database.set_url_status(crawler.start_url, url, "skipped")
            logger.info("Ignoring missing resource HTTP %d: %s", error.code, url)
            return
        crawler.database.set_url_status(crawler.start_url, url, "failed", str(error))
        state["errors"].append({"url": url, "error": str(error)})
        logger.warning("Failed fetch for %s: %s", url, error)

    @staticmethod
    def _collect_results(states):
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
        self._wait_for_delay(url)
        body, content_type, charset = self.fetcher.fetch(url, retries=self.retries)

        kind = self._classify(url, content_type)

        if kind == "document":
            self._save_document(url, content_type, body)
            return None, []

        if kind == "skip":
            logger.info("Skipping non-html resource: %s", url)
            return None, []

        return self._build_html_page(url, body, charset)

    def _classify(self, url, content_type):
        if document_extension(url, content_type) is not None:
            return "document"
        if content_type == "text/html":
            return "html"
        return "skip"

    def _save_document(self, url, content_type, body):
        document = self.document_downloader.save(
            url,
            content_type,
            body,
            self.site_name,
        )
        document["url"] = url
        self.database.save_document(self.start_url, document)

    def _build_html_page(self, url, body, charset):
        html = body.decode(charset or "utf-8", errors="replace")
        with metrics.measure("parse"):
            title, raw_links, raw_images, published_at, content = PageParser.parse_html(html)
        links = self._normalize_resources(url, raw_links, same_origin=True)
        image_urls = self._normalize_resources(url, raw_images, same_origin=False)
        downloaded_images = self._download_images(image_urls)

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

    
    def _download_images(self, image_urls):
        downloaded = []
        for image_url in image_urls:
            if not self.database.claim_image(image_url):
                existing = self.database.image_path(image_url)
                if existing is not None:
                    downloaded.append(str(existing))
                continue
            try:
                with metrics.measure("image"):
                    image_path = self.image_downloader.download(image_url)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                self.database.fail_image(image_url, error)
                logger.warning(
                    "Image download failed for %s: %s",
                    image_url,
                    error,
                    exc_info=True,
                )
            else:
                self.database.finish_image(image_url, image_path)
                downloaded.append(str(image_path))
        return downloaded
    

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
            self._wait_for_delay(robots_url)
            body, _, charset = self.fetcher.fetch(robots_url, retries=self.retries)
        except HTTPError as error:
            if error.code == 404:
                logger.info("No robots.txt at %s", robots_url)
            else:
                logger.warning(
                    "robots.txt unavailable HTTP %d for %s — assuming allow-all",
                    error.code,
                    robots_url,
                )
            parser.parse([])
        except (URLError, TimeoutError, OSError, ValueError) as error:
            logger.warning(
                "robots fetch failed for %s: %s — assuming allow-all",
                robots_url,
                error,
            )
            parser.parse([])
        else:
            parser.parse(body.decode(charset or "utf-8", errors="replace").splitlines())
        return parser

    def _wait_for_delay(self, url):
        with metrics.measure("wait"):
            host = urlsplit(url).hostname or ""
            with self._rate_limit_lock:
                now = time.monotonic()
                wait_seconds = max(0.0, self._next_request_at.get(host, 0.0) - now)
                if wait_seconds:
                    logger.info("WAIT host=%s sleep=%.2fs", host, wait_seconds)
                    self.sleep(wait_seconds)
                self._next_request_at[host] = time.monotonic() + self.delay

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

