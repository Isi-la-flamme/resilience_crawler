import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime


class Metrics:
    """Agrège les durées et volumes d'appels par phase, de façon thread-safe."""

    def __init__(self):
        """Crée un agrégateur vide protégé pour les accès concurrents."""
        self._durations = {}
        self._counts = {}
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, phase):
        """Mesure la durée d'un bloc et ajoute un appel à la phase.

        La durée est enregistrée dans un bloc `finally`, donc également si le
        code mesuré lève une exception. La méthode est un context manager :
        `with metrics.measure("fetch")`.
        """
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                self._durations[phase] = self._durations.get(phase, 0.0) + elapsed
                self._counts[phase] = self._counts.get(phase, 0) + 1

    def summary(self):
        """Retourne une copie des agrégats, avec moyenne en millisecondes.

        La structure de retour est indexée par phase et expose `total_seconds`,
        `calls` et `avg_ms`. Les durées et compteurs sont copiés sous verrou.
        """
        with self._lock:
            return {
                phase: {
                    "total_seconds": round(self._durations[phase], 4),
                    "calls": self._counts[phase],
                    "avg_ms": round(
                        1000 * self._durations[phase] / self._counts[phase], 2
                    ),
                }
                for phase in self._durations
            }

    def dump(
        self,
        path,
        workers,
        max_pages,
        image_batch_size,
        crawl_duration_seconds,
    ):
        """Écrit un rapport JSON des métriques dans `path`.

        Le numéro de test augmente à partir du rapport précédent s'il existe.
        Chaque phase inclut son pourcentage de la durée totale. Un rapport
        précédent mal formé n'est pas écrasé silencieusement.

        Raises:
            ValueError: Si la durée totale est non positive ou si le rapport
                précédent contient une forme ou un numéro invalide.
            OSError: Si le fichier ne peut pas être lu ou écrit.
        """
        if crawl_duration_seconds <= 0:
            raise ValueError("La durée totale du crawl doit être strictement positive.")

        test_number = 1
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                raise ValueError(f"Le fichier de métriques doit contenir un objet JSON : {path}")
            previous_test_number = previous.get("test_number", 0)
            if (
                not isinstance(previous_test_number, int)
                or isinstance(previous_test_number, bool)
                or previous_test_number < 0
            ):
                raise ValueError(f"test_number doit être un entier positif ou nul : {path}")
            test_number = previous_test_number + 1

        data = {
            "crawl_duration_seconds": round(crawl_duration_seconds, 2),
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "test_number": test_number,
            "configuration": {
                "workers": workers,
                "max_pages": max_pages,
                "image_batch_size": image_batch_size,
            },
        }
        for phase, summary in self.summary().items():
            summary["usage_percent"] = round(
                100 * summary["total_seconds"] / crawl_duration_seconds,
                2,
            )
            data[phase] = summary
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def reset(self):
        """Réinitialise les durées et compteurs sans modifier le fichier JSON."""
        with self._lock:
            self._durations.clear()
            self._counts.clear()


metrics = Metrics()