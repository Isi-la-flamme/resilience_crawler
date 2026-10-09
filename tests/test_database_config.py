import json
import io
import logging
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from config import load_config
from logging_setup import configure_logging
from database import CrawlDatabase
from json_logging import JsonLineFormatter


class DatabaseConfigTests(unittest.TestCase):
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
                "database_file": "crawl.db",
                "images_dir": "img",
                "log_file": "crawler.jsonl",
            }), encoding="utf-8")

            config = load_config(config_path)

        self.assertEqual(config["workers"], 6)
        self.assertEqual(config["image_batch_size"], 3)
        self.assertEqual(config["crawler_name"], "Yennenga Crawl")
        self.assertEqual(config["database_flush_interval"], 30)
        self.assertEqual(config["documents_path"], config_path.parent / "documents")
        self.assertEqual(config["database_path"], config_path.parent / "crawl.db")
        self.assertEqual(config["images_path"], config_path.parent / "img")
        self.assertEqual(config["log_path"], config_path.parent / "crawler.jsonl")

    def test_rejects_missing_start_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"start_urls": []}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "start_urls"):
                load_config(config_path)

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
                self.assertEqual(database.claim_next(start_url), "https://example.org/next")

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
