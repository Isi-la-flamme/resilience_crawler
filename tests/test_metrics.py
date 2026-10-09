import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from metrics import Metrics


class MetricsTests(unittest.TestCase):
    def test_dump_adds_date_and_increments_test_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.json"
            path.write_text(json.dumps({"test_number": 4}), encoding="utf-8")

            metrics = Metrics()
            with metrics.measure("fetch"):
                pass
            metrics.dump(
                path,
                workers=4,
                max_pages=50,
                image_batch_size=3,
                crawl_duration_seconds=10,
            )

            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["test_number"], 5)
        self.assertEqual(
            data["configuration"],
            {"workers": 4, "max_pages": 50, "image_batch_size": 3},
        )
        self.assertEqual(list(data)[0], "crawl_duration_seconds")
        self.assertEqual(data["crawl_duration_seconds"], 10)
        self.assertAlmostEqual(
            data["fetch"]["usage_percent"],
            100 * data["fetch"]["total_seconds"] / data["crawl_duration_seconds"],
        )
        self.assertIsInstance(datetime.fromisoformat(data["date"]), datetime)

    def test_dump_starts_test_number_at_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.json"

            Metrics().dump(
                path,
                workers=1,
                max_pages=10,
                image_batch_size=1,
                crawl_duration_seconds=10,
            )

            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["test_number"], 1)


if __name__ == "__main__":
    unittest.main()
