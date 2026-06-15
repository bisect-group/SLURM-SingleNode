from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ssn.config import resolve_profile
from ssn.modules import modules_status_report, modules_verify_errors


ROOT = Path(__file__).resolve().parents[1]


class ModulesTests(unittest.TestCase):
    def test_missing_cuda_and_miniconda_are_validate_only_skips(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            report = modules_status_report(resolved, cuda_prefix=Path(tmp))
        self.assertEqual(report["cuda"]["status"], "not_detected")
        self.assertEqual(report["miniconda"]["status"], "not_detected")
        self.assertEqual(report["modulefiles"], [])

    def test_single_cuda_toolkit_renders_default_and_versioned_modules(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            toolkit = prefix / "cuda-12.4"
            (toolkit / "bin").mkdir(parents=True)
            (toolkit / "lib64").mkdir()
            (toolkit / "include").mkdir()
            (toolkit / "version.json").write_text(json.dumps({"cuda": {"version": "12.4.1"}}))
            report = modules_status_report(resolved, cuda_prefix=prefix)
        names = {item["name"] for item in report["modulefiles"]}
        self.assertEqual(report["cuda"]["status"], "detected")
        self.assertIn("cuda", names)
        self.assertIn("cuda/12.4", names)

    def test_multiple_cuda_toolkits_without_default_need_review(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            for version in ("11.8", "12.4"):
                root = prefix / f"cuda-{version}"
                (root / "lib64").mkdir(parents=True)
            report = modules_status_report(resolved, cuda_prefix=prefix)
        names = {item["name"] for item in report["modulefiles"]}
        self.assertEqual(report["cuda"]["status"], "needs_default_review")
        self.assertNotIn("cuda", names)
        self.assertIn("cuda/11.8", names)
        self.assertIn("cuda/12.4", names)

    def test_miniconda_module_renders_when_conda_exists(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            conda_root = Path(tmp) / "miniconda3"
            conda = conda_root / "bin" / "conda"
            conda.parent.mkdir(parents=True)
            conda.write_text("#!/bin/sh\n")
            resolved["resolved_policies"]["modules"]["shared_env_base"]["root"] = str(conda_root)
            with mock.patch("ssn.modules.command_stdout", return_value="conda 24.1.0"):
                report = modules_status_report(resolved)
        names = {item["name"] for item in report["modulefiles"]}
        self.assertEqual(report["miniconda"]["status"], "detected")
        self.assertIn("miniconda3", names)

    def test_modules_verify_errors_only_reports_failures(self) -> None:
        report = {
            "checks": [
                {"name": "ok", "status": "PASS", "detail": "fine"},
                {"name": "skip", "status": "SKIP", "detail": "absent"},
                {"name": "bad", "status": "FAIL", "detail": "broken"},
            ]
        }
        self.assertEqual(modules_verify_errors(report), ["bad: broken"])


if __name__ == "__main__":
    unittest.main()
