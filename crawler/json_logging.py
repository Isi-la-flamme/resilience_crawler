import json
import logging
from datetime import datetime, timezone


class JsonLineFormatter(logging.Formatter):
    """Formate les entrées de journal en objets JSON sur une seule ligne."""

    def format(self, record):
        """Sérialise le niveau, l'heure UTC, le logger et le message.

        Les détails d'une exception sont ajoutés sous `exception` lorsqu'ils
        existent. Les caractères Unicode sont écrits sans échappement superflu.
        """
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)
