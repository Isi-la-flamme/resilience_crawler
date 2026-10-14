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
                        "content": (
                            f"Article unique numéro {index}. "
                            + "Texte éditorial exploitable. " * 25
                        ),
                        "html": (
                            "<html><head><title>À supprimer</title>"
                            "<script>tracking()</script></head><body>"
                            "<header>En-tête inutile</header>"
                            f"{repeated}"
                            f"<main><article><p>Article unique numéro {index}. "
                            + "Texte éditorial exploitable. " * 25
                            + "</p>"
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
        self.assertIn("Article unique numéro 4", unique_page_body)
        self.assertGreaterEqual(len(unique_page_body), 500)
        self.assertEqual(title, "Page 0")
        self.assertEqual(report[:3], (5, 5, 1))
        self.assertEqual(report[3], 4)
        self.assertGreater(report[4], report[5])
        self.assertEqual(repeated_examples, [("div", 4, 4)])

    def test_excludes_cleaned_text_shorter_than_500_characters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "crawl.db"
            output_path = Path(temp_dir) / "crawl-clean.db"
            start_url = "https://example.org/"
            with CrawlDatabase(source_path) as database:
                for page_name, text in (
                    ("short", "x" * 499),
                    ("boundary", "x" * 500),
                ):
                    database.save_page(start_url, {
                        "url": f"https://example.org/{page_name}",
                        "title": page_name,
                        "published_at": None,
                        "content": text,
                        "html": f"<html><body><p>{text}</p></body></html>",
                        "crawler_name": "Test",
                        "links": [],
                        "images": [],
                    }, [])

            counts = clean_database(source_path, output_path)
            source = sqlite3.connect(source_path)
            cleaned = sqlite3.connect(output_path)
            try:
                source_page_count = source.execute(
                    "SELECT COUNT(*) FROM pages"
                ).fetchone()[0]
                cleaned_urls = cleaned.execute(
                    "SELECT url FROM pages ORDER BY url"
                ).fetchall()
                site_counts = cleaned.execute(
                    """SELECT source_pages, cleaned_pages FROM site_report
                       WHERE start_url=?""",
                    (start_url,),
                ).fetchone()
            finally:
                source.close()
                cleaned.close()

        self.assertEqual(counts["pages"], 1)
        self.assertEqual(source_page_count, 2)
        self.assertEqual(
            cleaned_urls,
            [("https://example.org/boundary",)],
        )
        self.assertEqual(site_counts, (2, 1))

    def test_persists_page_metadata_and_heading_outline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "crawl.db"
            output_path = Path(temp_dir) / "crawl-clean.db"
            start_url = "https://example.org/"
            page_url = "https://example.org/informations/actualites/article"
            page_html = (
                '<html lang="fr-BF"><head>'
                '<meta name="description" content="Description institutionnelle">'
                '<meta name="keywords" content="budget, santé; budget">'
                '</head><body><nav><h2>Navigation</h2></nav>'
                '<main><article><h1>Budget <em>national</em></h1>'
                '<h2>Résultats annuels</h2><p>'
                + "Contenu éditorial utile. " * 100
                + "</p></article></main></body></html>"
            )
            with CrawlDatabase(source_path) as database:
                database.save_page(start_url, {
                    "url": page_url,
                    "title": "Budget national",
                    "published_at": "2026-01-01",
                    "content": "Contenu éditorial utile. " * 100,
                    "html": page_html,
                    "crawler_name": "Test",
                    "links": [],
                    "images": [],
                }, [])

            clean_database(source_path, output_path)
            cleaned = sqlite3.connect(output_path)
            try:
                result = cleaned.execute(
                    """SELECT page_type, language, meta_description, meta_keywords_json,
                              headings_json, word_count, cleaned_text
                       FROM pages WHERE url=?""",
                    (page_url,),
                ).fetchone()
            finally:
                cleaned.close()

        page_type, language, description, keywords, headings, word_count, text = result
        self.assertEqual(page_type, "article")
        self.assertEqual(language, "fr-bf")
        self.assertEqual(description, "Description institutionnelle")
        self.assertEqual(keywords, '["budget", "santé"]')
        self.assertEqual(
            headings,
            '[{"level": 1, "text": "Budget national"}, '
            '{"level": 2, "text": "Résultats annuels"}]',
        )
        self.assertEqual(word_count, len(text.split()))
        self.assertNotIn("Navigation", headings)

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
