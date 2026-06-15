from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from ssn.safety import (
    ALLOWED_RETENTION_ROOTS,
    apply_retention_cleanup,
    apply_test_retention_cleanup,
    mask_email,
    redact_for_plan,
    retention_candidates,
    retention_cleanup_report,
    retention_operation_hash,
)


class SafetyTests(unittest.TestCase):
    def test_redact_for_plan_hides_secrets_and_fingerprints_keys(self) -> None:
        public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDK4gL+vIo0nZYQjH9hDq8qjZi8e75g4uT6pXKte9c7T"
        data = {
            "db_password": "secret",
            "email": "person@example.com",
            "public_key": public_key,
        }
        redacted = redact_for_plan(data, terminal=True)
        self.assertEqual(redacted["db_password"], "[REDACTED]")
        self.assertEqual(redacted["email"], "p***@example.com")
        self.assertTrue(redacted["public_key"].startswith("SHA256:"))

    def test_mask_email_without_at_is_unchanged(self) -> None:
        self.assertEqual(mask_email("not-an-email"), "not-an-email")

    def test_retention_candidates_is_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old"
            path.write_text("x")
            old_time = time.time() - 10 * 86400
            os.utime(path, (old_time, old_time))
            candidates = retention_candidates(tmp, older_than_days=1)
            self.assertEqual(len(candidates), 1)
            self.assertTrue(path.exists())

    def test_retention_cleanup_deletes_only_test_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_path = root / "ssn-test-retention-old"
            real_path = root / "install-20200101000000"
            test_path.mkdir()
            real_path.mkdir()
            old_time = time.time() - 10 * 86400
            os.utime(test_path, (old_time, old_time))
            os.utime(real_path, (old_time, old_time))
            report = retention_cleanup_report(root, older_than_days=1)
            applied = apply_test_retention_cleanup(report)
            results = {Path(item["path"]).name: item["status"] for item in applied["deletion_results"]}
            self.assertEqual(results["ssn-test-retention-old"], "deleted")
            self.assertEqual(results["install-20200101000000"], "skipped")
            self.assertFalse(test_path.exists())
            self.assertTrue(real_path.exists())

    def test_retention_cleanup_skips_symlink_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside_root = Path(tmp) / "outside"
            root.mkdir()
            outside_root.mkdir()
            target = outside_root / "ssn-test-retention-target"
            link = root / "ssn-test-retention-link"
            target.mkdir()
            link.symlink_to(target, target_is_directory=True)
            old_time = time.time() - 10 * 86400
            os.utime(target, (old_time, old_time), follow_symlinks=False)
            os.utime(link, (old_time, old_time), follow_symlinks=False)
            report = retention_cleanup_report(root, older_than_days=1)
            applied = apply_test_retention_cleanup(report)
            results = {Path(item["path"]).name: item for item in applied["deletion_results"]}
            self.assertEqual(results["ssn-test-retention-link"]["status"], "skipped")
            self.assertEqual(results["ssn-test-retention-link"]["reason"], "symlink")
            self.assertTrue(link.is_symlink())
            self.assertTrue(target.exists())

    def test_retention_cleanup_skips_candidate_outside_report_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside_root = Path(tmp) / "outside"
            root.mkdir()
            outside_root.mkdir()
            outside = outside_root / "ssn-test-retention-outside"
            outside.mkdir()
            old_time = time.time() - 10 * 86400
            os.utime(outside, (old_time, old_time))
            report = retention_cleanup_report(root, older_than_days=1)
            report["candidates"] = [{"path": str(outside), "mtime": "old", "type": "directory"}]
            report["operation_hash"] = retention_operation_hash(report)
            applied = apply_test_retention_cleanup(report)
            self.assertEqual(applied["deletion_results"][0]["status"], "skipped")
            self.assertEqual(applied["deletion_results"][0]["reason"], "outside retention root")
            self.assertTrue(outside.exists())

    def test_retention_cleanup_production_root_requires_explicit_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plans"
            root.mkdir()
            old_plan = root / "install-20200101000000"
            old_plan.mkdir()
            old_time = time.time() - 10 * 86400
            os.utime(old_plan, (old_time, old_time))
            report = retention_cleanup_report(root, older_than_days=1)
            report["root"] = "/var/lib/slurm-single-node/plans"
            report["candidates"][0]["path"] = "/var/lib/slurm-single-node/plans/install-20200101000000"
            report["operation_hash"] = retention_operation_hash(report)

            with self.assertRaisesRegex(ValueError, "requires --allow-production-roots"):
                apply_retention_cleanup(report)

    def test_retention_cleanup_production_root_allows_known_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "plans"
            fake_root.mkdir()
            old_plan = fake_root / "install-20200101000000"
            old_plan.mkdir()
            old_time = time.time() - 10 * 86400
            os.utime(old_plan, (old_time, old_time))
            report = retention_cleanup_report(fake_root, older_than_days=1)
            report["root"] = str(fake_root)
            ALLOWED_RETENTION_ROOTS[str(fake_root)] = ("install-",)
            report["operation_hash"] = retention_operation_hash(report)
            try:
                applied = apply_retention_cleanup(report, allow_production_roots=True)
            finally:
                ALLOWED_RETENTION_ROOTS.pop(str(fake_root), None)

            self.assertEqual(applied["deletion_results"][0]["status"], "deleted")
            self.assertFalse(old_plan.exists())


if __name__ == "__main__":
    unittest.main()
