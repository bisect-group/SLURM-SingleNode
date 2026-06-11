from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ssn.ops import validate_feature_gates, wait_for_no_active_jobs, create_plan_token, validate_plan_token


class PlanTokenTests(unittest.TestCase):
    def _write_report(self, root: Path, **overrides: object) -> Path:
        report = {
            "schema_version": 1,
            "command": "apply",
            "profile": "gpu-bisect-quadro-p620",
            "config_hash": "abc123",
            "risk": "queued_jobs",
        }
        report.update(overrides)
        path = root / "apply-report.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return path

    def test_create_and_validate_plan_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._write_report(root)
            store = root / "tokens"
            token, record = create_plan_token(
                plan,
                risk="queued_jobs",
                reason="unit test",
                store_root=store,
            )
            self.assertEqual(record["risk"], "queued_jobs")
            validated = validate_plan_token(
                token,
                json.loads(plan.read_text()),
                risk="queued_jobs",
                store_root=store,
            )
            self.assertEqual(validated["token_id"], record["token_id"])

    def test_plan_token_is_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._write_report(root)
            store = root / "tokens"
            token, _ = create_plan_token(plan, risk="queued_jobs", reason="unit test", store_root=store)
            report = json.loads(plan.read_text())
            validate_plan_token(token, report, risk="queued_jobs", store_root=store)
            with self.assertRaisesRegex(ValueError, "already been used"):
                validate_plan_token(token, report, risk="queued_jobs", store_root=store)

    def test_plan_token_is_bound_to_config_hash_and_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._write_report(root)
            store = root / "tokens"
            token, _ = create_plan_token(plan, risk="queued_jobs", reason="unit test", store_root=store)
            report = json.loads(plan.read_text())
            report["config_hash"] = "changed"
            with self.assertRaisesRegex(ValueError, "not bound"):
                validate_plan_token(token, report, risk="queued_jobs", store_root=store, mark_used=False)
            with self.assertRaisesRegex(ValueError, "not bound"):
                validate_plan_token(
                    token,
                    json.loads(plan.read_text()),
                    risk="different_risk",
                    store_root=store,
                    mark_used=False,
                )

    def test_plan_token_is_bound_to_operation_hash_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._write_report(root, command="scratch-cleanup", risk="fixture_scratch_cleanup", operation_hash="one")
            store = root / "tokens"
            token, _ = create_plan_token(
                plan,
                risk="fixture_scratch_cleanup",
                reason="unit test",
                store_root=store,
            )
            report = json.loads(plan.read_text())
            report["operation_hash"] = "two"
            with self.assertRaisesRegex(ValueError, "not bound"):
                validate_plan_token(
                    token,
                    report,
                    risk="fixture_scratch_cleanup",
                    store_root=store,
                    mark_used=False,
                )

    def test_plan_token_expiry_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._write_report(root)
            store = root / "tokens"
            token, record = create_plan_token(plan, risk="queued_jobs", reason="unit test", store_root=store)
            token_path = store / f"{record['token_id']}.json"
            stored = json.loads(token_path.read_text())
            stored["expires_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
            token_path.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(ValueError, "expired"):
                validate_plan_token(
                    token,
                    json.loads(plan.read_text()),
                    risk="queued_jobs",
                    store_root=store,
                )

    def test_plan_token_requires_bound_plan_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._write_report(root, config_hash=None)
            with self.assertRaisesRegex(ValueError, "no config_hash"):
                create_plan_token(plan, risk="queued_jobs", reason="unit test", store_root=root / "tokens")


class FeatureGateTests(unittest.TestCase):
    def test_feature_gate_reports_missing_required_commands(self) -> None:
        resolved = {
            "derived": {"has_gpus": False, "paths": {}},
            "resolved_policies": {"storage": {"quotas": {}, "job_scratch": {}}},
        }
        capabilities = {
            "cgroup_fs": "cgroup2fs",
            "commands": {},
            "mounts": {},
        }
        errors = validate_feature_gates(resolved, mode="apply", capabilities=capabilities)
        self.assertTrue(any("ansible-playbook" in error for error in errors))
        self.assertTrue(any("scontrol" in error for error in errors))

    def test_feature_gate_checks_gpu_count_and_devices(self) -> None:
        resolved = {
            "hardware": {"gpus": 1},
            "derived": {"has_gpus": True, "paths": {}},
            "resolved_policies": {"storage": {"quotas": {}, "job_scratch": {}}},
        }
        capabilities = {
            "cgroup_fs": "cgroup2fs",
            "commands": {
                "ansible-playbook": "/usr/bin/ansible-playbook",
                "lua5.3": "/usr/bin/lua5.3",
                "scontrol": "/usr/bin/scontrol",
                "squeue": "/usr/bin/squeue",
                "slurmd": "/usr/sbin/slurmd",
                "sacctmgr": "/usr/bin/sacctmgr",
                "sinfo": "/usr/bin/sinfo",
                "sbatch": "/usr/bin/sbatch",
                "nvidia-smi": "/usr/bin/nvidia-smi",
            },
            "mounts": {},
            "nvidia": {"query": "", "devices": {"/dev/nvidia0": False}},
            "slurm": {"accounting_cluster": "ssn"},
        }
        errors = validate_feature_gates(resolved, mode="apply", capabilities=capabilities)
        self.assertTrue(any("expects 1 GPU" in error for error in errors))
        self.assertTrue(any("/dev/nvidia0" in error for error in errors))

    def test_wait_for_no_active_jobs_times_out_with_last_jobs(self) -> None:
        with mock.patch("ssn.ops.active_jobs", return_value=[{"id": "1", "state": "RUNNING"}]):
            jobs = wait_for_no_active_jobs(0, poll_seconds=0)
        self.assertEqual(jobs[0]["id"], "1")


if __name__ == "__main__":
    unittest.main()
