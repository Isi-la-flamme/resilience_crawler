import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cli


class CliTests(unittest.TestCase):
    def test_runtime_options_override_config_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://example.org", "https://example.com"],
                "max_pages": 25,
                "workers": 6,
                "delay": 0,
                "timeout": 5,
                "retries": 1,
                "max_response_bytes": 1024,
                "database_file": "crawl.db",
                "images_dir": "img",
                "log_file": "crawler.jsonl",
            }), encoding="utf-8")

            with (
                patch("cli.configure_logging", return_value=[]),
                patch("cli.CrawlDatabase") as database_class,
                patch("cli.Crawler") as crawler_class,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                database = database_class.return_value.__enter__.return_value
                database.summary.return_value = {
                    "pages": 0,
                    "images": 0,
                    "documents": 0,
                }
                crawler_class.crawl_many.return_value = [
                    {"pages": []},
                    {"pages": []},
                ]

                cli.main([
                    "--config", str(config_path),
                    "--max-pages", "12",
                    "--workers", "3",
                    "--retries", "0",
                ])

        self.assertEqual(len(crawler_class.call_args_list), 2)
        for call in crawler_class.call_args_list:
            self.assertEqual(call.kwargs["max_pages"], 12)
            self.assertEqual(call.kwargs["workers"], 3)
            self.assertEqual(call.kwargs["retries"], 0)

    def test_rejects_invalid_runtime_limits(self):
        for option, value in (
            ("--max-pages", "0"),
            ("--workers", "-1"),
            ("--retries", "-1"),
        ):
            with self.subTest(option=option), self.assertRaises(SystemExit) as error:
                cli.main([option, value])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
