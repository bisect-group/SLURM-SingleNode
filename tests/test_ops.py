from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from ssn.ops import create_plan_token, validate_plan_token


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


if __name__ == "__main__":
    unittest.main()
