import logging
from pathlib import Path
from urllib.parse import urlsplit

from .file_store import FileStore, full_url_hash


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

        site_store = FileStore(self.output_dir / site_name)
        filename = site_store.build_name(url, extension)
        file_path = site_store.write(filename, body)
        logger.info("DOCUMENT saved %s -> %s (%d bytes)", url, file_path, len(body))
        return {
            "url_hash": full_url_hash(url),
            "site_name": site_name,
            "local_path": str(file_path),
            "filename": file_path.name,
            "content_type": content_type,
            "size_bytes": len(body),
        }