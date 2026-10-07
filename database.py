import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class CrawlDatabase:
    def __init__(self, path):
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(database_path, timeout=30, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._create_tables()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE url_queue SET status='queued' WHERE status='fetching'"
            )
            self._connection.execute(
                "UPDATE url_queue SET status='queued', error=NULL WHERE status='failed'"
            )
            self._connection.execute(
                "UPDATE images SET status='queued' WHERE status='downloading'"
            )
            self._connection.execute("""
                UPDATE url_queue
                SET status='done', error=NULL
                WHERE EXISTS (
                    SELECT 1 FROM pages
                    WHERE pages.start_url=url_queue.start_url
                      AND pages.url=url_queue.url
                ) OR EXISTS (
                    SELECT 1 FROM documents
                    WHERE documents.start_url=url_queue.start_url
                      AND documents.url=url_queue.url
                )
            """)

    def close(self):
        with self._lock:
            self._connection.close()

    def _create_tables(self):
        with self._lock, self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS url_queue (
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT,
                    PRIMARY KEY (start_url, url)
                );
                CREATE INDEX IF NOT EXISTS url_queue_pending
                    ON url_queue(start_url, status);
                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT,
                    content TEXT,
                    html TEXT,
                    crawler_name TEXT NOT NULL DEFAULT 'Yennenga Crawl',
                    links_json TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    crawled_at TEXT NOT NULL,
                    UNIQUE (start_url, url),
                    UNIQUE (start_url, url_hash)
                );
                CREATE TABLE IF NOT EXISTS images (
                    url TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    local_path TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS documents (
                    start_url TEXT NOT NULL,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    PRIMARY KEY (start_url, url),
                    UNIQUE (start_url, url_hash)
                );
            """)
            self._migrate_pages()

    def _migrate_pages(self):
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(pages)")
        }
        required_columns = {
            "id", "url_hash", "published_at", "content", "html", "crawler_name",
        }
        if required_columns.issubset(columns):
            return

        if {
            "id", "published_at", "content", "html", "crawler_name",
        }.issubset(columns):
            self._connection.execute("ALTER TABLE pages ADD COLUMN url_hash TEXT")
            rows = self._connection.execute(
                "SELECT start_url, url FROM pages"
            ).fetchall()
            self._connection.executemany(
                "UPDATE pages SET url_hash=? WHERE start_url=? AND url=?",
                (
                    (self.page_url_hash(url), start_url, url)
                    for start_url, url in rows
                ),
            )
            self._connection.execute("""
                CREATE UNIQUE INDEX pages_start_url_hash
                ON pages(start_url, url_hash)
            """)
            return

        self._connection.execute("""
            CREATE TABLE pages_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_url TEXT NOT NULL,
                url TEXT NOT NULL,
                url_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                published_at TEXT,
                content TEXT,
                html TEXT,
                crawler_name TEXT NOT NULL DEFAULT 'Yennenga Crawl',
                links_json TEXT NOT NULL,
                images_json TEXT NOT NULL,
                crawled_at TEXT NOT NULL,
                UNIQUE (start_url, url),
                UNIQUE (start_url, url_hash)
            )
        """)
        old_rows = self._connection.execute("SELECT * FROM pages").fetchall()
        old_column_names = [
            row[1] for row in self._connection.execute("PRAGMA table_info(pages)")
        ]
        for old_row in old_rows:
            old = dict(zip(old_column_names, old_row))
            self._connection.execute(
                """INSERT INTO pages_new
                   (start_url, url, url_hash, title, published_at, content, html,
                    crawler_name, links_json, images_json, crawled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    old["start_url"],
                    old["url"],
                    self.page_url_hash(old["url"]),
                    old["title"],
                    old.get("published_at"),
                    old.get("content"),
                    old.get("html"),
                    old.get("crawler_name", "Yennenga Crawl"),
                    old["links_json"],
                    old["images_json"],
                    old["crawled_at"],
                ),
            )
        self._connection.execute("DROP TABLE pages")
        self._connection.execute("ALTER TABLE pages_new RENAME TO pages")

    @staticmethod
    def page_url_hash(url):
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def enqueue(self, start_url, urls):
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT OR IGNORE INTO url_queue(start_url, url) VALUES (?, ?)",
                ((start_url, url) for url in urls),
            )

    def claim_next(self, start_url):
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT queue.url FROM url_queue AS queue
                   WHERE queue.start_url=? AND queue.status='queued'
                     AND NOT EXISTS (
                         SELECT 1 FROM pages
                         WHERE pages.start_url=queue.start_url
                           AND pages.url=queue.url
                     ) AND NOT EXISTS (
                         SELECT 1 FROM documents
                         WHERE documents.start_url=queue.start_url
                           AND documents.url=queue.url
                     )
                   ORDER BY queue.rowid LIMIT 1""",
                (start_url,),
            ).fetchone()
            if row is None:
                return None
            url = row[0]
            self._connection.execute(
                "UPDATE url_queue SET status='fetching', error=NULL WHERE start_url=? AND url=?",
                (start_url, url),
            )
            return url

    def save_page(self, start_url, page, discovered_urls):
        self.save_pages(start_url, [(page, discovered_urls)])

    def save_pages(self, start_url, page_results):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            for page, discovered_urls in page_results:
                self._connection.execute(
                    """INSERT INTO pages
                       (start_url, url, url_hash, title, published_at, content, html, crawler_name,
                        links_json, images_json, crawled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(start_url, url) DO UPDATE SET
                        title=excluded.title,
                        url_hash=excluded.url_hash,
                        published_at=excluded.published_at,
                        content=excluded.content,
                        html=excluded.html,
                        crawler_name=excluded.crawler_name,
                        links_json=excluded.links_json,
                        images_json=excluded.images_json,
                        crawled_at=excluded.crawled_at""",
                    (
                        start_url,
                        page["url"],
                        self.page_url_hash(page["url"]),
                        page["title"],
                        page.get("published_at"),
                        page.get("content"),
                        page.get("html"),
                        page.get("crawler_name", "Yennenga Crawl"),
                        json.dumps(page["links"], ensure_ascii=False),
                        json.dumps(page["images"], ensure_ascii=False),
                        now,
                    ),
                )
                row = self._connection.execute(
                    "SELECT id, crawled_at FROM pages WHERE start_url=? AND url=?",
                    (start_url, page["url"]),
                ).fetchone()
                page["id"], page["crawled_at"] = row
                self._connection.execute(
                    "UPDATE url_queue SET status='done', error=NULL WHERE start_url=? AND url=?",
                    (start_url, page["url"]),
                )
                self._connection.executemany(
                    "INSERT OR IGNORE INTO url_queue(start_url, url) VALUES (?, ?)",
                    ((start_url, url) for url in discovered_urls),
                )

    def save_document(self, start_url, document):
        downloaded_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO documents
                   (start_url, url, url_hash, site_name, local_path, filename,
                    content_type, size_bytes, downloaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(start_url, url) DO UPDATE SET
                    url_hash=excluded.url_hash,
                    site_name=excluded.site_name,
                    local_path=excluded.local_path,
                    filename=excluded.filename,
                    content_type=excluded.content_type,
                    size_bytes=excluded.size_bytes,
                    downloaded_at=excluded.downloaded_at""",
                (
                    start_url,
                    document["url"],
                    document["url_hash"],
                    document["site_name"],
                    document["local_path"],
                    document["filename"],
                    document["content_type"],
                    document["size_bytes"],
                    downloaded_at,
                ),
            )
            self._connection.execute(
                "UPDATE url_queue SET status='done', error=NULL WHERE start_url=? AND url=?",
                (start_url, document["url"]),
            )

    def has_document(self, start_url, url):
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM documents WHERE start_url=? AND url=?",
                (start_url, url),
            ).fetchone()
        return row is not None

    def set_url_status(self, start_url, url, status, error=None):
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE url_queue SET status=?, error=? WHERE start_url=? AND url=?",
                (status, error, start_url, url),
            )

    def page_count(self, start_url):
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM pages WHERE start_url=?",
                (start_url,),
            ).fetchone()
        return row[0]

    def queued_url_count(self, start_url, url):
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM url_queue WHERE start_url=? AND url=?",
                (start_url, url),
            ).fetchone()
        return row[0]

    def claim_image(self, url):
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status, local_path FROM images WHERE url=?",
                (url,),
            ).fetchone()
            if row:
                if row[0] == "downloading":
                    return False
                if row[0] == "downloaded" and row[1] and Path(row[1]).is_file():
                    return False
            self._connection.execute(
                """INSERT INTO images(url, status) VALUES (?, 'downloading')
                   ON CONFLICT(url) DO UPDATE SET status='downloading', error=NULL""",
                (url,),
            )
            return True

    def finish_image(self, url, local_path):
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE images SET status='downloaded', local_path=?, error=NULL WHERE url=?",
                (str(local_path), url),
            )

    def image_path(self, url):
        with self._lock:
            row = self._connection.execute(
                "SELECT local_path FROM images WHERE url=? AND status='downloaded'",
                (url,),
            ).fetchone()
        if row is None:
            return None
        path = Path(row[0])
        return path if path.is_file() else None

    def fail_image(self, url, error):
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE images SET status='failed', error=? WHERE url=?",
                (str(error), url),
            )

    def summary(self):
        with self._lock:
            counts = dict(self._connection.execute(
                "SELECT status, COUNT(*) FROM url_queue GROUP BY status"
            ).fetchall())
            page_count = self._connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            image_count = self._connection.execute(
                "SELECT COUNT(*) FROM images WHERE status='downloaded'"
            ).fetchone()[0]
            document_count = self._connection.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
        return {
            "pages": page_count,
            "images": image_count,
            "documents": document_count,
            "queue": counts,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
