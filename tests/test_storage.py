from __future__ import annotations

import os
import pwd
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ssn.config import resolve_profile
from ssn.storage import (
    SCRATCH_CLEANUP_RISK,
    STORAGE_QUOTA_ENABLE_RISK,
    apply_fixture_scratch_cleanup,
    apply_fixture_quotas,
    apply_user_scratch_cleanup,
    cleanup_operation_hash,
    enable_storage_quotas,
    parse_fixture_quota_overrides,
    quota_capability_report,
    scratch_health_report,
    storage_quota_operation_hash,
    storage_quota_plan,
    _fstab_with_quota_options,
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

    def test_quota_report_includes_home_data_scratch_with_overrides(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {"ssn-test-quota": {"status": "active", "tier": "standard"}},
        }

        def fake_findmnt(path: str) -> dict[str, str]:
            mountpoint = "/" if path == "/home" else path
            return {"target": mountpoint, "source": f"dev-{mountpoint}", "fstype": "ext4", "options": "rw", "raw": "raw"}

        with (
            mock.patch("ssn.storage._findmnt_info", side_effect=fake_findmnt),
            mock.patch("ssn.storage._find_fstab_entry", return_value={"options": "defaults"}),
            mock.patch(
                "ssn.storage._quota_active_for_mount",
                return_value={"quotaon": "user quota is on", "repquota_user_rc": 0, "repquota_group_rc": 0, "active_user_quota": True, "active_group_quota": True},
            ),
            mock.patch("ssn.storage.shutil.which", return_value="/usr/sbin/tool"),
        ):
            report = quota_capability_report(
                users_doc,
                resolved,
                quota_overrides=parse_fixture_quota_overrides(["home=64MB", "data=64MB", "scratch=128MB"]),
            )

        self.assertEqual(sorted(report["mounts"]), ["data", "home", "scratch"])
        self.assertEqual(report["mounts"]["home"]["mountpoint"], "/")
        self.assertEqual(report["mounts"]["home"]["hard_kb"], 64 * 1024)
        self.assertEqual(report["mounts"]["scratch"]["hard_kb"], 128 * 1024)

    def test_quota_report_all_managed_is_report_only(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-quota": {"status": "active", "tier": "standard"},
                "realuser": {"status": "active", "tier": "standard"},
                "suspended": {"status": "suspended", "tier": "standard"},
            },
        }

        with (
            mock.patch("ssn.storage._findmnt_info", return_value={"target": "/data", "source": "dev", "fstype": "ext4", "options": "rw", "raw": "raw"}),
            mock.patch("ssn.storage._find_fstab_entry", return_value={"options": "defaults"}),
            mock.patch(
                "ssn.storage._quota_active_for_mount",
                return_value={"quotaon": "user quota is on", "repquota_user_rc": 0, "repquota_group_rc": 0, "active_user_quota": True, "active_group_quota": True},
            ),
            mock.patch("ssn.storage.shutil.which", return_value="/usr/sbin/tool"),
            mock.patch("ssn.storage._pwd_entry", return_value=None),
        ):
            report = quota_capability_report(users_doc, resolved, scope="all_managed")

        self.assertEqual(report["scope"], "all_managed")
        self.assertEqual(report["managed_users"], ["realuser", "ssn-test-quota"])
        self.assertTrue(all(not user["apply_allowed"] for user in report["users"]))

    def test_apply_fixture_quotas_uses_overrides_and_fixture_users_only(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-quota": {"status": "active", "tier": "standard"},
                "realuser": {"status": "active", "tier": "standard"},
            },
        }
        commands: list[list[str]] = []

        def fake_command_rc(command: list[str]) -> int:
            commands.append(command)
            return 0

        def fake_findmnt(path: str) -> dict[str, str]:
            return {"target": "/" if path == "/home" else path, "source": "dev", "fstype": "ext4", "options": "rw", "raw": "raw"}

        with (
            mock.patch("ssn.storage.os.geteuid", return_value=0),
            mock.patch("ssn.storage._user_exists", return_value=True),
            mock.patch("ssn.storage.command_rc", side_effect=fake_command_rc),
            mock.patch("ssn.storage._findmnt_info", side_effect=fake_findmnt),
            mock.patch("ssn.storage._find_fstab_entry", return_value={"options": "defaults"}),
            mock.patch(
                "ssn.storage._quota_active_for_mount",
                return_value={"quotaon": "user quota is on", "repquota_user_rc": 0, "repquota_group_rc": 0, "active_user_quota": True, "active_group_quota": True},
            ),
            mock.patch("ssn.storage.shutil.which", return_value="/usr/sbin/tool"),
        ):
            report = apply_fixture_quotas(
                users_doc,
                resolved,
                quota_overrides=parse_fixture_quota_overrides(["data=64MB"]),
            )

        self.assertTrue(all(action["user"] == "ssn-test-quota" for action in report["actions"]))
        self.assertIn(["setquota", "-u", "ssn-test-quota", "0", str(100 * 1024 * 1024), "0", "0", "/"], commands)
        self.assertIn(["setquota", "-u", "ssn-test-quota", "0", str(64 * 1024), "0", "0", "/data"], commands)

    def test_storage_quota_plan_has_risk_and_operation_hash(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        with (
            mock.patch("ssn.storage._findmnt_info", return_value={"target": "/scratch", "source": "dev", "fstype": "ext4", "options": "rw", "raw": "raw"}),
            mock.patch("ssn.storage._find_fstab_entry", return_value={"options": "defaults"}),
            mock.patch(
                "ssn.storage._quota_active_for_mount",
                return_value={"quotaon": None, "repquota_user_rc": 1, "repquota_group_rc": 1, "active_user_quota": False, "active_group_quota": False},
            ),
            mock.patch("ssn.storage.shutil.which", return_value="/usr/sbin/tool"),
        ):
            plan = storage_quota_plan(resolved, labels=["scratch"])
        self.assertEqual(plan["risk"], STORAGE_QUOTA_ENABLE_RISK)
        self.assertEqual(plan["operation_hash"], storage_quota_operation_hash(plan))
        self.assertTrue(plan["mounts"]["scratch"]["enable_needed"])

    def test_fstab_with_quota_options_preserves_comments_and_adds_options(self) -> None:
        before = "# header\nUUID=root / ext4 defaults 0 1\nUUID=data /data ext4 defaults,nofail 0 2\n"
        after, changed = _fstab_with_quota_options(before, ["/", "/data"])
        self.assertEqual(changed, ["/", "/data"])
        self.assertIn("UUID=root\t/\text4\tdefaults,usrquota,grpquota\t0\t1", after)
        self.assertIn("UUID=data\t/data\text4\tdefaults,nofail,usrquota,grpquota\t0\t2", after)

    def test_enable_storage_quotas_updates_fstab_and_runs_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fstab = Path(tmp) / "fstab"
            backup = Path(tmp) / "backup"
            fstab.write_text("UUID=scratch /scratch ext4 defaults 0 2\n")
            plan = {
                "schema_version": 1,
                "command": "storage-quotas",
                "profile": "unit",
                "config_hash": "hash",
                "mode": "plan",
                "risk": STORAGE_QUOTA_ENABLE_RISK,
                "fstab_path": str(fstab),
                "mount_labels": ["scratch"],
                "mounts": {
                    "scratch": {
                        "path": "/scratch",
                        "mountpoint": "/scratch",
                        "source": "dev",
                        "fstype": "ext4",
                        "fstab": {"options": "defaults"},
                        "proposed_options": "defaults,usrquota,grpquota",
                        "policy_quota": "128MB",
                        "enable_needed": True,
                        "can_enable": True,
                        "active_user_quota": False,
                        "active_group_quota": False,
                    }
                },
            }
            plan["operation_hash"] = storage_quota_operation_hash(plan)
            commands: list[list[str]] = []

            def fake_command_result(command: list[str]) -> dict[str, object]:
                commands.append(command)
                return {"cmd": command, "rc": 0, "stdout": "", "stderr": ""}

            with (
                mock.patch("ssn.storage.os.geteuid", return_value=0),
                mock.patch("ssn.storage.command_result", side_effect=fake_command_result),
                mock.patch(
                    "ssn.storage._quota_active_for_mount",
                    return_value={"active_user_quota": True, "active_group_quota": True, "quotaon": "on", "repquota_user_rc": 0, "repquota_group_rc": 0},
                ),
                mock.patch("ssn.storage._quota_mount_capability", return_value={"path": "/scratch", "policy_quota": "128MB"}),
            ):
                report = enable_storage_quotas(plan, fstab_path=fstab, backup_root=backup)

            self.assertEqual(report["status"], "enabled")
            self.assertIn("defaults,usrquota,grpquota", fstab.read_text())
            self.assertIn(["mount", "-o", "remount", "/scratch"], commands)
            self.assertIn(["quotacheck", "-cugm", "/scratch"], commands)
            self.assertIn(["quotaon", "-ug", "/scratch"], commands)

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

    def test_user_scratch_cleanup_requires_cache_or_tmp_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scratch"
            jobs = root / "jobs"
            old_file = root / "ssn-test-storage-cleanup" / "cache" / "old.bin"
            unsafe = root / "ssn-test-storage-cleanup" / "other" / "old.bin"
            old_file.parent.mkdir(parents=True)
            unsafe.parent.mkdir(parents=True)
            jobs.mkdir(parents=True)
            old_file.write_text("x")
            unsafe.write_text("x")
            report = {
                "schema_version": 1,
                "command": "scratch-cleanup",
                "mode": "report_only",
                "risk": SCRATCH_CLEANUP_RISK,
                "root": str(root),
                "jobs_root_excluded": str(jobs),
                "age_days": 30,
                "cleanup_users": ["ssn-test-storage-cleanup"],
                "candidates": [
                    {"path": str(old_file), "type": "file", "username": "ssn-test-storage-cleanup"},
                    {"path": str(unsafe), "type": "file", "username": "ssn-test-storage-cleanup"},
                ],
            }
            report["operation_hash"] = cleanup_operation_hash(report)
            with mock.patch("ssn.storage._user_has_active_slurm_job", return_value=False):
                applied = apply_user_scratch_cleanup(
                    report,
                    allowed_users={"ssn-test-storage-cleanup"},
                    require_scratch_root=False,
                )

            results = {Path(item["path"]).name + ":" + Path(item["path"]).parent.name: item["status"] for item in applied["deletion_results"]}
            self.assertEqual(results["old.bin:cache"], "deleted")
            self.assertEqual(results["old.bin:other"], "skipped")
            self.assertFalse(old_file.exists())
            self.assertTrue(unsafe.exists())


if __name__ == "__main__":
    unittest.main()
