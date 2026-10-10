import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from .pdf_index import SEARCH_TOKEN

RIGHTS_STATUS = "not_assessed"
RIGHTS_NOTICE = (
    "Les conditions de réutilisation ne sont pas encore évaluées. "
    "Consultez la source institutionnelle."
)
MAX_DETAIL_TEXT = 100_000


def list_sites(pages_database, pdf_database):
    pages_database = _require_database(pages_database, "base de pages")
    pdf_database = _require_database(pdf_database, "index PDF")
    pages_connection = _connect_readonly(pages_database)
    pdf_connection = _connect_readonly(pdf_database)
    try:
        sites = {}
        for start_url, page_count, latest, body_bytes in pages_connection.execute("""
            SELECT start_url, COUNT(*), MAX(crawled_at),
                   SUM(length(cleaned_text))
            FROM pages GROUP BY start_url ORDER BY start_url
        """):
            host = urlsplit(start_url).hostname or start_url
            sites[host] = {
                "name": host,
                "source_url": start_url,
                "pages": page_count,
                "documents": 0,
                "last_collected": latest,
                "text_bytes": body_bytes or 0,
                "rights_status": RIGHTS_STATUS,
                "rights_notice": RIGHTS_NOTICE,
            }
        for host, document_count in pdf_connection.execute("""
            SELECT site_name, COUNT(*) FROM documents GROUP BY site_name
        """):
            site = sites.setdefault(host, {
                "name": host,
                "source_url": f"https://{host}/",
                "pages": 0,
                "documents": 0,
                "last_collected": None,
                "text_bytes": 0,
                "rights_status": RIGHTS_STATUS,
                "rights_notice": RIGHTS_NOTICE,
            })
            site["documents"] = document_count
        return sorted(sites.values(), key=lambda site: site["name"])
    finally:
        pages_connection.close()
        pdf_connection.close()


def list_pages(database, query=None, site=None, limit=20, offset=0):
    database = _require_database(database, "base de pages")
    terms = _terms(query)
    filters = []
    parameters = []
    if site:
        filters.append("start_url = ?")
        parameters.append(_site_start_url(site))
    for term in terms:
        filters.append("(title LIKE ? ESCAPE '\\' OR cleaned_text LIKE ? ESCAPE '\\')")
        escaped = _escape_like(term)
        parameters.extend((f"%{escaped}%", f"%{escaped}%"))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    connection = _connect_readonly(database)
    try:
        total = connection.execute(
            f"SELECT COUNT(*) FROM pages{where}",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT id, start_url, url, title, published_at, crawled_at,
                       substr(cleaned_text, 1, 700)
                FROM pages{where}
                ORDER BY COALESCE(published_at, crawled_at) DESC, id DESC
                LIMIT ? OFFSET ?""",
            (*parameters, limit, offset),
        ).fetchall()
    finally:
        connection.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "rights_status": RIGHTS_STATUS,
        "rights_notice": RIGHTS_NOTICE,
        "items": [
            {
                "id": row[0],
                "site": urlsplit(row[1]).hostname or row[1],
                "source_url": row[2],
                "title": row[3],
                "published_at": row[4],
                "collected_at": row[5],
                "excerpt": row[6] or "",
                "rights_status": RIGHTS_STATUS,
            }
            for row in rows
        ],
    }


def get_page(database, page_id):
    database = _require_database(database, "base de pages")
    connection = _connect_readonly(database)
    try:
        row = connection.execute(
            """SELECT id, start_url, url, title, published_at, crawled_at, cleaned_text
               FROM pages WHERE id=?""",
            (page_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    text = row[6] or ""
    return {
        "id": row[0],
        "site": urlsplit(row[1]).hostname or row[1],
        "source_url": row[2],
        "title": row[3],
        "published_at": row[4],
        "collected_at": row[5],
        "text": text[:MAX_DETAIL_TEXT],
        "text_truncated": len(text) > MAX_DETAIL_TEXT,
        "rights_status": RIGHTS_STATUS,
        "rights_notice": RIGHTS_NOTICE,
    }


def list_documents(database, query=None, site=None, limit=20, offset=0):
    database = _require_database(database, "index PDF")
    terms = _terms(query)
    filters = []
    parameters = []
    match_query = None
    if terms:
        match_query = " AND ".join(f'"{term}"' for term in terms)
        filters.append("pdf_search MATCH ?")
        parameters.append(match_query)
    if site:
        filters.append("documents.site_name = ?")
        parameters.append(_site_host(site))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    connection = _connect_readonly(database)
    try:
        total = connection.execute(
            f"""SELECT COUNT(*) FROM documents
                JOIN pdf_search ON documents.id=pdf_search.rowid{where}""",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT documents.id, documents.site_name, documents.url,
                       documents.filename, documents.title, documents.author,
                       documents.page_count, documents.size_bytes,
                       documents.downloaded_at, documents.status,
                       CASE WHEN ? IS NULL THEN ''
                            ELSE snippet(pdf_search, 3, '[', ']', ' … ', 14)
                       END
                FROM documents
                JOIN pdf_search ON documents.id=pdf_search.rowid{where}
                ORDER BY
                    CASE WHEN ? IS NOT NULL THEN bm25(pdf_search) ELSE 0 END,
                    documents.downloaded_at DESC, documents.title
                LIMIT ? OFFSET ?""",
            (match_query, *parameters, match_query, limit, offset),
        ).fetchall()
    finally:
        connection.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "rights_status": RIGHTS_STATUS,
        "rights_notice": RIGHTS_NOTICE,
        "items": [
            {
                "id": row[0],
                "site": row[1],
                "source_url": row[2],
                "filename": row[3],
                "title": row[4],
                "author": row[5],
                "pages": row[6],
                "size_bytes": row[7],
                "collected_at": row[8],
                "status": row[9],
                "excerpt": row[10] or "",
                "rights_status": RIGHTS_STATUS,
            }
            for row in rows
        ],
    }


def get_document(database, document_id):
    database = _require_database(database, "index PDF")
    connection = _connect_readonly(database)
    try:
        row = connection.execute(
            """SELECT id, site_name, url, filename, title, author, page_count,
                      size_bytes, downloaded_at, status, extracted_text
               FROM documents WHERE id=?""",
            (document_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    text = row[10] or ""
    return {
        "id": row[0],
        "site": row[1],
        "source_url": row[2],
        "filename": row[3],
        "title": row[4],
        "author": row[5],
        "pages": row[6],
        "size_bytes": row[7],
        "collected_at": row[8],
        "status": row[9],
        "text": text[:MAX_DETAIL_TEXT],
        "text_truncated": len(text) > MAX_DETAIL_TEXT,
        "rights_status": RIGHTS_STATUS,
        "rights_notice": RIGHTS_NOTICE,
    }


def _require_database(path, description):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{description} introuvable : {path}")
    return path


def _connect_readonly(path):
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=15)


def _terms(query):
    if query is None:
        return []
    query = query.strip()
    if not query:
        return []
    if len(query) > 200:
        raise ValueError("La recherche est limitée à 200 caractères.")
    terms = SEARCH_TOKEN.findall(query)
    if not terms:
        raise ValueError("La recherche doit contenir au moins un mot.")
    if len(terms) > 10:
        raise ValueError("La recherche est limitée à 10 mots.")
    return terms


def _site_start_url(site):
    host = _site_host(site)
    return f"https://{host}/"


def _site_host(site):
    site = site.strip()
    parsed = urlsplit(site if "://" in site else f"https://{site}")
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Le filtre site doit être un nom de domaine ou une URL HTTPS.")
    return parsed.hostname.lower()


def _escape_like(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
