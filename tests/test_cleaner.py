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

    def test_appends_new_pages_without_replacing_existing_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "crawl.db"
            output_path = Path(temp_dir) / "clean.db"
            start_url = "https://example.org/"
            original_text = "Texte initial à conserver. " * 30
            with CrawlDatabase(source_path) as database:
                database.save_page(start_url, {
                    "url": f"{start_url}existing",
                    "title": "Page existante",
                    "content": original_text,
                    "html": f"<html><body><p>{original_text}</p></body></html>",
                    "crawler_name": "Test",
                    "links": [],
                    "images": [],
                }, [])
            clean_database(source_path, output_path)

            with self.assertRaisesRegex(ValueError, "différente"):
                clean_database(source_path, source_path)

            with CrawlDatabase(source_path) as database:
                changed_text = "Texte modifié ne devant pas remplacer l'existant. " * 20
                database.save_page(start_url, {
                    "url": f"{start_url}existing",
                    "title": "Titre modifié",
                    "content": changed_text,
                    "html": f"<html><body><p>{changed_text}</p></body></html>",
                    "crawler_name": "Test",
                    "links": [],
                    "images": [],
                }, [])
                new_text = "Nouvelle page ajoutée à la base nettoyée. " * 20
                database.save_page(start_url, {
                    "url": f"{start_url}new",
                    "title": "Nouvelle page",
                    "content": new_text,
                    "html": f"<html><body><p>{new_text}</p></body></html>",
                    "crawler_name": "Test",
                    "links": [],
                    "images": [],
                }, [])

            first_counts = clean_database(source_path, output_path)
            second_counts = clean_database(source_path, output_path)
            cleaned = sqlite3.connect(output_path)
            try:
                pages = cleaned.execute(
                    "SELECT url,title,cleaned_text FROM pages ORDER BY url"
                ).fetchall()
                report = cleaned.execute(
                    "SELECT source_pages,cleaned_pages FROM site_report WHERE start_url=?",
                    (start_url,),
                ).fetchone()
            finally:
                cleaned.close()

        self.assertEqual(first_counts["pages"], 2)
        self.assertEqual(second_counts["pages"], 2)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][1], "Page existante")
        self.assertEqual(pages[0][2], original_text.strip())
        self.assertEqual(pages[1][1], "Nouvelle page")
        self.assertEqual(pages[1][2], new_text.strip())
        self.assertEqual(report, (2, 2))

    def test_appending_migrates_legacy_clean_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "crawl.db"
            output_path = root / "clean.db"
            start_url = "https://example.org/"
            old_text = "Ancienne page déjà préparée. " * 25
            legacy = sqlite3.connect(output_path)
            legacy.executescript("""
                CREATE TABLE pages (
                    id INTEGER PRIMARY KEY,
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT,
                    content TEXT,
                    cleaned_text TEXT NOT NULL,
                    crawler_name TEXT NOT NULL,
                    links_json TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    crawled_at TEXT NOT NULL,
                    UNIQUE (start_url, url)
                );
                CREATE TABLE boilerplate_blocks (
                    start_url TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    occurrences INTEGER NOT NULL,
                    required_pages INTEGER NOT NULL,
                    sample_text TEXT NOT NULL,
                    PRIMARY KEY (start_url, signature)
                );
                CREATE TABLE site_report (
                    start_url TEXT PRIMARY KEY,
                    source_pages INTEGER NOT NULL,
                    cleaned_pages INTEGER NOT NULL DEFAULT 0,
                    repeated_blocks INTEGER NOT NULL DEFAULT 0,
                    removed_blocks INTEGER NOT NULL DEFAULT 0,
                    source_html_bytes INTEGER NOT NULL DEFAULT 0,
                    cleaned_text_bytes INTEGER NOT NULL DEFAULT 0
                );
            """)
            legacy.execute(
                """INSERT INTO pages
                   VALUES (1, ?, ?, 'legacy-hash', 'Ancienne actualité', NULL, NULL,
                           ?, 'Test', '[]', '[]', '2026-01-01')""",
                (
                    start_url,
                    f"{start_url}informations/actualites/old",
                    old_text,
                ),
            )
            legacy.execute(
                "INSERT INTO site_report(start_url,source_pages,cleaned_pages) VALUES(?,1,1)",
                (start_url,),
            )
            legacy.commit()
            legacy.close()

            new_text = "Contenu éditorial de la nouvelle page. " * 25
            with CrawlDatabase(source_path) as database:
                database.save_page(start_url, {
                    "url": f"{start_url}informations/actualites/new",
                    "title": "Nouvelle actualité",
                    "content": new_text,
                    "html": (
                        '<html lang="fr"><body><article><h1>Nouvelle actualité</h1>'
                        f"<p>{new_text}</p></article></body></html>"
                    ),
                    "crawler_name": "Test",
                    "links": [],
                    "images": [],
                }, [])

            clean_database(source_path, output_path)
            cleaned = sqlite3.connect(output_path)
            try:
                rows = cleaned.execute(
                    "SELECT url,page_type,word_count FROM pages ORDER BY url"
                ).fetchall()
                columns = {
                    row[1] for row in cleaned.execute("PRAGMA table_info(pages)")
                }
            finally:
                cleaned.close()

        self.assertEqual(len(rows), 2)
        self.assertIn("page_type", columns)
        self.assertIn("headings_json", columns)
        self.assertEqual(rows[0][1], "article")
        self.assertGreater(rows[0][2], 0)
        self.assertEqual(rows[1][1], "article")


if __name__ == "__main__":
    unittest.main()
