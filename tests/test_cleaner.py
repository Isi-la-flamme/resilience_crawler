import sqlite3
import tempfile
import unittest
from pathlib import Path

from crawler.cleaner import clean_database
from crawler.database import CrawlDatabase


class CleanerTests(unittest.TestCase):
    def test_removes_head_known_noise_and_blocks_repeated_on_eighty_percent(self):
        common_block = (
            "<div class='site-menu'>"
            + ("Menu partagé du site avec des informations répétées. " * 3)
            + "</div>"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "crawl.db"
            output_path = Path(temp_dir) / "crawl-clean.db"
            start_url = "https://example.org/"

            with CrawlDatabase(source_path) as database:
                for index in range(5):
                    repeated = common_block if index < 4 else common_block.replace(
                        "Menu partagé du site",
                        "Menu spécifique de cette page",
                    ).replace("site-menu", "different-menu")
                    database.save_page(start_url, {
                        "url": f"https://example.org/page-{index}",
                        "title": f"Page {index}",
                        "published_at": None,
                        "content": f"Article unique numéro {index}",
                        "html": (
                            "<html><head><title>À supprimer</title>"
                            "<script>tracking()</script></head><body>"
                            "<header>En-tête inutile</header>"
                            f"{repeated}"
                            f"<main><article><p>Article unique numéro {index}</p>"
                            "</article></main>"
                            "<footer>Pied de page inutile</footer></body></html>"
                        ),
                        "crawler_name": "Test",
                        "links": [],
                        "images": [],
                    }, [])

            counts = clean_database(source_path, output_path)
            cleaned = sqlite3.connect(output_path)
            try:
                cleaned_text, title = cleaned.execute(
                    """SELECT cleaned_text, title FROM pages
                       WHERE url='https://example.org/page-0'"""
                ).fetchone()
                unique_page_body = cleaned.execute(
                    """SELECT cleaned_text FROM pages
                       WHERE url='https://example.org/page-4'"""
                ).fetchone()[0]
                report = cleaned.execute(
                    """SELECT source_pages, cleaned_pages, repeated_blocks,
                              removed_blocks, source_html_bytes, cleaned_text_bytes
                       FROM site_report WHERE start_url=?""",
                    (start_url,),
                ).fetchone()
                repeated_examples = cleaned.execute(
                    "SELECT tag, occurrences, required_pages FROM boilerplate_blocks"
                ).fetchall()
            finally:
                cleaned.close()

        self.assertEqual(counts, {
            "pages": 5,
            "boilerplate_blocks": 1,
            "site_report": 1,
        })
        self.assertNotRegex(cleaned_text, r"<[^>]+>")
        self.assertNotIn("tracking", cleaned_text)
        self.assertNotIn("site-menu", cleaned_text)
        self.assertNotIn("En-tête inutile", cleaned_text)
        self.assertNotIn("Pied de page inutile", cleaned_text)
        self.assertIn("Article unique numéro 0", cleaned_text)
        self.assertNotIn("different-menu", unique_page_body)
        self.assertEqual(unique_page_body, "Article unique numéro 4")
        self.assertEqual(title, "Page 0")
        self.assertEqual(report[:3], (5, 5, 1))
        self.assertEqual(report[3], 4)
        self.assertGreater(report[4], report[5])
        self.assertEqual(repeated_examples, [("div", 4, 4)])

    def test_refuses_to_overwrite_source_or_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "crawl.db"
            output_path = Path(temp_dir) / "clean.db"
            connection = sqlite3.connect(source_path)
            connection.executescript("""
                CREATE TABLE pages (
                    id INTEGER PRIMARY KEY, start_url TEXT, url TEXT, url_hash TEXT,
                    title TEXT, published_at TEXT, content TEXT, html TEXT,
                    crawler_name TEXT, links_json TEXT, images_json TEXT, crawled_at TEXT
                );
            """)
            connection.close()

            with self.assertRaisesRegex(ValueError, "différente"):
                clean_database(source_path, source_path)

            output_path.touch()
            with self.assertRaises(FileExistsError):
                clean_database(source_path, output_path)


if __name__ == "__main__":
    unittest.main()
