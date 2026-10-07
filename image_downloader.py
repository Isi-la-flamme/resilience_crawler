import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from file_store import FileStore


logger = logging.getLogger(__name__)


class ImageDownloader:
    def __init__(self, fetcher, output_dir, retries=2, before_request=None):
        self.fetcher = fetcher
        self.store = FileStore(output_dir)
        self.retries = retries
        self.before_request = before_request or (lambda: None)

    def download(self, url):
        self.before_request()
        body, content_type, _ = self.fetcher.fetch(url, retries=self.retries)
        if not content_type.startswith("image/"):
            raise ValueError(f"Ressource non image ({content_type}) : {url}")

        extension = mimetypes.guess_extension(content_type) or Path(urlsplit(url).path).suffix
        filename = self.store.build_name(url, extension)
        image_path = self.store.write(filename, body)
        logger.info("IMAGE saved %s -> %s (%d bytes)", url, image_path, len(body))
        return image_path