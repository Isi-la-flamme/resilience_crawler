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
        self.assertEqual(pages.json()["items"][0]["rights_status"], "not_assessed")

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
