import hashlib
import html
import logging
import math
import os
import re
import sqlite3
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from .parser import PageParser

logger = logging.getLogger(__name__)

REPEAT_THRESHOLD = 0.8
MIN_REPEATED_PAGES = 3
BLOCK_TEXT_MIN_LENGTH = 80
BLOCK_TEXT_MAX_LENGTH = 10000
BLOCK_SAMPLE_LENGTH = 300
MIN_CLEANED_TEXT_LENGTH = 500
PROCESS_BATCH_SIZE = 100
EXCLUDED_TAGS = {
    "head", "script", "style", "noscript", "svg", "nav", "header",
    "footer", "aside", "form", "template",
}
CANDIDATE_TAGS = {"div", "section", "ul", "ol", "table"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
TEXT_WHITESPACE = re.compile(r"\s+")


def clean_database(source_path, output_path):
    source_path = Path(source_path).resolve()
    output_path = Path(output_path).resolve()
    if source_path == output_path:
        raise ValueError("La base nettoyée doit être différente de la base source.")
    if not source_path.is_file():
        raise FileNotFoundError(f"Base source introuvable : {source_path}")
    if output_path.exists():
        raise FileExistsError(f"La base de sortie existe déjà : {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}-",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        source = sqlite3.connect(
            f"{source_path.as_uri()}?mode=ro",
            uri=True,
            timeout=30,
        )
        target = sqlite3.connect(temporary_path, timeout=30)
        try:
            _create_output_schema(target)
            source.execute("BEGIN")
            site_page_counts = dict(source.execute(
                "SELECT start_url, COUNT(*) FROM pages GROUP BY start_url"
            ).fetchall())
            _detect_repeated_blocks(source, target)
            repeated_blocks = _load_repeated_blocks(target)
            _write_site_reports(target, site_page_counts)
            _clean_pages(source, target, repeated_blocks)
            target.commit()
        finally:
            target.close()
            source.close()

        os.replace(temporary_path, output_path)
        temporary_path = None
        connection = sqlite3.connect(output_path)
        try:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in ("pages", "boilerplate_blocks", "site_report")
            }
        finally:
            connection.close()
        logger.info("Clean database created at %s: %s", output_path, counts)
        return counts
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _create_output_schema(connection):
    connection.executescript("""
        PRAGMA journal_mode=DELETE;
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
        CREATE INDEX pages_by_site ON pages(start_url);
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
        CREATE TABLE block_occurrences (
            start_url TEXT NOT NULL,
            url TEXT NOT NULL,
            signature TEXT NOT NULL,
            tag TEXT NOT NULL,
            sample_text TEXT NOT NULL,
            PRIMARY KEY (start_url, url, signature)
        );
    """)


def _detect_repeated_blocks(source, target):
    cursor = source.execute(
        "SELECT start_url, url, html FROM pages ORDER BY start_url, url"
    )
    processed = 0
    with target:
        for start_url, url, source_html in cursor:
            _, candidates, _ = BodyHTMLCleaner().clean(source_html or "")
            unique_candidates = {
                candidate["signature"]: candidate
                for candidate in candidates
            }
            target.executemany(
                """INSERT OR IGNORE INTO block_occurrences
                   (start_url, url, signature, tag, sample_text)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    (
                        start_url,
                        url,
                        candidate["signature"],
                        candidate["tag"],
                        candidate["sample_text"],
                    )
                    for candidate in unique_candidates.values()
                ),
            )
            processed += 1
            if processed % PROCESS_BATCH_SIZE == 0:
                target.commit()
            if processed % 1000 == 0:
                logger.info(
                    "Cleaner detected repeated-block candidates in %d pages",
                    processed,
                )

    site_counts = dict(source.execute(
        "SELECT start_url, COUNT(*) FROM pages GROUP BY start_url"
    ).fetchall())
    for start_url, page_count in site_counts.items():
        required_pages = max(
            MIN_REPEATED_PAGES,
            math.ceil(REPEAT_THRESHOLD * page_count),
        )
        with target:
            target.execute(
                """INSERT INTO boilerplate_blocks
                   (start_url, signature, tag, occurrences, required_pages, sample_text)
                   SELECT ?, signature, tag, COUNT(*), ?, MIN(sample_text)
                   FROM block_occurrences
                   WHERE start_url=?
                   GROUP BY signature, tag
                   HAVING COUNT(*) >= ?""",
                (start_url, required_pages, start_url, required_pages),
            )
    with target:
        target.execute("DROP TABLE block_occurrences")


def _load_repeated_blocks(connection):
    blocks = {}
    for start_url, signature in connection.execute(
        "SELECT start_url, signature FROM boilerplate_blocks"
    ):
        blocks.setdefault(start_url, set()).add(signature)
    return blocks


def _clean_pages(source, target, repeated_blocks):
    cursor = source.execute("""
        SELECT id, start_url, url, url_hash, title, published_at, content,
               html, crawler_name, links_json, images_json, crawled_at
        FROM pages
        ORDER BY start_url, url
    """)
    processed = 0
    report_totals = {}
    with target:
        for (
            page_id,
            start_url,
            url,
            url_hash,
            title,
            published_at,
            content,
            source_html,
            crawler_name,
            links_json,
            images_json,
            crawled_at,
        ) in cursor:
            cleaned_body_html, _, removed_count = BodyHTMLCleaner(
                repeated_blocks.get(start_url, set())
            ).clean(source_html or "")
            cleaned_text = PageParser.parse_html(cleaned_body_html)[4]
            include_page = len(cleaned_text) >= MIN_CLEANED_TEXT_LENGTH
            if include_page:
                target.execute(
                    """INSERT INTO pages
                       (id, start_url, url, url_hash, title, published_at, content,
                        cleaned_text, crawler_name, links_json, images_json, crawled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        page_id,
                        start_url,
                        url,
                        url_hash,
                        title,
                        published_at,
                        content,
                        cleaned_text,
                        crawler_name,
                        links_json,
                        images_json,
                        crawled_at,
                    ),
                )
            page_totals = report_totals.setdefault(start_url, [0, 0, 0, 0])
            page_totals[0] += include_page
            page_totals[1] += len((source_html or "").encode("utf-8"))
            if include_page:
                page_totals[2] += len(cleaned_text.encode("utf-8"))
            page_totals[3] += removed_count
            processed += 1
            if processed % PROCESS_BATCH_SIZE == 0:
                _flush_site_report_totals(target, report_totals)
                target.commit()
            if processed % 1000 == 0:
                logger.info("Cleaner saved plain text for %d pages", processed)
        _flush_site_report_totals(target, report_totals)


def _flush_site_report_totals(target, report_totals):
    target.executemany(
        """UPDATE site_report
           SET cleaned_pages=cleaned_pages+?,
               source_html_bytes=source_html_bytes+?,
               cleaned_text_bytes=cleaned_text_bytes+?,
               removed_blocks=removed_blocks+?
           WHERE start_url=?""",
        (
            (pages, source_bytes, cleaned_bytes, removed_blocks, start_url)
            for start_url, (pages, source_bytes, cleaned_bytes, removed_blocks)
            in report_totals.items()
        ),
    )
    report_totals.clear()


def _write_site_reports(target, site_page_counts):
    block_counts = dict(target.execute(
        "SELECT start_url, COUNT(*) FROM boilerplate_blocks GROUP BY start_url"
    ).fetchall())
    with target:
        target.executemany(
            """INSERT INTO site_report(start_url, source_pages, repeated_blocks)
               VALUES (?, ?, ?)""",
            (
                (start_url, page_count, block_counts.get(start_url, 0))
                for start_url, page_count in site_page_counts.items()
            ),
        )


class BodyHTMLCleaner(HTMLParser):
    def __init__(self, repeated_signatures=()):
        super().__init__(convert_charrefs=True)
        self.repeated_signatures = repeated_signatures
        self._excluded_stack = []
        self._candidate_stack = []
        self._output = []
        self._candidates = []
        self._removed_blocks = 0

    def clean(self, html_text):
        self.feed(html_text)
        self.close()
        while self._candidate_stack:
            self._finish_candidate()
        return "".join(self._output), self._candidates, self._removed_blocks

    def handle_starttag(self, tag, _attrs):
        if self._excluded_stack:
            if tag not in VOID_TAGS:
                self._excluded_stack.append(tag)
            return
        if tag in EXCLUDED_TAGS:
            if tag not in VOID_TAGS:
                self._excluded_stack.append(tag)
            return

        if tag in CANDIDATE_TAGS:
            self._candidate_stack.append({
                "tag": tag,
                "markup": [],
                "text": [],
                "text_length": 0,
                "too_long": False,
                "removed_children": 0,
            })
        if tag not in {"html", "body"}:
            self._emit(self.get_starttag_text() or f"<{tag}>")

    def handle_endtag(self, tag):
        if self._excluded_stack:
            matching_index = next(
                (
                    index
                    for index in range(len(self._excluded_stack) - 1, -1, -1)
                    if self._excluded_stack[index] == tag
                ),
                None,
            )
            if matching_index is not None:
                del self._excluded_stack[matching_index:]
            return
        if tag in EXCLUDED_TAGS:
            return
        if tag not in {"html", "body"}:
            self._emit(f"</{tag}>")

        if tag in CANDIDATE_TAGS:
            matching_index = next(
                (
                    index
                    for index in range(len(self._candidate_stack) - 1, -1, -1)
                    if self._candidate_stack[index]["tag"] == tag
                ),
                None,
            )
            if matching_index is not None:
                while len(self._candidate_stack) > matching_index:
                    self._finish_candidate()

    def handle_data(self, data):
        if self._excluded_stack:
            return
        for candidate in self._candidate_stack:
            if candidate["too_long"]:
                continue
            remaining = BLOCK_TEXT_MAX_LENGTH + 1 - candidate["text_length"]
            candidate["text"].append(data[:remaining])
            candidate["text_length"] += min(len(data), remaining)
            if candidate["text_length"] > BLOCK_TEXT_MAX_LENGTH:
                candidate["too_long"] = True
                candidate["text"].clear()
        self._emit(html.escape(data, quote=False))

    def handle_entityref(self, name):
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name):
        self.handle_data(html.unescape(f"&#{name};"))

    def handle_comment(self, _data):
        return

    def _emit(self, markup):
        if self._candidate_stack:
            self._candidate_stack[-1]["markup"].append(markup)
        else:
            self._output.append(markup)

    def _finish_candidate(self):
        candidate = self._candidate_stack.pop()
        text = TEXT_WHITESPACE.sub(" ", "".join(candidate["text"])).strip()
        signature = None
        if (
            not candidate["too_long"]
            and len(text) >= BLOCK_TEXT_MIN_LENGTH
        ):
            signature = hashlib.sha256(
                f"{candidate['tag']}\0{text.casefold()}".encode("utf-8")
            ).hexdigest()
            self._candidates.append({
                "tag": candidate["tag"],
                "signature": signature,
                "sample_text": text[:BLOCK_SAMPLE_LENGTH],
            })

        if signature and signature in self.repeated_signatures:
            removed_children = 1
        else:
            markup = "".join(candidate["markup"])
            if self._candidate_stack:
                self._candidate_stack[-1]["markup"].append(markup)
                self._candidate_stack[-1]["removed_children"] += (
                    candidate["removed_children"]
                )
            else:
                self._output.append(markup)
                self._removed_blocks += candidate["removed_children"]
            removed_children = candidate["removed_children"]

        if signature and signature in self.repeated_signatures:
            if self._candidate_stack:
                self._candidate_stack[-1]["removed_children"] += removed_children
            else:
                self._removed_blocks += removed_children
