"""Lectures en lecture seule des bases locales pour le portail Open Data."""

import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlsplit

from .pdf_index import SEARCH_TOKEN

RIGHTS_STATUS = "not_assessed"
RIGHTS_NOTICE = (
    "Les conditions de réutilisation ne sont pas encore évaluées. "
    "Consultez la source institutionnelle."
)
PDF_SNIPPET_START = "\ue000"
PDF_SNIPPET_END = "\ue001"


def list_sites(pages_database, pdf_database):
    """Fusionne les sites présents dans la base de pages et l'index PDF.

    Les URL terminées par `pricing` ou `token`, ou répétant un segment
    `pricing`, `contact` ou `token`, ne contribuent pas aux totaux. Les droits
    sont explicitement signalés comme non évalués ; cette fonction ne consulte
    jamais les sites sources.
    """
    pages_database = _require_database(pages_database, "base de pages")
    pdf_database = _require_database(pdf_database, "index PDF")
    pages_connection = _connect_readonly(pages_database)
    pdf_connection = _connect_readonly(pdf_database)
    try:
        sites = {}
        for start_url, page_count, latest, body_bytes in pages_connection.execute("""
            SELECT start_url, COUNT(*), MAX(crawled_at),
                   SUM(length(cleaned_text))
            FROM pages
            WHERE is_excluded_url(url)=0
            GROUP BY start_url ORDER BY start_url
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
            SELECT site_name, COUNT(*) FROM documents
            WHERE is_excluded_url(url)=0
            GROUP BY site_name
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


def list_pages(database, query=None, site=None, limit=20, offset=0, match_mode="contains"):
    """Recherche et pagine les pages nettoyées stockées localement.

    Tous les termes sont combinés avec AND et cherchés dans le titre ou le
    texte. Le résultat contient un extrait contextuel et les termes destinés
    à la surbrillance côté interface.

    Args:
        database: Fichier SQLite nettoyé.
        query: Recherche facultative, limitée par `_terms`.
        site: Hôte ou URL HTTPS du site à filtrer.
        limit: Nombre maximal de résultats.
        offset: Index de départ pour la pagination.
        match_mode: `contains` cherche aussi dans les mots ; `whole` exige
            une correspondance complète.

    Returns:
        Métadonnées de pagination, état des droits, termes correspondants et
        liste d'items synthétiques.
    """
    database = _require_database(database, "base de pages")
    terms = _terms(query)
    match_mode = _match_mode(match_mode)
    filters = []
    parameters = []
    filters.append("is_excluded_url(url)=0")
    if site:
        filters.append("start_url = ?")
        parameters.append(_site_start_url(site))
    for term in terms:
        if match_mode == "whole":
            filters.append(
                "(word_position(title, ?) > 0 OR word_position(cleaned_text, ?) > 0)"
            )
            parameters.extend((term, term))
        else:
            filters.append("(title LIKE ? ESCAPE '\\' OR cleaned_text LIKE ? ESCAPE '\\')")
            escaped = _escape_like(term)
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    if terms:
        position_function = (
            "word_position(cleaned_text, ?)"
            if match_mode == "whole"
            else "instr(lower(cleaned_text), lower(?))"
        )
        start_cases = " ".join(
            f"WHEN {position_function} > 0 "
            f"THEN max(1, {position_function} - 350)"
            for _ in terms
        )
        position_cases = " ".join(
            f"WHEN {position_function} > 0 THEN {position_function}"
            for _ in terms
        )
        excerpt_start = f"CASE {start_cases} ELSE 1 END"
        match_position = f"CASE {position_cases} ELSE 0 END"
        excerpt_parameters = [
            value
            for term in terms
            for value in (term, term)
        ] * 2
    else:
        excerpt_start = "1"
        match_position = "0"
        excerpt_parameters = []
    connection = _connect_readonly(database)
    try:
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pages)")
        }
        metadata_columns = (
            _optional_column(page_columns, "page_type"),
            _optional_column(page_columns, "language"),
            _optional_column(page_columns, "meta_description"),
            _optional_column(page_columns, "meta_keywords_json", "'[]'"),
            _optional_column(page_columns, "headings_json", "'[]'"),
            _optional_column(page_columns, "word_count"),
        )
        total = connection.execute(
            f"SELECT COUNT(*) FROM pages{where}",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT id, start_url, url, title, published_at, crawled_at,
                       substr(cleaned_text, {excerpt_start}, 700),
                       length(cleaned_text),
                       {match_position},
                       {", ".join(metadata_columns)}
                FROM pages{where}
                ORDER BY COALESCE(published_at, crawled_at) DESC, id DESC
                LIMIT ? OFFSET ?""",
            (
                *excerpt_parameters,
                *parameters,
                limit,
                offset,
            ),
        ).fetchall()
    finally:
        connection.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "search_fields": ["title", "text"],
        "matched_terms": terms,
        "match_mode": match_mode,
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
                "excerpt": _contextual_excerpt(
                    row[6] or "",
                    row[7] or 0,
                    row[8] or 0,
                ),
                "page_type": row[9] or "page",
                "language": row[10],
                "description": row[11],
                "keywords": _json_list(row[12]),
                "headings": _json_list(row[13]),
                "word_count": row[14],
                "rights_status": RIGHTS_STATUS,
            }
            for row in rows
        ],
    }


def get_page(database, page_id):
    """Retourne le texte intégral local d'une page, ou `None` si absente.

    Le texte n'est pas tronqué. Les URL exclues restent invisibles à l'API.
    """
    database = _require_database(database, "base de pages")
    connection = _connect_readonly(database)
    try:
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pages)")
        }
        metadata_columns = (
            _optional_column(page_columns, "page_type"),
            _optional_column(page_columns, "language"),
            _optional_column(page_columns, "meta_description"),
            _optional_column(page_columns, "meta_keywords_json", "'[]'"),
            _optional_column(page_columns, "headings_json", "'[]'"),
            _optional_column(page_columns, "word_count"),
        )
        row = connection.execute(
            f"""SELECT id, start_url, url, title, published_at, crawled_at, cleaned_text,
                       {", ".join(metadata_columns)}
               FROM pages WHERE id=? AND is_excluded_url(url)=0""",
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
        "text": text,
        "text_truncated": False,
        "page_type": row[7] or "page",
        "language": row[8],
        "description": row[9],
        "keywords": _json_list(row[10]),
        "headings": _json_list(row[11]),
        "word_count": row[12],
        "rights_status": RIGHTS_STATUS,
        "rights_notice": RIGHTS_NOTICE,
    }


def list_documents(database, query=None, site=None, limit=20, offset=0, match_mode="contains"):
    """Recherche et pagine les documents indexés localement.

    Le mode `whole` utilise FTS5 et classe les correspondances par BM25 ;
    `contains` cherche aussi à l'intérieur des mots. Sans requête, les
    documents sont ordonnés par date de collecte.
    """
    database = _require_database(database, "index PDF")
    terms = _terms(query)
    match_mode = _match_mode(match_mode)
    filters = []
    parameters = []
    filters.append("is_excluded_url(documents.url)=0")
    match_query = None
    if terms:
        if match_mode == "whole":
            match_query = " AND ".join(f'"{term}"' for term in terms)
            filters.append("pdf_search MATCH ?")
            parameters.append(match_query)
        else:
            for term in terms:
                escaped = _escape_like(term)
                filters.append("""(
                    documents.title LIKE ? ESCAPE '\\'
                    OR documents.author LIKE ? ESCAPE '\\'
                    OR documents.site_name LIKE ? ESCAPE '\\'
                    OR documents.extracted_text LIKE ? ESCAPE '\\'
                )""")
                pattern = f"%{escaped}%"
                parameters.extend((pattern, pattern, pattern, pattern))
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
        if match_mode == "whole" and terms:
            snippet_expression = """snippet(
                pdf_search, 3, ?, ?, ' … ', 14
            )"""
            snippet_parameters = [PDF_SNIPPET_START, PDF_SNIPPET_END]
            order_by = "bm25(pdf_search), documents.downloaded_at DESC, documents.title"
        elif match_mode == "contains" and terms:
            first_term = terms[0]
            snippet_expression = """CASE
                WHEN instr(lower(documents.extracted_text), lower(?)) > 0
                THEN substr(
                    documents.extracted_text,
                    max(1, instr(lower(documents.extracted_text), lower(?)) - 350),
                    700
                )
                ELSE '' END"""
            snippet_parameters = [first_term, first_term]
            order_by = "documents.downloaded_at DESC, documents.title"
        else:
            snippet_expression = "''"
            snippet_parameters = []
            order_by = "documents.downloaded_at DESC, documents.title"
        rows = connection.execute(
            f"""SELECT documents.id, documents.site_name, documents.url,
                       documents.filename, documents.title, documents.author,
                       documents.page_count, documents.size_bytes,
                       documents.downloaded_at, documents.status,
                       {snippet_expression}
                FROM documents
                JOIN pdf_search ON documents.id=pdf_search.rowid{where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?""",
            (*snippet_parameters, *parameters, limit, offset),
        ).fetchall()
    finally:
        connection.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "search_fields": ["title", "author", "site", "text"],
        "matched_terms": terms,
        "match_mode": match_mode,
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
                "excerpt": (row[10] or "")
                    .replace(PDF_SNIPPET_START, "")
                    .replace(PDF_SNIPPET_END, ""),
                "rights_status": RIGHTS_STATUS,
            }
            for row in rows
        ],
    }


def get_document(database, document_id):
    """Retourne le texte extrait intégral d'un document indexé, ou `None`.

    Cette fonction sert le contenu déjà enregistré localement et n'ouvre pas
    le PDF ni son URL source.
    """
    database = _require_database(database, "index PDF")
    connection = _connect_readonly(database)
    try:
        row = connection.execute(
            """SELECT id, site_name, url, filename, title, author, page_count,
                      size_bytes, downloaded_at, status, extracted_text
               FROM documents WHERE id=? AND is_excluded_url(url)=0""",
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
        "text": text,
        "text_truncated": False,
        "rights_status": RIGHTS_STATUS,
        "rights_notice": RIGHTS_NOTICE,
    }


def list_images(
    database,
    images_directory,
    query=None,
    site=None,
    limit=20,
    offset=0,
    match_mode="contains",
):
    """Liste les images locales téléchargées et leurs pages de provenance.

    La table `images` fournit les fichiers réellement présents ; `pages.images_json`
    relie les URL d'image aux pages qui les référencent. Aucun accès réseau
    n'est effectué. Les entrées dont le fichier manque ou sort du dossier
    d'images sont exclues.
    """
    database = _require_database(database, "base de collecte")
    images_directory = Path(images_directory).resolve()
    terms = _terms(query)
    match_mode = _match_mode(match_mode)
    site_host = _site_host(site) if site else None
    connection = _connect_readonly(database)
    try:
        rows = connection.execute("""
            SELECT pages.start_url, pages.url, pages.title, json_each.value,
                   images.local_path
            FROM pages
            JOIN json_each(pages.images_json)
            JOIN images ON images.url=json_each.value
            WHERE images.status='downloaded'
              AND images.local_path IS NOT NULL
              AND is_excluded_url(pages.url)=0
            ORDER BY pages.start_url, pages.url, json_each.value
        """)
        items = []
        seen = set()
        for start_url, page_url, title, image_url, local_path in rows:
            host = urlsplit(start_url).hostname or start_url
            if site_host and host.lower() != site_host:
                continue
            searchable_values = (host, page_url, title or "", image_url)
            if any(
                not any(
                    term.casefold() in value.casefold()
                    if match_mode == "contains"
                    else _contains_word(value, term)
                    for value in searchable_values
                )
                for term in terms
            ):
                continue
            image_path = _image_file_path(local_path, images_directory)
            if image_path is None or not image_path.is_file():
                continue
            key = (image_url, page_url)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "site": host,
                "title": title or page_url,
                "source_url": page_url,
                "image_url": image_url,
                "local_url": (
                    "/api/v1/images/file?url="
                    + quote(image_url, safe="")
                ),
            })
    finally:
        connection.close()

    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "search_fields": ["site", "page title", "image URL"],
        "matched_terms": terms,
        "match_mode": match_mode,
        "items": items[offset:offset + limit],
    }


def resolve_image_file(database, images_directory, image_url):
    """Résout une URL d'image vers son fichier local autorisé, ou `None`.

    Seules les lignes en statut `downloaded` sont servies. Le chemin enregistré
    est résolu puis confiné au dossier `images_directory`, afin qu'une valeur
    SQLite ne puisse pas permettre de lire un fichier arbitraire.
    """
    database = _require_database(database, "base de collecte")
    images_directory = Path(images_directory).resolve()
    connection = _connect_readonly(database)
    try:
        row = connection.execute(
            """SELECT local_path FROM images
               WHERE url=? AND status='downloaded' AND local_path IS NOT NULL""",
            (image_url,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    image_path = _image_file_path(row[0], images_directory)
    if image_path is None or not image_path.is_file():
        return None
    return image_path


def _image_file_path(local_path, images_directory):
    """Résout un chemin image absolu ou relatif en restant dans le dossier racine."""
    path = Path(local_path)
    candidates = (
        [path.resolve()]
        if path.is_absolute()
        else [
            (images_directory.parent / path).resolve(),
            (images_directory / path).resolve(),
        ]
    )
    for candidate in candidates:
        try:
            candidate.relative_to(images_directory)
        except ValueError:
            continue
        return candidate
    return None


def _require_database(path, description):
    """Résout un chemin de base et lève une erreur explicite s'il est absent."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{description} introuvable : {path}")
    return path


def _connect_readonly(path):
    """Ouvre SQLite en lecture seule et enregistre le filtre d'URL exclue."""
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro",
        uri=True,
        timeout=15,
    )
    connection.create_function("is_excluded_url", 1, _is_excluded_url)
    connection.create_function("word_position", 2, _word_position)
    return connection


def _optional_column(columns, column, default_sql="NULL"):
    """Sélectionne une colonne enrichie ou une valeur compatible ancien schéma."""
    if column in columns:
        return column
    return f"{default_sql} AS {column}"


def _json_list(value):
    """Décode une liste JSON persistée dans la base nettoyée."""
    if value is None:
        return []
    items = json.loads(value)
    if not isinstance(items, list):
        raise ValueError("Les métadonnées JSON doivent être une liste.")
    return items


def _is_excluded_url(url):
    """Retourne 1 pour les routes exclues `pricing`, `contact` ou `token`."""
    segments = [
        segment.casefold()
        for segment in urlsplit(url).path.split("/")
        if segment
    ]
    excluded = bool(segments and segments[-1] in {"pricing", "token"})
    excluded = excluded or any(
        segments.count(tag) > 1
        for tag in {"pricing", "contact", "token"}
    )
    return int(excluded)


def _contextual_excerpt(text, total_length, match_position):
    """Ajoute les ellipses de contexte adaptées à un extrait de résultat."""
    if not text:
        return ""
    start_position = max(0, match_position - 351) if match_position else 0
    prefix = "… " if start_position else ""
    suffix = " …" if start_position + len(text) < total_length else ""
    return f"{prefix}{text}{suffix}"


def _terms(query):
    """Valide et tokenise une recherche textuelle pour l'API locale.

    Les requêtes vides deviennent une liste vide ; les recherches non vides
    sont limitées à 200 caractères et dix mots.

    Raises:
        ValueError: Si la requête ne contient aucun mot ou dépasse ses limites.
    """
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


def _match_mode(match_mode):
    """Valide le mode de comparaison choisi pour les termes de recherche."""
    if match_mode not in {"contains", "whole"}:
        raise ValueError("Le mode de recherche doit être `contains` ou `whole`.")
    return match_mode


def _contains_word(text, term):
    """Indique si `term` apparaît comme un mot complet, sans les chiffres/lettres autour."""
    if not text:
        return False
    pattern = rf"(?<![^\W_]){re.escape(term)}(?![^\W_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _word_position(text, term):
    """Retourne la position SQL (base 1) du premier mot entier, ou zéro."""
    if not text or not term:
        return 0
    pattern = rf"(?<![^\W_]){re.escape(term)}(?![^\W_])"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.start() + 1 if match else 0


def _site_start_url(site):
    """Convertit le filtre de site en URL HTTPS canonique de départ."""
    host = _site_host(site)
    return f"https://{host}/"


def _site_host(site):
    """Valide un nom d'hôte ou une URL HTTPS sans chemin applicatif."""
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
    """Échappe les caractères spéciaux LIKE pour une recherche littérale."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
