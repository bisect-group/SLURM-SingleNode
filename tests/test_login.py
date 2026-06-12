from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ssn.config import resolve_profile
from ssn.login import (
    _parse_gpu_count,
    _slice_dropin_content,
    gpu_snapshot_status,
    login_isolation_report,
)


ROOT = Path(__file__).resolve().parents[1]


class LoginIsolationTests(unittest.TestCase):
    def test_login_isolation_targets_only_active_managed_fixtures(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-standard": {"status": "active", "tier": "standard"},
                "ssn-test-suspended": {"status": "suspended", "tier": "standard"},
                "realuser": {"status": "active", "tier": "standard"},
            },
        }
        state_doc = {
            "schema_version": 1,
            "users": {
                "ssn-test-standard": {"managed": True},
                "ssn-test-suspended": {"managed": True},
                "realuser": {"managed": True},
            },
        }
        with mock.patch("ssn.login.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=1003, pw_gid=1003)):
            report = login_isolation_report(users_doc, state_doc, resolved)
        self.assertEqual([target["user"] for target in report["targets"]], ["ssn-test-standard"])
        self.assertEqual(report["limits"]["cpu_quota"], "200%")
        self.assertEqual(report["limits"]["memory_max"], "4096M")

    def test_login_isolation_managed_allowlist_targets_only_allowed_users(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-standard": {"status": "active", "tier": "standard"},
                "ssn-test-priority": {"status": "active", "tier": "priority"},
                "realuser": {"status": "active", "tier": "standard"},
            },
        }
        state_doc = {
            "schema_version": 1,
            "users": {
                "ssn-test-standard": {"managed": True},
                "ssn-test-priority": {"managed": True},
                "realuser": {"managed": True},
            },
        }
        with mock.patch("ssn.login.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=1003, pw_gid=1003)):
            report = login_isolation_report(
                users_doc,
                state_doc,
                resolved,
                target_scope="managed_allowlist",
                allow_users=["realuser"],
            )
        self.assertEqual([target["user"] for target in report["targets"]], ["realuser"])
        self.assertEqual(report["target_scope"], "managed_allowlist")

    def test_cgroup_dropin_includes_login_limits_and_device_closure(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        content = _slice_dropin_content(resolved, gpu_mode="cgroup", enabled=True)
        self.assertIn("CPUQuota=200%", content)
        self.assertIn("MemoryMax=4096M", content)
        self.assertIn("TasksMax=128", content)
        self.assertIn("DevicePolicy=closed", content)
        self.assertIn("DeviceAllow=/dev/null rw", content)

    def test_disabled_dropin_resets_controls_without_deleting_file(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        content = _slice_dropin_content(resolved, gpu_mode="disabled", enabled=False)
        self.assertIn("Login isolation is disabled", content)
        self.assertIn("CPUQuota=", content)
        self.assertIn("DevicePolicy=auto", content)

    def test_gpu_snapshot_status_reports_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gpu-status.json"
            path.write_text(
                '{"schema_version":1,"generated_at":"2000-01-01T00:00:00+00:00","gpus":[],"slurm_jobs":[]}'
            )
            status = gpu_snapshot_status(path)
        self.assertTrue(status["exists"])
        self.assertFalse(status["fresh"])

    def test_parse_gpu_count_from_slurm_job_detail(self) -> None:
        detail = "JobId=42 AllocTRES=cpu=1,mem=128M,gres/gpu=1 TresPerNode=gres/gpu:1"
        self.assertEqual(_parse_gpu_count(detail), 1)


if __name__ == "__main__":
    unittest.main()
