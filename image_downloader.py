import hashlib
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class ImageDownloader:
    def __init__(self, fetcher, output_dir, retries=2, before_request=None):
        self.fetcher = fetcher
        self.output_dir = Path(output_dir)
        self.retries = retries
        self.before_request = before_request or (lambda: None)

    def download(self, url):
        self.before_request()
        body, content_type, _ = self.fetcher.fetch(url, retries=self.retries)
        if not content_type.startswith("image/"):
            raise ValueError(f"Ressource non image ({content_type}) : {url}")

        extension = mimetypes.guess_extension(content_type) or Path(urlsplit(url).path).suffix
        filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + extension
        self.output_dir.mkdir(parents=True, exist_ok=True)
        image_path = self.output_dir / filename
        if not image_path.exists():
            image_path.write_bytes(body)
        logger.info("IMAGE saved %s -> %s (%d bytes)", url, image_path, len(body))
        return image_path
