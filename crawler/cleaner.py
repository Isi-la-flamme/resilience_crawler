"""Nettoyage hors ligne du HTML collecté et production d'une base texte."""

import hashlib
import html
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

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
    """Ajoute les pages nettoyées à une base de texte sans modifier la source.

    Le traitement identifie les blocs récurrents par site, retire les éléments
    de navigation et boilerplate, puis conserve uniquement les pages dont le
    texte final contient au moins `MIN_CLEANED_TEXT_LENGTH` caractères. La
    sortie est créée si elle n'existe pas ; sinon les pages absentes y sont
    ajoutées, sans remplacer les URL déjà présentes. Une nouvelle exécution
    sur la même source est donc idempotente.

    Args:
        source_path: Base crawler contenant la table `pages` et le HTML original.
        output_path: Chemin SQLite de la base nettoyée.

    Returns:
        Compteurs de pages nettoyées, blocs répétitifs et rapports par site.

    Raises:
        ValueError: Si la source et la sortie désignent le même fichier.
        FileNotFoundError: Si la base source n'existe pas.
        sqlite3.Error: Si la base source ne respecte pas le schéma attendu.
    """
    source_path = Path(source_path).resolve()
    output_path = Path(output_path).resolve()
    if source_path == output_path:
        raise ValueError("La base nettoyée doit être différente de la base source.")
    if not source_path.is_file():
        raise FileNotFoundError(f"Base source introuvable : {source_path}")
    if output_path.exists():
        return _append_clean_database(source_path, output_path)

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


def _append_clean_database(source_path, output_path):
    """Nettoie la source dans une base temporaire puis fusionne sans écraser."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    incoming_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}-incoming-",
            suffix=".db",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            incoming_path = Path(temporary_file.name)
        incoming_path.unlink()

        clean_database(source_path, incoming_path)
        target = sqlite3.connect(output_path, timeout=30, uri=True)
        try:
            target.execute(
                "ATTACH DATABASE ? AS incoming",
                (f"{incoming_path.as_uri()}?mode=ro",),
            )
            target.execute("BEGIN IMMEDIATE")
            _migrate_output_schema(target)
            added_by_site = dict(target.execute("""
                SELECT incoming_pages.start_url, COUNT(*)
                FROM incoming.pages AS incoming_pages
                WHERE NOT EXISTS (
                    SELECT 1 FROM main.pages AS existing_pages
                    WHERE existing_pages.start_url=incoming_pages.start_url
                      AND existing_pages.url=incoming_pages.url
                )
                GROUP BY incoming_pages.start_url
            """).fetchall())
            target.execute("""
                INSERT OR IGNORE INTO main.pages
                    (start_url, url, url_hash, title, published_at, content,
                     cleaned_text, page_type, language, meta_description,
                     meta_keywords_json, headings_json, word_count, crawler_name,
                     links_json, images_json, crawled_at)
                SELECT start_url, url, url_hash, title, published_at, content,
                       cleaned_text, page_type, language, meta_description,
                       meta_keywords_json, headings_json, word_count, crawler_name,
                       links_json, images_json, crawled_at
                FROM incoming.pages
            """)
            target.execute("""
                INSERT INTO main.boilerplate_blocks
                    (start_url, signature, tag, occurrences, required_pages, sample_text)
                SELECT start_url, signature, tag, occurrences, required_pages, sample_text
                FROM incoming.boilerplate_blocks
                WHERE 1
                ON CONFLICT(start_url, signature) DO UPDATE SET
                    occurrences=MAX(boilerplate_blocks.occurrences, excluded.occurrences),
                    required_pages=MAX(
                        boilerplate_blocks.required_pages,
                        excluded.required_pages
                    )
            """)
            incoming_reports = target.execute("""
                SELECT start_url, source_pages, removed_blocks, source_html_bytes
                FROM incoming.site_report
            """).fetchall()
            for start_url, source_pages, removed_blocks, source_html_bytes in incoming_reports:
                existing_source_pages = target.execute(
                    "SELECT source_pages FROM main.site_report WHERE start_url=?",
                    (start_url,),
                ).fetchone()
                merged_source_pages = (
                    source_pages
                    if existing_source_pages is None
                    or source_pages > existing_source_pages[0]
                    else existing_source_pages[0] + added_by_site.get(start_url, 0)
                )
                target.execute("""
                    INSERT INTO main.site_report
                        (start_url, source_pages, cleaned_pages, repeated_blocks,
                         removed_blocks, source_html_bytes, cleaned_text_bytes)
                    VALUES (
                        ?, ?, 0, 0, ?, ?, 0
                    )
                    ON CONFLICT(start_url) DO UPDATE SET
                        source_pages=MAX(site_report.source_pages, excluded.source_pages),
                        removed_blocks=MAX(
                            site_report.removed_blocks,
                            excluded.removed_blocks
                        ),
                        source_html_bytes=MAX(
                            site_report.source_html_bytes,
                            excluded.source_html_bytes
                        )
                """, (
                    start_url,
                    merged_source_pages,
                    removed_blocks,
                    source_html_bytes,
                ))
                target.execute("""
                    UPDATE main.site_report
                    SET cleaned_pages=(
                            SELECT COUNT(*) FROM main.pages
                            WHERE pages.start_url=?
                        ),
                        repeated_blocks=(
                            SELECT COUNT(*) FROM main.boilerplate_blocks
                            WHERE boilerplate_blocks.start_url=?
                        ),
                        cleaned_text_bytes=COALESCE((
                            SELECT SUM(length(CAST(cleaned_text AS BLOB)))
                            FROM main.pages WHERE pages.start_url=?
                        ), 0)
                    WHERE start_url=?
                """, (start_url, start_url, start_url, start_url))
            target.commit()
        except Exception:
            target.rollback()
            raise
        finally:
            target.close()

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
        logger.info("Clean database updated at %s: %s", output_path, counts)
        return counts
    finally:
        if incoming_path is not None:
            incoming_path.unlink(missing_ok=True)


def _migrate_output_schema(connection):
    """Ajoute les colonnes enrichies aux bases nettoyées plus anciennes."""
    columns = {
        row[1] for row in connection.execute("PRAGMA main.table_info(pages)")
    }
    migrations = (
        ("page_type", "TEXT NOT NULL DEFAULT 'page'"),
        ("language", "TEXT"),
        ("meta_description", "TEXT"),
        ("meta_keywords_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("headings_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("word_count", "INTEGER NOT NULL DEFAULT 0"),
    )
    for column, declaration in migrations:
        if column not in columns:
            connection.execute(
                f"ALTER TABLE main.pages ADD COLUMN {column} {declaration}"
            )

    for page_id, url, text in connection.execute(
        "SELECT id, url, cleaned_text FROM main.pages WHERE word_count=0"
    ):
        word_count = len(re.findall(r"[^\W_]+", text or "", flags=re.UNICODE))
        connection.execute(
            "UPDATE main.pages SET page_type=?, word_count=? WHERE id=?",
            (_classify_page(url), word_count, page_id),
        )


def _create_output_schema(connection):
    """Crée les tables de pages nettoyées, de boilerplate et de rapports."""
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
            page_type TEXT NOT NULL,
            language TEXT,
            meta_description TEXT,
            meta_keywords_json TEXT NOT NULL,
            headings_json TEXT NOT NULL,
            word_count INTEGER NOT NULL,
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
    """Repère les blocs HTML qui se répètent suffisamment sur chaque site.

    Le HTML est analysé avec `BodyHTMLCleaner`; une empreinte normalisée
    identifie chaque bloc candidat. Un bloc est retenu s'il apparaît sur au
    moins `max(MIN_REPEATED_PAGES, ceil(REPEAT_THRESHOLD * pages_du_site))`
    pages.
    """
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
    """Charge les signatures répétitives sous forme `{site: set(signatures)}`."""
    blocks = {}
    for start_url, signature in connection.execute(
        "SELECT start_url, signature FROM boilerplate_blocks"
    ):
        blocks.setdefault(start_url, set()).add(signature)
    return blocks


def _clean_pages(source, target, repeated_blocks):
    """Nettoie les pages une par une et remplit la table `pages` de sortie.

    Les pages sous le seuil de texte restent absentes de la table, mais sont
    tout de même comptabilisées dans les statistiques du site. Les mises à jour
    des rapports sont vidées par lots afin de limiter la mémoire.
    """
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
                metadata = PageMetadataExtractor.extract(
                    source_html or "",
                    cleaned_body_html,
                )
                target.execute(
                    """INSERT INTO pages
                       (id, start_url, url, url_hash, title, published_at, content,
                        cleaned_text, page_type, language, meta_description,
                        meta_keywords_json, headings_json, word_count, crawler_name,
                        links_json, images_json, crawled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        page_id,
                        start_url,
                        url,
                        url_hash,
                        title,
                        published_at,
                        content,
                        cleaned_text,
                        _classify_page(url),
                        metadata["language"],
                        metadata["description"],
                        json.dumps(metadata["keywords"], ensure_ascii=False),
                        json.dumps(metadata["headings"], ensure_ascii=False),
                        len(re.findall(r"[^\W_]+", cleaned_text, flags=re.UNICODE)),
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
    """Ajoute au rapport les agrégats temporaires puis vide leur dictionnaire."""
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
    """Initialise une ligne de rapport par site avec ses pages source et blocs."""
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


def _classify_page(url):
    """Classe la page à partir de segments explicites de son URL."""
    segments = {
        segment.casefold()
        for segment in urlsplit(url).path.split("/")
        if segment
    }
    if "conseils-des-ministres" in segments:
        return "meeting_report"
    if segments.intersection({"actualites", "articles"}):
        return "article"
    if segments.intersection({"evenements", "evenements-1", "snc", "fespaco"}):
        return "event"
    if segments.intersection({"ressources", "documents", "mediatheque"}):
        return "resource"
    if segments.intersection({"ministere", "ministre"}):
        return "institution"
    return "page"


class PageMetadataExtractor(HTMLParser):
    """Extrait les métadonnées publiées et les titres structurants d'une page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.language = None
        self.description = None
        self.keywords = []
        self.headings = []
        self._active_heading = None

    @classmethod
    def extract(cls, source_html, cleaned_html):
        """Retourne métadonnées éditoriales et titres du HTML nettoyé."""
        source_parser = cls()
        source_parser.feed(source_html)
        source_parser.close()

        heading_parser = cls()
        heading_parser.feed(cleaned_html)
        heading_parser.close()
        return {
            "language": source_parser.language,
            "description": source_parser.description,
            "keywords": source_parser.keywords,
            "headings": heading_parser.headings,
        }

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html" and attrs.get("lang"):
            self.language = attrs["lang"].strip().lower() or None
        if tag == "meta":
            name = (
                attrs.get("name")
                or attrs.get("property")
                or attrs.get("itemprop")
                or ""
            ).strip().casefold()
            value = (attrs.get("content") or "").strip()
            if name in {"description", "og:description", "twitter:description"}:
                if value and not self.description:
                    self.description = value
            elif name == "keywords" and value and not self.keywords:
                self.keywords = list(dict.fromkeys(
                    keyword.strip()
                    for keyword in re.split(r"[,;]", value)
                    if keyword.strip()
                ))
        elif len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self._active_heading = {"level": int(tag[1]), "parts": []}

    def handle_endtag(self, tag):
        if (
            self._active_heading is not None
            and tag == f"h{self._active_heading['level']}"
        ):
            text = " ".join("".join(self._active_heading["parts"]).split())
            if text:
                self.headings.append({
                    "level": self._active_heading["level"],
                    "text": text,
                })
            self._active_heading = None

    def handle_data(self, data):
        if self._active_heading is not None:
            self._active_heading["parts"].append(data)


class BodyHTMLCleaner(HTMLParser):
    """Retire les zones exclues et détecte/élimine les blocs récurrents.

    Les balises candidates sont conservées temporairement afin de calculer une
    signature textuelle normalisée. Le HTML restitué garde le markup original
    hors blocs supprimés ; les entités et le texte sont rééchappés sans
    double-encodage.
    """

    def __init__(self, repeated_signatures=()):
        """Configure les signatures à retirer et réinitialise l'état du parseur."""
        super().__init__(convert_charrefs=True)
        self.repeated_signatures = repeated_signatures
        self._excluded_stack = []
        self._candidate_stack = []
        self._output = []
        self._candidates = []
        self._removed_blocks = 0

    def clean(self, html_text):
        """Nettoie un document HTML et retourne HTML, candidats et compte supprimé.

        Returns:
            Tuple `(cleaned_html, candidates, removed_count)`. Chaque candidat
            contient sa balise, son empreinte SHA-256 et un court échantillon.
        """
        self.feed(html_text)
        self.close()
        while self._candidate_stack:
            self._finish_candidate()
        return "".join(self._output), self._candidates, self._removed_blocks

    def handle_starttag(self, tag, _attrs):
        """Ouvre les zones exclues/candidates et conserve les balises utiles."""
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
        """Ferme les états de balise en tolérant un HTML imparfaitement imbriqué."""
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
        """Ajoute le texte au markup et aux candidats dans la limite configurée."""
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
        """Décode une référence nommée puis la traite comme texte normal."""
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name):
        """Décode une référence numérique puis la traite comme texte normal."""
        self.handle_data(html.unescape(f"&#{name};"))

    def handle_comment(self, _data):
        """Ignore les commentaires HTML."""
        return

    def _emit(self, markup):
        """Ajoute du markup à la balise candidate active ou à la sortie."""
        if self._candidate_stack:
            self._candidate_stack[-1]["markup"].append(markup)
        else:
            self._output.append(markup)

    def _finish_candidate(self):
        """Finalise le candidat supérieur et le conserve ou le supprime.

        Les blocs supprimés sont propagés comme compteurs aux candidats parents
        afin que le rapport ne compte pas plusieurs fois les mêmes suppressions
        imbriquées.
        """
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
