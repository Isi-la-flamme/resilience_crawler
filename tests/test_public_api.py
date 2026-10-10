import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import create_app


class PublicApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.pages_database = root / "pages.db"
        self.pdf_database = root / "pdf.db"

        pages = sqlite3.connect(self.pages_database)
        pages.executescript("""
            CREATE TABLE pages (
                id INTEGER PRIMARY KEY,
                start_url TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                published_at TEXT,
                crawled_at TEXT NOT NULL,
                cleaned_text TEXT NOT NULL
            );
            INSERT INTO pages VALUES
                (1, 'https://example.org/', 'https://example.org/budget',
                 'Budget 2026', '2026-01-01', '2026-01-02',
                 'Le budget public pour le développement.'),
                (2, 'https://example.org/', 'https://example.org/recruitment',
                 'Recrutement public', NULL, '2026-01-03',
                 'Information sur le recrutement dans la fonction publique.');
        """)
        pages.close()

        documents = sqlite3.connect(self.pdf_database)
        documents.executescript("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                site_name TEXT NOT NULL,
                url TEXT NOT NULL,
                filename TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT,
                page_count INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                downloaded_at TEXT NOT NULL,
                status TEXT NOT NULL,
                extracted_text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE pdf_search USING fts5(
                title, author, site_name, extracted_text, url UNINDEXED,
                local_path UNINDEXED
            );
            INSERT INTO documents VALUES
                (1, 'example.org', 'https://example.org/budget.pdf',
                 'budget.pdf', 'Budget national', 'Ministère', 10, 1024,
                 '2026-01-04', 'indexed', 'Budget national du Burkina Faso'),
                (2, 'example.org', 'https://example.org/scan.pdf',
                 'scan.pdf', 'Document scanné', NULL, 2, 2048,
                 '2026-01-05', 'no_text', '');
            INSERT INTO pdf_search(rowid,title,author,site_name,extracted_text,url,local_path)
                SELECT id,title,author,site_name,extracted_text,url,''
                FROM documents;
        """)
        documents.close()

        self.client = TestClient(create_app(self.pages_database, self.pdf_database))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_dashboard_and_health_expose_prototype_notice(self):
        dashboard = self.client.get("/")
        health = self.client.get("/api/v1/health")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("lang=\"fr\"", dashboard.text)
        self.assertIn("conditions de", dashboard.text)
        self.assertIn("réutilisation", dashboard.text)
        self.assertIn("appendHighlighted", dashboard.text)
        self.assertIn("item.source_url || item.url", dashboard.text)
        self.assertIn("Lire le texte intégral", dashboard.text)
        self.assertIn("fetch(`/api/v1/${detailKind}/${item.id}`)", dashboard.text)
        self.assertNotIn('link.target = "_blank"', dashboard.text)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(health.json()["rights_status"], "not_assessed")

    def test_sites_pages_search_pagination_and_rights_notice(self):
        sites = self.client.get("/api/v1/sites")
        pages = self.client.get("/api/v1/pages", params={
            "q": "budget public",
            "site": "example.org",
            "limit": 1,
        })

        self.assertEqual(sites.status_code, 200)
        self.assertEqual(sites.json()["items"][0]["pages"], 2)
        self.assertEqual(sites.json()["items"][0]["documents"], 2)
        self.assertEqual(sites.json()["items"][0]["rights_status"], "not_assessed")
        self.assertEqual(pages.json()["total"], 1)
        self.assertEqual(pages.json()["items"][0]["title"], "Budget 2026")
        self.assertEqual(pages.json()["search_fields"], ["title", "text"])
        self.assertEqual(pages.json()["matched_terms"], ["budget", "public"])
        self.assertIn("budget public", pages.json()["items"][0]["excerpt"])
        self.assertEqual(
            pages.json()["items"][0]["source_url"],
            "https://example.org/budget",
        )
        self.assertEqual(pages.json()["items"][0]["rights_status"], "not_assessed")

    def test_page_search_excerpt_is_centered_on_the_match(self):
        pages = sqlite3.connect(self.pages_database)
        pages.execute(
            "UPDATE pages SET cleaned_text=? WHERE id=1",
            (
                "Début du rapport. " * 100
                + "Conclusion avec mot-clef distinctif. "
                + "Fin du rapport. " * 100,
            ),
        )
        pages.commit()
        pages.close()

        response = self.client.get(
            "/api/v1/pages",
            params={"q": "budget distinctif"},
        )
        result = response.json()["items"][0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("distinctif", result["excerpt"])
        self.assertTrue(result["excerpt"].startswith("… "))
        self.assertTrue(result["excerpt"].endswith(" …"))

    def test_page_detail_and_invalid_ids(self):
        page = self.client.get("/api/v1/pages/1")
        missing = self.client.get("/api/v1/pages/999")
        invalid_search = self.client.get("/api/v1/pages", params={"q": "!!!"})
        invalid_site = self.client.get(
            "/api/v1/pages",
            params={"site": "http://example.org"},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn("budget public", page.json()["text"])
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(invalid_search.status_code, 422)
        self.assertEqual(invalid_site.status_code, 422)

    def test_page_and_document_details_return_full_text_from_database(self):
        expected_page_text = "Texte intégral " * 8_000
        expected_pdf_text = "PDF intégral " * 8_000
        pages = sqlite3.connect(self.pages_database)
        pages.execute(
            "UPDATE pages SET cleaned_text=? WHERE id=1",
            (expected_page_text,),
        )
        pages.commit()
        pages.close()

        documents = sqlite3.connect(self.pdf_database)
        documents.execute(
            "UPDATE documents SET extracted_text=? WHERE id=1",
            (expected_pdf_text,),
        )
        documents.commit()
        documents.close()

        page = self.client.get("/api/v1/pages/1").json()
        document = self.client.get("/api/v1/documents/1").json()

        self.assertEqual(page["text"], expected_page_text)
        self.assertFalse(page["text_truncated"])
        self.assertEqual(document["text"], expected_pdf_text)
        self.assertFalse(document["text_truncated"])

    def test_pricing_and_token_urls_are_hidden_from_api(self):
        pages = sqlite3.connect(self.pages_database)
        pages.executemany(
            """INSERT INTO pages
               (id, start_url, url, title, published_at, crawled_at, cleaned_text)
               VALUES (?, 'https://example.org/', ?, ?, NULL, '2026-01-06', ?)""",
            (
                (
                    3,
                    "https://example.org/category/page/4/pricing//pricing",
                    "Pricing page",
                    "A pricing page.",
                ),
                (
                    4,
                    "https://example.org/nested/token/",
                    "Token page",
                    "A token page.",
                ),
            ),
        )
        pages.commit()
        pages.close()

        documents = sqlite3.connect(self.pdf_database)
        hidden_documents = [
            (
                3,
                "https://example.org/pricing/",
                "pricing.pdf",
                "Pricing PDF",
            ),
            (
                4,
                "https://example.org/nested/token",
                "token.pdf",
                "Token PDF",
            ),
        ]
        documents.executemany(
            """INSERT INTO documents
               (id, site_name, url, filename, title, author, page_count,
                size_bytes, downloaded_at, status, extracted_text)
               VALUES (?, 'example.org', ?, ?, ?, NULL, 1, 100, '2026-01-06',
                       'indexed', 'Excluded document text')""",
            hidden_documents,
        )
        documents.executemany(
            """INSERT INTO pdf_search
               (rowid, title, author, site_name, extracted_text, url, local_path)
               VALUES (?, ?, NULL, 'example.org', 'Excluded document text', ?, '')""",
            (
                (doc_id, title, url)
                for doc_id, url, _, title in hidden_documents
            ),
        )
        documents.commit()
        documents.close()

        pages_response = self.client.get("/api/v1/pages")
        hidden_page = self.client.get("/api/v1/pages/3")
        documents_response = self.client.get("/api/v1/documents")
        hidden_document = self.client.get("/api/v1/documents/3")
        sites = self.client.get("/api/v1/sites").json()["items"][0]

        self.assertEqual(pages_response.json()["total"], 2)
        self.assertTrue(all(
            not item["source_url"].rstrip("/").endswith(("pricing", "token"))
            for item in pages_response.json()["items"]
        ))
        self.assertEqual(hidden_page.status_code, 404)
        self.assertEqual(documents_response.json()["total"], 2)
        self.assertTrue(all(
            not item["source_url"].rstrip("/").endswith(("pricing", "token"))
            for item in documents_response.json()["items"]
        ))
        self.assertEqual(hidden_document.status_code, 404)
        self.assertEqual(sites["pages"], 2)
        self.assertEqual(sites["documents"], 2)

    def test_pdf_search_returns_source_and_keeps_non_text_documents_listed(self):
        search = self.client.get(
            "/api/v1/documents",
            params={"q": "budget national", "site": "example.org"},
        )
        all_documents = self.client.get("/api/v1/documents")
        document = self.client.get("/api/v1/documents/1")
        missing = self.client.get("/api/v1/documents/999")

        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["total"], 1)
        result = search.json()["items"][0]
        self.assertEqual(result["title"], "Budget national")
        self.assertEqual(result["source_url"], "https://example.org/budget.pdf")
        self.assertEqual(
            search.json()["search_fields"],
            ["title", "author", "site", "text"],
        )
        self.assertEqual(search.json()["matched_terms"], ["budget", "national"])
        self.assertIn("Budget national", result["excerpt"])
        self.assertNotIn("\ue000", result["excerpt"])
        self.assertNotIn("\ue001", result["excerpt"])
        self.assertEqual(result["rights_status"], "not_assessed")
        self.assertEqual(all_documents.json()["total"], 2)
        self.assertEqual(
            {item["status"] for item in all_documents.json()["items"]},
            {"indexed", "no_text"},
        )
        self.assertIn("Budget national du Burkina", document.json()["text"])
        self.assertEqual(missing.status_code, 404)

    def test_enforces_pagination_bounds(self):
        response = self.client.get("/api/v1/pages", params={"limit": 1000})

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
