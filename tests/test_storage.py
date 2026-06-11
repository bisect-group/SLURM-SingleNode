from __future__ import annotations

import os
import pwd
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ssn.config import resolve_profile
from ssn.storage import (
    apply_fixture_scratch_cleanup,
    cleanup_operation_hash,
    quota_capability_report,
    scratch_health_report,
)


ROOT = Path(__file__).resolve().parents[1]


class StorageTests(unittest.TestCase):
    def test_quota_report_is_fixture_scoped_and_skips_without_active_quota(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-standard": {"status": "active", "tier": "standard"},
                "realuser": {"status": "active", "tier": "standard"},
            },
        }
        with (
            mock.patch("ssn.storage.shutil.which", return_value=None),
            mock.patch("ssn.storage.command_stdout", return_value=None),
            mock.patch("ssn.storage.command_rc", return_value=1),
        ):
            report = quota_capability_report(users_doc, resolved)
        self.assertEqual(report["fixture_users"], ["ssn-test-standard"])
        self.assertFalse(report["mounts"]["data"]["can_apply"])

    def test_scratch_health_report_can_pass_for_managed_user_dirs(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        current = pwd.getpwuid(os.getuid()).pw_name
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {current: {"status": "active", "tier": "standard"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "scratch"
            jobs = scratch / "jobs"
            for path in (jobs, scratch / current, scratch / current / "cache", scratch / current / "tmp"):
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o700 if current in str(path) else 0o755)
            local_resolved = dict(resolved)
            local_resolved["derived"] = {**resolved["derived"], "paths": {**resolved["derived"]["paths"], "scratch": str(scratch)}}
            storage = dict(resolved["resolved_policies"]["storage"])
            storage["job_scratch"] = {**storage["job_scratch"], "root": str(jobs)}
            local_resolved["resolved_policies"] = {**resolved["resolved_policies"], "storage": storage}
            with mock.patch("ssn.storage.command_stdout", return_value=str(scratch)):
                report = scratch_health_report(users_doc, local_resolved)
        self.assertTrue(report["healthy"])

    def test_fixture_cleanup_skips_non_fixture_paths(self) -> None:
        report = {
            "schema_version": 1,
            "command": "scratch-cleanup",
            "mode": "report_only",
            "root": "/scratch",
            "jobs_root_excluded": "/scratch/jobs",
            "age_days": 30,
            "candidates": [{"path": "/scratch/not-ssn-test", "type": "directory"}],
        }
        report["operation_hash"] = cleanup_operation_hash(report)
        applied = apply_fixture_scratch_cleanup(report)
        self.assertEqual(applied["deletion_results"][0]["status"], "skipped")
        self.assertEqual(applied["deletion_results"][0]["reason"], "not an allowed fixture path")

    def test_fixture_cleanup_allows_absent_fixture_path_without_touching_real_data(self) -> None:
        report = {
            "schema_version": 1,
            "command": "scratch-cleanup",
            "mode": "report_only",
            "root": "/scratch",
            "jobs_root_excluded": "/scratch/jobs",
            "age_days": 30,
            "candidates": [{"path": "/scratch/ssn-test-cleanup-unit", "type": "directory"}],
        }
        report["operation_hash"] = cleanup_operation_hash(report)
        applied = apply_fixture_scratch_cleanup(report)
        self.assertEqual(applied["deletion_results"][0]["status"], "skipped")
        self.assertEqual(applied["deletion_results"][0]["reason"], "already absent")


if __name__ == "__main__":
    unittest.main()
