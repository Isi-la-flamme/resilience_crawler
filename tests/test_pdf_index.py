import sqlite3
import tempfile
import unittest
from pathlib import Path

from crawler.pdf_index import build_pdf_index, search_pdf_index


def make_text_pdf():
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length 58 >>\nstream\n"
            b"BT /F1 12 Tf 72 720 Td (Budget national Burkina) Tj ET\n"
            b"endstream"
        ),
        (
            b"<< /Title <FEFF0052006100700070006F007200740020004200750064006700650074"
            b"0020004E006100740069006F006E0061006C>"
            b" /Author <FEFF004D0069006E00690073007400E8007200650020004500780065006D0070006C0065> >>"
        ),
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


class PdfIndexTests(unittest.TestCase):
    def test_indexes_pdf_metadata_and_searches_by_keyword(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_database = root / "crawl.db"
            index_database = root / "pdf-index.db"
            pdf_path = root / "rapport.pdf"
            pdf_path.write_bytes(make_text_pdf())

            source = sqlite3.connect(source_database)
            source.executescript("""
                CREATE TABLE documents (
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    downloaded_at TEXT NOT NULL
                );
            """)
            source.execute(
                """INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "https://example.org/",
                    "https://example.org/rapport.pdf",
                    "example.org",
                    str(pdf_path),
                    "rapport.pdf",
                    "application/pdf",
                    pdf_path.stat().st_size,
                    "2026-10-09T12:00:00+00:00",
                ),
            )
            source.commit()
            source.close()

            counts = build_pdf_index(source_database, index_database)
            results = search_pdf_index(index_database, "budget national")
            connection = sqlite3.connect(index_database)
            try:
                document = connection.execute(
                    """SELECT title, author, page_count, status, extracted_text
                       FROM documents"""
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(counts["indexed"], 1)
        self.assertEqual(counts["searchable"], 1)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(document, (
            "Rapport Budget National",
            "Ministère Exemple",
            1,
            "indexed",
            "Budget national Burkina",
        ))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "Rapport Budget National")
        self.assertEqual(results[0][1], "example.org")
        self.assertEqual(results[0][2], "https://example.org/rapport.pdf")

    def test_records_invalid_pdf_and_refuses_existing_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_database = root / "crawl.db"
            index_database = root / "pdf-index.db"
            invalid_pdf = root / "invalid.pdf"
            invalid_pdf.write_bytes(b"not a PDF")
            source = sqlite3.connect(source_database)
            source.executescript("""
                CREATE TABLE documents (
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    downloaded_at TEXT NOT NULL
                );
            """)
            source.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "https://example.org/",
                    "https://example.org/invalid.pdf",
                    "example.org",
                    str(invalid_pdf),
                    "invalid.pdf",
                    "application/pdf",
                    invalid_pdf.stat().st_size,
                    "2026-10-09T12:00:00+00:00",
                ),
            )
            source.commit()
            source.close()

            counts = build_pdf_index(source_database, index_database)

            with self.assertRaises(FileExistsError):
                build_pdf_index(source_database, index_database)
            connection = sqlite3.connect(index_database)
            try:
                row = connection.execute(
                    "SELECT status, error FROM documents"
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(counts["failed"], 1)
        self.assertEqual(row[0], "error")
        self.assertTrue(row[1])

    def test_search_requires_meaningful_terms_and_positive_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_database = Path(temp_dir) / "index.db"
            connection = sqlite3.connect(index_database)
            connection.executescript("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY, title TEXT, author TEXT, site_name TEXT,
                    extracted_text TEXT, url TEXT, local_path TEXT
                );
                CREATE VIRTUAL TABLE pdf_search USING fts5(
                    title, author, site_name, extracted_text, url UNINDEXED,
                    local_path UNINDEXED
                );
            """)
            connection.close()

            with self.assertRaisesRegex(ValueError, "au moins un mot"):
                search_pdf_index(index_database, "!!!")
            with self.assertRaisesRegex(ValueError, "entier positif"):
                search_pdf_index(index_database, "budget", 0)


if __name__ == "__main__":
    unittest.main()
