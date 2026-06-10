from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ssn.config import resolve_profile
from ssn.units import duration_to_seconds, memory_to_mb, normalize_duration


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_cpu_dev_profile_resolves(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        self.assertFalse(resolved["derived"]["has_gpus"])
        self.assertEqual(resolved["derived"]["cpus_allocatable"], 18)
        self.assertEqual(resolved["identity"]["default_partition"], "compute")
        tiers = {tier["name"]: tier for tier in resolved["derived"]["rendered_tiers"]}
        self.assertEqual(tiers["standard"]["max_cpus_per_job"], 4)
        self.assertIsNone(tiers["standard"]["max_gpus_per_job"])
        self.assertEqual(tiers["emergency"]["max_cpus_per_job"], 18)

    def test_gpu_profile_blocks_review_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "REVIEW_REQUIRED"):
            resolve_profile("generic-nvidia-4gpu", ROOT)

    def test_dgx_profile_resolves_as_gpu(self) -> None:
        resolved = resolve_profile("dgx-v100", ROOT)
        self.assertTrue(resolved["derived"]["has_gpus"])
        self.assertEqual(len(resolved["derived"]["gres_entries"]), 8)

    def test_render_values_are_serializable(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.txt"
            path.write_text(str(resolved["derived"]["rendered_tiers"]))
            self.assertTrue(path.exists())


class UnitTests(unittest.TestCase):
    def test_memory_to_mb(self) -> None:
        self.assertEqual(memory_to_mb("4GB"), 4096)
        self.assertEqual(memory_to_mb("122097MB"), 122097)

    def test_duration_normalization(self) -> None:
        self.assertEqual(duration_to_seconds("4h"), 14400)
        self.assertEqual(normalize_duration("96h"), "4-00:00:00")


if __name__ == "__main__":
    unittest.main()
