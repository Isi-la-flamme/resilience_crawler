import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cli


class CliTests(unittest.TestCase):
    def test_config_without_path_uses_default_yaml(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config) as load_config,
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = []
            database.ensure_start_url_queued.return_value = 0
            database.summary.return_value = {
                "pages": 0,
                "images": 0,
                "documents": 0,
            }
            crawler_class.crawl_many.return_value = [{"pages": []}]

            cli.main(["--config"])

        load_config.assert_called_once_with(cli.DEFAULT_CONFIG_PATH)
        crawler_class.assert_called_once()

    def test_no_images_flag_disables_image_downloads(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = []
            database.ensure_start_url_queued.return_value = 0
            database.summary.return_value = {
                "pages": 0,
                "images": 0,
                "documents": 0,
            }
            crawler_class.crawl_many.return_value = [{"pages": []}]

            cli.main(["--config", "--no-images"])

        self.assertFalse(crawler_class.call_args.kwargs["download_images"])

    def test_no_html_flag_disables_page_storage(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = []
            database.ensure_start_url_queued.return_value = 0
            database.summary.return_value = {
                "pages": 0,
                "images": 0,
                "documents": 0,
            }
            crawler_class.crawl_many.return_value = [{"pages": []}]

            cli.main(["--config", "--no-html"])

        self.assertFalse(crawler_class.call_args.kwargs["download_html"])

    def test_disabling_all_content_types_is_reported_before_crawl(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            contextlib.redirect_stderr(io.StringIO()) as error_output,
        ):
            with self.assertRaises(SystemExit) as exit_info:
                cli.main(["--config", "--no-html", "--no-images", "--no-documents"])

        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("réactivez au moins un type de contenu", error_output.getvalue())
        database_class.assert_not_called()

    def test_no_documents_flag_disables_document_downloads(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = []
            database.ensure_start_url_queued.return_value = 0
            database.summary.return_value = {
                "pages": 0,
                "images": 0,
                "documents": 0,
            }
            crawler_class.crawl_many.return_value = [{"pages": []}]

            cli.main(["--config", "--no-documents"])

        self.assertFalse(crawler_class.call_args.kwargs["download_documents"])

    def test_export_excel_does_not_start_a_crawl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://example.org"],
                "max_pages": 25,
                "workers": 2,
                "delay": 0,
                "timeout": 5,
                "retries": 1,
                "max_response_bytes": 1024,
                "database_file": "crawl.db",
                "images_dir": "img",
                "log_file": "crawler.jsonl",
            }), encoding="utf-8")

            with (
                patch("cli.export_database_to_excel", return_value={
                    "pages": 10,
                    "images": 5,
                    "documents": 2,
                }) as export,
                patch("cli.Crawler") as crawler_class,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                cli.main([
                    "--config", str(config_path),
                    "--export-excel", "export.xlsx",
                ])

        export.assert_called_once_with(
            config_path.parent / "crawl_example-org.db",
            config_path.parent / "export.xlsx",
        )
        crawler_class.assert_not_called()
        self.assertIn("10 pages, 5 images, 2 documents", output.getvalue())

    def test_positional_urls_select_a_database_named_for_their_host(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl_configured.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = []
            database.ensure_start_url_queued.return_value = 0
            database.summary.return_value = {
                "pages": 0,
                "images": 0,
                "documents": 0,
            }
            crawler_class.crawl_many.return_value = [{"pages": []}]

            cli.main(["https://finance.gov.bf/"])

        database_class.assert_called_once_with(Path("crawl_finance.db"))
        crawler_class.crawl_many.assert_called_once()

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
                database.queued_start_urls.return_value = []
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
                    "--refresh",
                ])

        self.assertEqual(len(crawler_class.call_args_list), 2)
        for call in crawler_class.call_args_list:
            self.assertEqual(call.kwargs["max_pages"], 12)
            self.assertEqual(call.kwargs["workers"], 3)
            self.assertEqual(call.kwargs["retries"], 0)
            self.assertTrue(call.kwargs["refresh"])

    def test_explicit_start_urls_are_used_instead_of_other_queued_sites(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://configured.example/"],
                "max_pages": 25,
                "workers": 2,
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
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                database = database_class.return_value.__enter__.return_value
                database.queued_start_urls.return_value = []
                database.summary.return_value = {
                    "pages": 0,
                    "images": 0,
                    "documents": 0,
                }
                crawler_class.crawl_many.return_value = [{"pages": []}]

                cli.main([
                    "--config", str(config_path),
                    "https://argument.example/",
                ])

        self.assertEqual(
            [call.args[0] for call in crawler_class.call_args_list],
            ["https://argument.example/"],
        )
        self.assertEqual(output.getvalue().count("Crawl terminé"), 1)

    def test_uses_configured_start_urls_when_no_positional_urls_are_given(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://configured.example/"],
                "max_pages": 25,
                "workers": 2,
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
                database.queued_start_urls.return_value = []
                database.summary.return_value = {
                    "pages": 0,
                    "images": 0,
                    "documents": 0,
                }
                crawler_class.crawl_many.return_value = [{"pages": []}]

                cli.main(["--config", str(config_path)])

        crawler_class.assert_called_once()
        self.assertEqual(
            crawler_class.call_args.args[0],
            "https://configured.example/",
        )

    def test_retry_failed_flag_explicitly_requeues_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://configured.example/"],
                "max_pages": 25,
                "workers": 2,
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
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                database = database_class.return_value.__enter__.return_value
                database.retry_failed_urls.return_value = 2
                database.queued_start_urls.return_value = []
                database.summary.return_value = {
                    "pages": 0,
                    "images": 0,
                    "documents": 0,
                }
                crawler_class.crawl_many.return_value = [{"pages": []}]

                cli.main(["--config", str(config_path), "--retry-failed"])

        database.retry_failed_urls.assert_called_once_with([
            "https://configured.example/",
        ])
        crawler_class.assert_called_once()
        self.assertEqual(
            crawler_class.call_args.args[0],
            "https://configured.example/",
        )
        self.assertIn("2 URL en échec remises", output.getvalue())

    def test_no_config_resumes_pending_sites(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = ["https://pending.example/"]
            database.summary.return_value = {
                "pages": 0,
                "images": 0,
                "documents": 0,
            }
            crawler_class.crawl_many.return_value = [{"pages": []}]

            cli.main([])

        crawler_class.assert_called_once()
        self.assertEqual(crawler_class.call_args.args[0], "https://pending.example/")
        self.assertIn("Reprise de 1 site(s)", output.getvalue())

    def test_no_config_and_empty_queue_requests_explicit_config(self):
        config = {
            "start_urls": ["https://configured.example/"],
            "max_pages": 25,
            "workers": 2,
            "delay": 0,
            "timeout": 5,
            "retries": 1,
            "max_response_bytes": 1024,
            "database_path": Path("crawl.db"),
            "images_path": Path("img"),
            "documents_path": Path("documents"),
            "log_path": Path("crawler.jsonl"),
            "crawler_name": "Test",
            "database_flush_interval": 30,
            "image_batch_size": 1,
        }
        with (
            patch("cli.load_config", return_value=config),
            patch("cli.configure_logging", return_value=[]),
            patch("cli.CrawlDatabase") as database_class,
            patch("cli.Crawler") as crawler_class,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            database = database_class.return_value.__enter__.return_value
            database.queued_start_urls.return_value = []

            cli.main([])

        crawler_class.assert_not_called()
        self.assertIn("python cli.py --config config.yaml", output.getvalue())

    def test_config_uses_only_pending_urls_for_configured_sites(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": [
                    "https://configured.example/",
                    "https://another.example/",
                ],
                "max_pages": 25,
                "workers": 2,
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
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                database = database_class.return_value.__enter__.return_value
                database.queued_start_urls.side_effect = [
                    ["https://another.example/"],
                    ["https://another.example/", "https://configured.example/"],
                ]
                database.ensure_start_url_queued.return_value = 1
                database.summary.return_value = {
                    "pages": 0,
                    "images": 0,
                    "documents": 0,
                }
                crawler_class.crawl_many.return_value = [
                    {"pages": []},
                    {"pages": []},
                ]

                cli.main(["--config", str(config_path)])

        self.assertEqual(
            [call.args[0] for call in crawler_class.call_args_list],
            [
                "https://another.example/",
                "https://configured.example/",
            ],
        )
        self.assertEqual(
            database.queued_start_urls.call_args_list[0].args,
            (["https://configured.example/", "https://another.example/"],),
        )
        self.assertEqual(
            database.ensure_start_url_queued.call_args_list[0].args,
            ("https://configured.example/",),
        )
        self.assertEqual(
            database.ensure_start_url_queued.call_args_list[1].args,
            ("https://another.example/",),
        )
        self.assertIn("Priorité aux URL en attente", output.getvalue())

    def test_config_starts_from_yaml_when_only_unconfigured_sites_are_queued(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "start_urls": ["https://configured.example/"],
                "max_pages": 25,
                "workers": 2,
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
                database.queued_start_urls.return_value = []
                database.ensure_start_url_queued.return_value = 0
                database.summary.return_value = {
                    "pages": 0,
                    "images": 0,
                    "documents": 0,
                }
                crawler_class.crawl_many.return_value = [{"pages": []}]

                cli.main(["--config", str(config_path)])

        crawler_class.assert_called_once()
        self.assertEqual(
            crawler_class.call_args.args[0],
            "https://configured.example/",
        )
        self.assertEqual(
            database.queued_start_urls.call_args_list[0].args,
            (["https://configured.example/"],),
        )

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
