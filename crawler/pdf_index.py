import logging
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)
SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
INDEX_BATCH_SIZE = 25


def build_pdf_index(source_database, output_database):
    source_database = Path(source_database).resolve()
    output_database = Path(output_database).resolve()
    if source_database == output_database:
        raise ValueError("La base d’index doit être différente de la base source.")
    if not source_database.is_file():
        raise FileNotFoundError(f"Base source introuvable : {source_database}")
    if output_database.exists():
        raise FileExistsError(f"La base d’index existe déjà : {output_database}")

    output_database.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_database.name}-",
            suffix=".tmp",
            dir=output_database.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        source = sqlite3.connect(
            f"{source_database.as_uri()}?mode=ro",
            uri=True,
            timeout=30,
        )
        index = sqlite3.connect(temporary_path, timeout=30)
        try:
            _create_index_schema(index)
            source.execute("BEGIN")
            cursor = source.execute("""
                SELECT start_url, url, site_name, local_path, filename,
                       content_type, size_bytes, downloaded_at
                FROM documents
                WHERE lower(content_type)='application/pdf'
                   OR lower(filename) LIKE '%.pdf'
                ORDER BY site_name, filename
            """)
            indexed = 0
            searchable = 0
            failed = 0
            rows = []
            for document in cursor:
                record = _extract_pdf(document)
                rows.append(record)
                indexed += 1
                searchable += record[12] == "indexed"
                failed += record[12] == "error"
                if len(rows) >= INDEX_BATCH_SIZE:
                    _insert_records(index, rows)
                    rows.clear()
            if rows:
                _insert_records(index, rows)
            index.commit()
            index.execute("INSERT INTO pdf_search(pdf_search) VALUES ('optimize')")
            index.commit()
        finally:
            index.close()
            source.close()

        os.replace(temporary_path, output_database)
        temporary_path = None
        counts = {
            "indexed": indexed,
            "searchable": searchable,
            "failed": failed,
            "output": str(output_database),
        }
        logger.info("PDF index built: %s", counts)
        return counts
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _create_index_schema(connection):
    connection.executescript("""
        PRAGMA journal_mode=DELETE;
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            start_url TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            site_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            page_count INTEGER NOT NULL,
            extracted_text TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT
        );
        CREATE VIRTUAL TABLE pdf_search USING fts5(
            title,
            author,
            site_name,
            extracted_text,
            url UNINDEXED,
            local_path UNINDEXED,
            content='documents',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
            INSERT INTO pdf_search(
                rowid, title, author, site_name, extracted_text, url, local_path
            ) VALUES (
                new.id, new.title, new.author, new.site_name, new.extracted_text,
                new.url, new.local_path
            );
        END;
    """)


def _extract_pdf(document):
    (
        start_url,
        url,
        site_name,
        local_path,
        filename,
        content_type,
        size_bytes,
        downloaded_at,
    ) = document
    title = Path(filename).stem
    author = None
    page_count = 0
    extracted_text = ""
    status = "indexed"
    error_message = None
    path = Path(local_path)

    try:
        if not path.is_file():
            raise FileNotFoundError(f"Fichier PDF introuvable : {path}")
        reader = PdfReader(path, strict=False)
        metadata = reader.metadata
        if metadata:
            title = _metadata_text(metadata.get("/Title")) or title
            author = _metadata_text(metadata.get("/Author"))
        page_count = len(reader.pages)
        text_parts = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text()
            if text:
                text_parts.append(text)
            if page_number % 100 == 0:
                logger.info("Extracted %d pages from %s", page_number, filename)
        extracted_text = "\n\n".join(text_parts).strip()
        if not extracted_text:
            status = "no_text"
    except (PdfReadError, OSError, ValueError) as error:
        status = "error"
        error_message = str(error)
        logger.warning("PDF extraction failed for %s: %s", path, error)

    return (
        start_url,
        url,
        site_name,
        str(path),
        filename,
        content_type,
        size_bytes,
        downloaded_at,
        title,
        author,
        page_count,
        extracted_text,
        status,
        error_message,
    )


def _metadata_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _insert_records(connection, records):
    connection.executemany(
        """INSERT INTO documents
           (start_url, url, site_name, local_path, filename, content_type,
            size_bytes, downloaded_at, title, author, page_count,
            extracted_text, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        records,
    )


def search_pdf_index(index_database, query, limit=20):
    index_database = Path(index_database).resolve()
    if not index_database.is_file():
        raise FileNotFoundError(f"Index PDF introuvable : {index_database}")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("La limite de résultats doit être un entier positif.")

    tokens = SEARCH_TOKEN.findall(query)
    if not tokens:
        raise ValueError("Saisis au moins un mot à rechercher.")
    match_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
    connection = sqlite3.connect(
        f"{index_database.as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )
    try:
        rows = connection.execute(
            """SELECT documents.title, documents.site_name, documents.url,
                      documents.local_path, documents.page_count,
                      documents.status,
                      snippet(pdf_search, 3, '[', ']', ' … ', 14)
               FROM pdf_search
               JOIN documents ON documents.id=pdf_search.rowid
               WHERE pdf_search MATCH ?
               ORDER BY bm25(pdf_search), documents.title
               LIMIT ?""",
            (match_query, limit),
        ).fetchall()
    finally:
        connection.close()
    return rows
