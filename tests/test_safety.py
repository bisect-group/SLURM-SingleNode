from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from ssn.safety import mask_email, redact_for_plan, retention_candidates


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


if __name__ == "__main__":
    unittest.main()
