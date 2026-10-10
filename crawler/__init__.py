"""API Python minimale du package crawler.

Les imports publics exposent le moteur de collecte et son stockage SQLite.
"""

from .database import CrawlDatabase
from .engine import Crawler

__all__ = ["Crawler", "CrawlDatabase"]
