import json
import io
import logging
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from crawler.config import database_filename_for_urls, load_config
from crawler.config import DEFAULT_CONFIG_PATH
from crawler.database import CrawlDatabase
from crawler.json_logging import JsonLineFormatter
from crawler.logging_setup import configure_logging


class DatabaseConfigTests(unittest.TestCase):
    def test_default_configuration_path_is_yaml(self):
        self.assertEqual(DEFAULT_CONFIG_PATH.name, "config.yaml")
        self.assertTrue(DEFAULT_CONFIG_PATH.is_file())

    def test_default_yaml_configuration_loads(self):
        config = load_config(DEFAULT_CONFIG_PATH)
        self.assertIn("start_urls", config)
        self.assertTrue(config["start_urls"])
        self.assertEqual(config["database_file"], "crawl_agriculture.db")
        self.assertEqual(config["database_path"], DEFAULT_CONFIG_PATH.parent / config["database_file"])
        self.assertNotIn("database_file:", DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))

    def test_loads_yaml_configuration_and_resolves_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "start_urls:\n"
                "  - https://ministere.gov.bf/\n"
                "max_pages: 12\n"
                "workers: 2\n"
                "delay: 0.5\n"
                "timeout: 10\n"
                "retries: 1\n"
                "max_response_bytes: 2048\n"
                "images_dir: img\n"
                "documents_dir: documents\n"
                "log_file: crawler.jsonl\n",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config["start_urls"], ["https://ministere.gov.bf/"])
        self.assertEqual(config["max_pages"], 12)
        self.assertEqual(config["database_file"], "crawl_ministere.db")
        self.assertEqual(config["database_path"], config_path.parent / "crawl_ministere.db")

    def test_loads_workers_and_resolves_paths_relative_to_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://example.org", "https://example.com"],
                "max_pages": 25,
                "workers": 6,
                "image_batch_size": 3,
                "delay": 0,
                "timeout": 5,
                "retries": 1,
                "max_response_bytes": 1024,
                "database_file": "custom-name.db",
                "images_dir": "img",
                "log_file": "crawler.jsonl",
            }), encoding="utf-8")

            config = load_config(config_path)

        self.assertEqual(config["workers"], 6)
        self.assertEqual(config["image_batch_size"], 3)
        self.assertEqual(config["crawler_name"], "Yennenga Crawl")
        self.assertEqual(config["database_flush_interval"], 30)
        self.assertEqual(config["documents_path"], config_path.parent / "documents")
        self.assertEqual(config["database_file"], "crawl_example-com-example-org.db")
        self.assertEqual(
            config["database_path"],
            config_path.parent / "crawl_example-com-example-org.db",
        )
        self.assertEqual(config["images_path"], config_path.parent / "img")
        self.assertEqual(config["log_path"], config_path.parent / "crawler.jsonl")

    def test_rejects_missing_start_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"start_urls": []}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "start_urls"):
                load_config(config_path)

    def test_database_filename_is_stable_for_domains_and_multiple_ministries(self):
        self.assertEqual(
            database_filename_for_urls([
                "https://www.agriculture.gov.bf/accueil",
                "https://agriculture.gov.bf/le-ministere",
            ]),
            "crawl_agriculture.db",
        )
        self.assertEqual(
            database_filename_for_urls([
                "https://www.finance.gov.bf/",
                "https://www.agriculture.gov.bf/",
            ]),
            "crawl_agriculture-finance.db",
        )
        self.assertEqual(
            database_filename_for_urls([
                "https://example.org/",
                "https://example.com/",
            ]),
            "crawl_example-com-example-org.db",
        )

    def test_queue_deduplicates_and_persists_page_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            start_url = "https://example.org/"
            with CrawlDatabase(database_path) as database:
                database.enqueue(start_url, [start_url, start_url])
                self.assertEqual(database.claim_next(start_url), start_url)
                database.save_page(
                    start_url,
                    {
                        "url": start_url,
                        "title": "Home",
                        "links": ["https://example.org/next"],
                        "images": [],
                    },
                    ["https://example.org/next"],
                )
                database.enqueue(start_url, ["https://example.org/next"])
                self.assertEqual(database.page_count(start_url), 1)
                self.assertEqual(database.claim_next(start_url), "https://example.org/next")
                database.set_url_status(start_url, "https://example.org/next", "failed", "temporary")

            with CrawlDatabase(database_path) as database:
                self.assertEqual(database.page_count(start_url), 1)
                self.assertIsNone(database.claim_next(start_url))
                self.assertEqual(database.retry_failed_urls([start_url]), 1)
                self.assertEqual(database.claim_next(start_url), "https://example.org/next")

    def test_failed_urls_are_not_automatically_requeued(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            start_url = "https://example.org/"
            failed_url = start_url + "unavailable"
            with CrawlDatabase(database_path) as database:
                database.enqueue(start_url, [failed_url])
                self.assertEqual(database.claim_next(start_url), failed_url)
                database.set_url_status(start_url, failed_url, "failed", "timeout")

            with CrawlDatabase(database_path) as database:
                self.assertIsNone(database.claim_next(start_url))
                self.assertEqual(database.retry_failed_urls([start_url]), 1)
                self.assertEqual(database.claim_next(start_url), failed_url)

    def test_finds_pending_sites_with_optional_start_url_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            queued_start = "https://queued.example/"
            other_start = "https://other.example/"
            done_start = "https://done.example/"
            with CrawlDatabase(database_path) as database:
                database.enqueue(queued_start, [queued_start + "pending"])
                database.enqueue(other_start, [other_start + "pending"])
                database.enqueue(done_start, [done_start + "done"])
                database.claim_next(done_start)
                database.save_page(
                    done_start,
                    {
                        "url": done_start + "done",
                        "title": "Done",
                        "links": [],
                        "images": [],
                    },
                    [],
                )

                self.assertEqual(
                    database.queued_start_urls(),
                    [other_start, queued_start],
                )
                self.assertEqual(
                    database.queued_start_urls([other_start, done_start]),
                    [other_start],
                )
                self.assertEqual(database.queued_start_urls([]), [])

    def test_config_filter_leaves_unconfigured_sites_queued(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            configured_start = "https://configured.example/"
            unconfigured_start = "https://previously-known.example/"
            with CrawlDatabase(database_path) as database:
                database.enqueue(configured_start, [configured_start + "pending"])
                database.enqueue(unconfigured_start, [unconfigured_start + "pending"])

                self.assertEqual(
                    database.queued_start_urls([configured_start]),
                    [configured_start],
                )
                outside_status = database._connection.execute(
                    "SELECT status FROM url_queue WHERE start_url=?",
                    (unconfigured_start,),
                ).fetchone()[0]

                self.assertEqual(outside_status, "queued")
                self.assertEqual(
                    database.claim_next(unconfigured_start),
                    unconfigured_start + "pending",
                )

    def test_reseeds_missing_queue_from_saved_page_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            start_url = "https://example.org/"
            saved_url = start_url + "already-crawled"
            pending_url = start_url + "not-crawled-yet"
            with CrawlDatabase(database_path) as database:
                database.save_page(
                    start_url,
                    {
                        "url": saved_url,
                        "title": "Saved",
                        "links": [saved_url, pending_url],
                        "images": [],
                    },
                    [saved_url, pending_url],
                )
                database._connection.execute("DELETE FROM url_queue")

                self.assertEqual(database.ensure_start_url_queued(start_url), 2)
                self.assertEqual(database.ensure_start_url_queued(start_url), 0)
                self.assertEqual(
                    database.queued_start_urls(),
                    [start_url],
                )
                self.assertEqual(database.claim_next(start_url), pending_url)

    def test_reseeding_does_not_reactivate_failed_or_skipped_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            start_url = "https://example.org/"
            failed_url = start_url + "failed"
            skipped_url = start_url + "skipped"
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                database.save_page(
                    start_url,
                    {
                        "url": start_url + "index",
                        "title": "Index",
                        "links": [failed_url, skipped_url],
                        "images": [],
                    },
                    [failed_url, skipped_url],
                )
                database.set_url_status(start_url, failed_url, "failed", "network error")
                database.set_url_status(start_url, skipped_url, "skipped", "too large")

                self.assertEqual(database.ensure_start_url_queued(start_url), 0)
                statuses = dict(database._connection.execute(
                    "SELECT url, status FROM url_queue WHERE start_url=?",
                    (start_url,),
                ))

        self.assertEqual(statuses[failed_url], "failed")
        self.assertEqual(statuses[skipped_url], "skipped")

    def test_seeds_new_start_url_when_queue_and_pages_are_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            start_url = "https://new.example/"
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                self.assertEqual(database.ensure_start_url_queued(start_url), 1)
                self.assertEqual(database.claim_next(start_url), start_url)

    def test_migrates_existing_pages_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            connection = sqlite3.connect(database_path)
            connection.executescript("""
                CREATE TABLE pages (
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    links_json TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    crawled_at TEXT NOT NULL,
                    PRIMARY KEY (start_url, url)
                );
                INSERT INTO pages VALUES (
                    'https://example.org/', 'https://example.org/page',
                    'Existing page', '[]', '[]', '2026-01-01T00:00:00+00:00'
                );
            """)
            connection.close()

            with CrawlDatabase(database_path) as database:
                row = database._connection.execute(
                    """SELECT id, url_hash, title, published_at, content, html, crawler_name
                       FROM pages WHERE url='https://example.org/page'"""
                ).fetchone()

        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], CrawlDatabase.page_url_hash("https://example.org/page"))
        self.assertEqual(row[2], "Existing page")
        self.assertEqual(row[3:6], (None, None, None))
        self.assertEqual(row[6], "Yennenga Crawl")

    def test_pages_already_stored_are_never_claimed_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "crawl.db"
            start_url = "https://example.org/"
            page_url = "https://example.org/page"
            with CrawlDatabase(database_path) as database:
                database.enqueue(start_url, [page_url])
                database.claim_next(start_url)
                database.save_page(
                    start_url,
                    {
                        "url": page_url,
                        "title": "Stored page",
                        "links": [],
                        "images": [],
                    },
                    [],
                )
                database.set_url_status(start_url, page_url, "queued")

            with CrawlDatabase(database_path) as database:
                self.assertIsNone(database.claim_next(start_url))
                self.assertEqual(
                    database._connection.execute(
                        "SELECT status FROM url_queue WHERE url=?",
                        (page_url,),
                    ).fetchone()[0],
                    "done",
                )

    def test_saves_multiple_pages_in_one_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            start_url = "https://example.org/"
            page_results = [
                (
                    {
                        "url": f"{start_url}page-{number}",
                        "title": f"Page {number}",
                        "links": [],
                        "images": [],
                    },
                    [],
                )
                for number in range(2)
            ]
            with CrawlDatabase(Path(temp_dir) / "crawl.db") as database:
                database.enqueue(start_url, [page["url"] for page, _ in page_results])
                for page, _ in page_results:
                    self.assertEqual(database.claim_next(start_url), page["url"])
                database.save_pages(start_url, page_results)
                self.assertEqual(database.page_count(start_url), 2)
                self.assertTrue(all(page.get("id") for page, _ in page_results))
                hashes = database._connection.execute(
                    "SELECT url, url_hash FROM pages ORDER BY url"
                ).fetchall()
                statuses = database._connection.execute(
                    "SELECT status FROM url_queue ORDER BY url"
                ).fetchall()

        self.assertEqual(statuses, [("done",), ("done",)])
        self.assertEqual(
            hashes,
            [
                (f"{start_url}page-0", CrawlDatabase.page_url_hash(f"{start_url}page-0")),
                (f"{start_url}page-1", CrawlDatabase.page_url_hash(f"{start_url}page-1")),
            ],
        )

    def test_json_log_formatter_produces_json_line(self):
        record = logging.LogRecord(
            "crawler.fetcher",
            logging.INFO,
            "fetcher.py",
            12,
            "Fetched %s",
            ("https://example.org",),
            None,
        )
        entry = json.loads(JsonLineFormatter().format(record))

        self.assertEqual(entry["level"], "INFO")
        self.assertEqual(entry["logger"], "crawler.fetcher")
        self.assertEqual(entry["message"], "Fetched https://example.org")

    def test_configure_logging_writes_progress_to_console_and_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "crawler.jsonl"
            console_output = io.StringIO()
            with redirect_stderr(console_output):
                handlers = configure_logging({"log_path": log_path, "log_level": "INFO"})
                try:
                    logging.getLogger("crawler").info("PAGE 1/10 https://example.org/")
                finally:
                    for handler in handlers:
                        logging.getLogger().removeHandler(handler)
                        handler.close()

            self.assertIn("PAGE 1/10 https://example.org/", console_output.getvalue())
            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["message"], "PAGE 1/10 https://example.org/")


if __name__ == "__main__":
    unittest.main()
