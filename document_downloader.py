import hashlib
import logging
import mimetypes
import os
import re
from urllib.parse import unquote
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm",
    ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".ppsm", ".pot", ".potx", ".potm",
    ".xla", ".xlam", ".ppa", ".ppam", ".vsdm", ".wps",
    ".odt", ".ods", ".odp", ".odg",
    ".rtf", ".csv", ".pub", ".vsd", ".vsdx", ".one", ".mdb", ".accdb",
    ".pages", ".numbers", ".key",
}
MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
}


def document_extension(url, content_type):
    extension = Path(urlsplit(url).path).suffix.lower()
    if extension in SUPPORTED_EXTENSIONS:
        return extension
    return MIME_EXTENSIONS.get(content_type.lower())


class DocumentDownloader:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)

    def save(self, url, content_type, body, site_name):
        extension = document_extension(url, content_type)
        if extension is None:
            raise ValueError(f"Format de document non pris en charge : {content_type} ({url})")

        site_dir = self.output_dir / site_name
        site_dir.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        original_name = unquote(Path(urlsplit(url).path).name)
        original_stem = Path(original_name).stem or "document"
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", original_stem).strip(" ._")
        safe_stem = (safe_stem or "document")[:100]
        file_path = site_dir / f"{url_hash}_{safe_stem}{extension}"
        temporary_path = file_path.with_suffix(file_path.suffix + ".part")
        temporary_path.write_bytes(body)
        os.replace(temporary_path, file_path)
        logger.info("DOCUMENT saved %s -> %s (%d bytes)", url, file_path, len(body))
        return {
            "url_hash": url_hash,
            "site_name": site_name,
            "local_path": str(file_path),
            "filename": file_path.name,
            "content_type": content_type,
            "size_bytes": len(body),
        }
