from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from ssn.config import resolve_profile
from ssn.gpu import _parse_gpu_count, gpu_recovery_plan, gpu_verification_errors, gpu_verification_report


ROOT = Path(__file__).resolve().parents[1]


class GpuSafetyTests(unittest.TestCase):
    def test_parse_gpu_count_handles_typed_and_untyped_gres(self) -> None:
        self.assertEqual(_parse_gpu_count("AllocTRES=cpu=1,gres/gpu=1"), 1)
        self.assertEqual(_parse_gpu_count("Gres=gpu:quadro_p620:1"), 1)
        self.assertEqual(_parse_gpu_count("TresPerNode=gres/gpu:2"), 2)

    def test_recovery_plan_separates_fixture_and_nonfixture_gpu_jobs(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        recovery = resolve_profile("cpu-bisect-node0", ROOT)
        with mock.patch(
            "ssn.gpu.gpu_jobs",
            return_value=[
                {"id": "1", "state": "RUNNING", "user": "ssn-test-standard", "name": "fixture", "gpu_count": 1},
                {"id": "2", "state": "PENDING", "user": "realuser", "name": "real", "gpu_count": 1},
            ],
        ):
            plan = gpu_recovery_plan(resolved, recovery)
        self.assertEqual(len(plan["fixture_gpu_jobs"]), 1)
        self.assertEqual(len(plan["nonfixture_gpu_jobs"]), 1)
        self.assertEqual(plan["actions"][0]["action"], "cancel")

    def test_gpu_report_marks_missing_gpu_unhealthy(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        with mock.patch("ssn.gpu.command_stdout", return_value=None), mock.patch("ssn.gpu.Path.exists", return_value=False):
            report = gpu_verification_report(resolved, conf_dir="/missing")
        errors = gpu_verification_errors(report)
        self.assertTrue(errors)
        self.assertFalse(report["healthy"])


if __name__ == "__main__":
    unittest.main()
