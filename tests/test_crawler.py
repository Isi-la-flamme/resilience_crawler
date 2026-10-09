import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import wait as wait_for_futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from crawler import Crawler, CrawlDatabase


class TestHandler(BaseHTTPRequestHandler):
    transient_requests = 0
    conditional_version = "1"
    conditional_request_headers = []
    excluded_route_requests = []
    missing_get_requests = 0
    missing_head_requests = 0
    head_requests = 0
    slow_active = 0
    slow_max_active = 0
    image_active = 0
    image_max_active = 0
    slow_lock = threading.Lock()

    def do_GET(self):
        final_path_segment = self.path.rstrip("/").rsplit("/", 1)[-1].lower()
        if final_path_segment in {"pricing", "token"}:
            type(self).excluded_route_requests.append(self.path)

        if self.path == "/robots.txt":
            self.respond(200, "User-agent: *\nDisallow: /private\n")
        elif self.path == "/missing":
            type(self).missing_get_requests += 1
            self.respond(404, "Introuvable")
        elif self.path == "/":
            self.respond(
                200,
                '<title>Accueil   du site</title>'
                '<a href="/next#top">Suite</a>'
                '<a href="/private">Privé</a>'
                '<a href="/missing">Absent</a>'
                '<a href="http://example.org/outside">Externe</a>',
            )
        elif self.path == "/next":
            self.respond(200, "<title>Page suivante</title>")
        elif self.path == "/temporary":
            type(self).transient_requests += 1
            status = 503 if type(self).transient_requests == 1 else 200
            self.respond(status, "<title>Récupérée</title>")
        elif self.path == "/large":
            self.respond(200, "<title>" + ("a" * 100) + "</title>")
        elif self.path == "/conditional":
            type(self).conditional_request_headers.append(
                self.headers.get("If-None-Match")
            )
            etag = f'"v{type(self).conditional_version}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.end_headers()
            else:
                body = (
                    f"<title>Version {type(self).conditional_version}</title>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", etag)
                self.send_header("Last-Modified", "Fri, 09 Oct 2026 00:00:00 GMT")
                self.end_headers()
                self.wfile.write(body)
        elif self.path == "/junk-route-root":
            self.respond(
                200,
                '<a href="/article/token/">Token</a>'
                '<a href="/article/pricing/">Pricing</a>'
                '<a href="/category/actualite/audience/page/4/pricing//pricing">'
                'Nested pricing</a>'
                '<a href="/article/normal">Normal</a>',
            )
        elif self.path == "/article/normal":
            self.respond(200, "<title>Normal</title>")
        elif self.path == "/queue-root":
            self.respond(200, '<a href="/queue-a">A</a><a href="/queue-b">B</a>')
        elif self.path == "/queue-a":
            self.respond(200, '<a href="/queue-shared">Partagé</a>')
        elif self.path == "/queue-b":
            self.respond(200, '<a href="/queue-shared">Partagé</a>')
        elif self.path == "/queue-shared":
            self.respond(200, "<title>Unique</title>")
        elif self.path == "/with-image":
            self.respond(200, '<title>Avec image</title><img src="/test-image.png">')
        elif self.path == "/with-missing-image":
            self.respond(200, '<title>Image absente</title><img src="/missing-image.png">')
        elif self.path == "/missing-image.png":
            self.respond(404, "Image introuvable")
        elif self.path == "/with-batch-images":
            self.respond(
                200,
                "".join(f'<img src="/batch-image/{index}.png">' for index in range(4)),
            )
        elif self.path.startswith("/batch-image/"):
            with type(self).slow_lock:
                type(self).image_active += 1
                type(self).image_max_active = max(
                    type(self).image_max_active,
                    type(self).image_active,
                )
            try:
                time.sleep(0.1)
                body = b"\x89PNG\r\n\x1a\n"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                with type(self).slow_lock:
                    type(self).image_active -= 1
        elif self.path == "/structured":
            self.respond(
                200,
                '<html><head><title>Article exemple</title>'
                '<meta property="article:published_time" content="2026-10-01T12:30:00Z">'
                '</head><body><nav>Menu ignoré</nav><main><article>'
                '<p>Contenu ciblé de l’article.</p>'
                '</article></main></body></html>',
            )
        elif self.path == "/document-links":
            self.respond(
                200,
                '<a href="/files/rapport.pdf">PDF</a>'
                '<a href="/files/rapport-final.docx">Word</a>'
                '<a href="/files/tableau.xlsm">Excel</a>'
                '<a href="/files/presentation.pptx">PowerPoint</a>'
                '<a href="/files/notes.odt">OpenDocument</a>'
                '<a href="/files/no-extension">PDF MIME</a>',
            )
        elif self.path.startswith("/files/"):
            extension = Path(self.path).suffix.lower()
            content_types = {
                ".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".odt": "application/vnd.oasis.opendocument.text",
            }
            body = b"test document bytes"
            self.send_response(200)
            content_type = "application/pdf" if extension == "" else content_types[extension]
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/test-image.png":
            body = b"\x89PNG\r\n\x1a\n"
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/parallel":
            self.respond(
                200,
                '<a href="/slow/one">one</a>'
                '<a href="/slow/two">two</a>'
                '<a href="/slow/three">three</a>',
            )
        elif self.path == "/batch-root":
            self.respond(200, '<a href="/fast">fast</a><a href="/slow/one">slow</a>')
        elif self.path == "/fast":
            self.respond(200, "<title>Fast</title>")
        elif self.path.startswith("/slow/"):
            with type(self).slow_lock:
                type(self).slow_active += 1
                type(self).slow_max_active = max(
                    type(self).slow_max_active,
                    type(self).slow_active,
                )
            time.sleep(0.1)
            self.respond(200, "<title>Slow</title>")
            with type(self).slow_lock:
                type(self).slow_active -= 1
        else:
            self.respond(404, "Introuvable")

    def do_HEAD(self):
        type(self).head_requests += 1
        if self.path == "/missing":
            type(self).missing_head_requests += 1
            status = 404
        else:
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def respond(self, status, body):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8" if isinstance(body, str) else "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


class CrawlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def make_crawler(self, path="/", **kwargs):
        return Crawler(
            self.base_url + path,
            delay=0,
            sleep=lambda _: None,
            **kwargs,
        )

    def test_crawls_same_site_and_respects_robots(self):
        TestHandler.missing_get_requests = 0
        TestHandler.missing_head_requests = 0
        result = self.make_crawler().crawl()

        self.assertEqual([page["url"] for page in result["pages"]], [
            self.base_url + "/",
            self.base_url + "/next",
        ])
        self.assertEqual(result["pages"][0]["title"], "Accueil du site")
        self.assertNotIn(
            self.base_url + "/private",
            [page["url"] for page in result["pages"]],
        )
        self.assertNotIn(
            "http://example.org/outside",
            result["pages"][0]["links"],
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(TestHandler.missing_head_requests, 0)
        self.assertEqual(TestHandler.missing_get_requests, 1)

    def test_obeys_page_limit(self):
        result = self.make_crawler(max_pages=1).crawl()

        self.assertEqual(len(result["pages"]), 1)

    def test_does_not_enqueue_url_more_than_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                result = self.make_crawler(
                    "/queue-root",
                    database=database,
                    workers=4,
                ).crawl()
                shared_count = database.queued_url_count(
                    self.base_url + "/queue-root",
                    self.base_url + "/queue-shared",
                )

        self.assertEqual(shared_count, 1)
        self.assertEqual(len(result["pages"]), 4)

    def test_resumes_from_saved_queue_with_page_limit_per_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                with patch("crawler.engine.metrics.dump"):
                    first = self.make_crawler(
                        "/queue-root",
                        database=database,
                        max_pages=2,
                    ).crawl()
                    second = self.make_crawler(
                        "/queue-root",
                        database=database,
                        max_pages=2,
                    ).crawl()

                total_pages = database.page_count(self.base_url + "/queue-root")

        self.assertEqual(len(first["pages"]), 2)
        self.assertEqual(len(second["pages"]), 2)
        self.assertEqual(total_pages, 4)
        self.assertFalse(
            {page["url"] for page in first["pages"]}
            & {page["url"] for page in second["pages"]}
        )

    def test_refresh_uses_conditional_requests_and_updates_changed_pages(self):
        TestHandler.conditional_version = "1"
        TestHandler.conditional_request_headers = []
        start_url = self.base_url + "/conditional"

        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                first = self.make_crawler(
                    "/conditional",
                    database=database,
                ).crawl()
                first_page = database.page_cache(start_url, start_url)
                unchanged = self.make_crawler(
                    "/conditional",
                    database=database,
                    refresh=True,
                ).crawl()
                etag_after_304 = database.page_cache(start_url, start_url)["etag"]

                TestHandler.conditional_version = "2"
                changed = self.make_crawler(
                    "/conditional",
                    database=database,
                    refresh=True,
                ).crawl()
                stored_title = database._connection.execute(
                    "SELECT title FROM pages WHERE start_url=? AND url=?",
                    (start_url, start_url),
                ).fetchone()[0]

        self.assertEqual(first["pages"][0]["title"], "Version 1")
        self.assertEqual(first_page["etag"], '"v1"')
        self.assertEqual(etag_after_304, '"v1"')
        self.assertEqual(unchanged["pages"], [])
        self.assertEqual(unchanged["unchanged_pages"], 1)
        self.assertEqual(changed["pages"][0]["title"], "Version 2")
        self.assertEqual(stored_title, "Version 2")
        self.assertEqual(
            TestHandler.conditional_request_headers,
            [None, '"v1"', '"v1"'],
        )

    def test_skips_token_and_pricing_routes_at_discovery_and_queue(self):
        TestHandler.excluded_route_requests = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                start_url = self.base_url + "/junk-route-root"
                excluded_urls = [
                    self.base_url + "/article/token/",
                    self.base_url + "/article/pricing/",
                    self.base_url
                    + "/category/actualite/audience/page/4/pricing//pricing",
                ]
                database.enqueue(start_url, excluded_urls)
                result = self.make_crawler(
                    "/junk-route-root",
                    database=database,
                ).crawl()
                statuses = dict(database._connection.execute(
                    "SELECT url, status FROM url_queue WHERE start_url=?",
                    (start_url,),
                ).fetchall())

        self.assertEqual(
            [page["url"] for page in result["pages"]],
            [
                self.base_url + "/junk-route-root",
                self.base_url + "/article/normal",
            ],
        )
        self.assertEqual(TestHandler.excluded_route_requests, [])
        self.assertEqual(
            statuses[self.base_url + "/article/token/"],
            "skipped",
        )
        self.assertEqual(
            statuses[self.base_url + "/article/pricing/"],
            "skipped",
        )
        self.assertEqual(
            statuses[
                self.base_url
                + "/category/actualite/audience/page/4/pricing//pricing"
            ],
            "skipped",
        )

    def test_retries_transient_server_error(self):
        TestHandler.transient_requests = 0
        result = self.make_crawler("/temporary", retries=1).crawl()

        self.assertEqual(len(result["pages"]), 1)
        self.assertEqual(TestHandler.transient_requests, 2)

    def test_rejects_non_http_start_url(self):
        with self.assertRaises(ValueError):
            Crawler("file:///tmp/page.html", sleep=lambda _: None)

    def test_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            self.make_crawler(max_pages=0)

    def test_reports_response_larger_than_limit(self):
        result = self.make_crawler(
            "/large",
            max_response_bytes=40,
        ).crawl()

        self.assertFalse(result["pages"])
        self.assertIn("limite de 40 octets", result["errors"][0]["error"])

    def test_cli_output_is_json_serializable(self):
        result = self.make_crawler(max_pages=1).crawl()

        self.assertIn("pages", json.loads(json.dumps(result)))

    def test_saves_pages_and_errors_to_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                result = self.make_crawler(database=database).crawl()
                self.assertEqual(database.page_count(self.base_url + "/"), 2)

        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(result["errors"], [])

    def test_saves_page_metadata_content_and_full_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                result = self.make_crawler(
                    "/structured",
                    database=database,
                    crawler_name="Crawler de test",
                ).crawl()
                stored_page = database._connection.execute(
                    """SELECT id, crawled_at, published_at, title, content, html, crawler_name
                       FROM pages WHERE url=?""",
                    (self.base_url + "/structured",),
                ).fetchone()

        page = result["pages"][0]
        self.assertEqual(page["id"], stored_page[0])
        self.assertTrue(stored_page[1])
        self.assertEqual(stored_page[2], "2026-10-01T12:30:00Z")
        self.assertEqual(stored_page[3], "Article exemple")
        self.assertEqual(stored_page[4], "Contenu ciblé de l’article.")
        self.assertIn("<html>", stored_page[5])
        self.assertNotIn("Menu ignoré", stored_page[4])
        self.assertEqual(stored_page[6], "Crawler de test")

    def test_downloads_supported_documents_into_site_named_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            documents_dir = Path(temp_dir) / "documents"
            start_url = self.base_url + "/document-links"
            with CrawlDatabase(database_path) as database:
                result = self.make_crawler(
                    "/document-links",
                    database=database,
                    documents_dir=documents_dir,
                ).crawl()
                rows = database._connection.execute(
                    "SELECT url, local_path, filename, content_type, size_bytes "
                    "FROM documents ORDER BY url"
                ).fetchall()
                self.assertEqual(database.summary()["documents"], 6)

            self.assertEqual(len(result["pages"]), 1)
            self.assertEqual(len(rows), 6)
            self.assertEqual(
                {Path(row[1]).suffix for row in rows},
                {".pdf", ".docx", ".xlsm", ".pptx", ".odt"},
            )
            for _, local_path, filename, _, size_bytes in rows:
                file_path = Path(local_path)
                self.assertEqual(file_path.parent, documents_dir / "127.0.0.1")
                self.assertEqual(file_path.name, filename)
                self.assertTrue(file_path.is_file())
                stem, hash_part = filename.rsplit("_", 1)
                url_hash = hash_part.rsplit(".", 1)[0]
                self.assertEqual(len(url_hash), 8)
                self.assertTrue(all(character in "0123456789abcdef" for character in url_hash))
                self.assertEqual(size_bytes, len(b"test document bytes"))

            with CrawlDatabase(database_path) as database:
                self.assertIsNone(database.claim_next(start_url))

    def test_downloads_image_into_configured_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                result = self.make_crawler(
                    "/with-image",
                    database=database,
                    image_dir=Path(temp_dir) / "img",
                    workers=2,
                ).crawl()

            page = result["pages"][0]
            self.assertEqual(page["images"], [self.base_url + "/test-image.png"])
            self.assertEqual(len(page["downloaded_images"]), 1)
            image_path = Path(page["downloaded_images"][0])
            self.assertTrue(
                image_path.is_relative_to(Path(temp_dir) / "img" / "127.0.0.1")
            )
            self.assertRegex(image_path.name, r"^test-image_[0-9a-f]{8}\.png$")
            self.assertEqual(image_path.read_bytes(), b"\x89PNG\r\n\x1a\n")

    def test_downloads_images_in_configured_batches(self):
        TestHandler.image_max_active = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.make_crawler(
                "/with-batch-images",
                image_dir=Path(temp_dir) / "img",
                image_batch_size=3,
            ).crawl()

        self.assertEqual(len(result["pages"][0]["downloaded_images"]), 4)
        self.assertGreaterEqual(TestHandler.image_max_active, 2)

    def test_missing_image_is_logged_without_traceback(self):
        image_url = self.base_url + "/missing-image.png"
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                with self.assertLogs("crawler", level="INFO") as logs:
                    self.make_crawler(
                        "/with-missing-image",
                        database=database,
                    ).crawl()
                image_status = database._connection.execute(
                    "SELECT status, error FROM images WHERE url=?",
                    (image_url,),
                ).fetchone()

        self.assertEqual(image_status[0], "failed")
        self.assertIn("HTTP Error 404", image_status[1])
        self.assertTrue(any("Image unavailable HTTP 404" in entry for entry in logs.output))
        self.assertFalse(any("Traceback" in entry for entry in logs.output))

    def test_workers_fetch_pages_in_parallel(self):
        TestHandler.slow_max_active = 0
        self.make_crawler("/parallel", workers=3).crawl()

        self.assertGreaterEqual(TestHandler.slow_max_active, 2)

    def test_fetches_urls_without_preflight_head_requests(self):
        TestHandler.head_requests = 0
        result = self.make_crawler("/parallel", workers=3).crawl()

        self.assertEqual(len(result["pages"]), 4)
        self.assertEqual(TestHandler.head_requests, 0)

    def test_start_urls_share_one_worker_pool(self):
        TestHandler.slow_max_active = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                crawlers = [
                    self.make_crawler(
                        path,
                        database=database,
                        workers=2,
                    )
                    for path in ("/parallel", "/batch-root")
                ]
                results = Crawler.crawl_many(crawlers)

                self.assertEqual(
                    database.page_count(self.base_url + "/parallel"),
                    4,
                )
                self.assertEqual(
                    database.page_count(self.base_url + "/batch-root"),
                    3,
                )

        self.assertEqual([len(result["pages"]) for result in results], [4, 3])
        self.assertGreaterEqual(TestHandler.slow_max_active, 2)

    def test_graceful_interrupt_saves_completed_pages_and_resumes_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            start_url = self.base_url + "/parallel"
            crawler = self.make_crawler(
                "/parallel",
                database=CrawlDatabase(database_path),
                workers=2,
            )
            original_wait = wait_for_futures
            interrupted = False

            def interrupt_once(*args, **kwargs):
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                return original_wait(*args, **kwargs)

            try:
                with patch("crawler.engine.wait", side_effect=interrupt_once):
                    with self.assertRaises(KeyboardInterrupt):
                        crawler.crawl()
                crawler.database.close()

                with CrawlDatabase(database_path) as database:
                    self.assertEqual(database.page_count(start_url), 1)
                    self.assertEqual(
                        database.claim_next(start_url),
                        self.base_url + "/slow/one",
                    )
            finally:
                if crawler.database._connection:
                    crawler.database.close()

    def test_flushes_completed_pages_at_configured_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                original_save_pages = database.save_pages
                flushes = []

                def record_flush(start_url, page_results):
                    flushes.append((
                        time.monotonic(),
                        [page["url"] for page, _ in page_results],
                    ))
                    return original_save_pages(start_url, page_results)

                database.save_pages = record_flush
                started_at = time.monotonic()
                self.make_crawler(
                    "/batch-root",
                    database=database,
                    workers=2,
                    database_flush_interval=0.02,
                ).crawl()

        fast_flushes = [
            elapsed
            for timestamp, urls in flushes
            if self.base_url + "/fast" in urls
            for elapsed in [timestamp - started_at]
        ]
        slow_finish = next(
            timestamp - started_at
            for timestamp, urls in flushes
            if self.base_url + "/slow/one" in urls
        )
        self.assertTrue(fast_flushes)
        self.assertLess(fast_flushes[0], slow_finish)

    def test_workers_do_not_exceed_page_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                result = self.make_crawler(
                    "/parallel",
                    workers=4,
                    max_pages=2,
                    database=database,
                ).crawl()
                saved_pages = database.page_count(self.base_url + "/parallel")

        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(saved_pages, 2)


if __name__ == "__main__":
    unittest.main()
