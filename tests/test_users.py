from __future__ import annotations

import unittest
from pathlib import Path

from ssn.config import resolve_profile
from ssn.users import parse_authorized_key, plan_user_sync, validate_users


ROOT = Path(__file__).resolve().parents[1]
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDK4gL+vIo0nZYQjH9hDq8qjZi8e75g4uT6pXKte9c7T test@example"


class UserTests(unittest.TestCase):
    def test_authorized_key_options_are_preserved(self) -> None:
        parsed = parse_authorized_key(f'from="203.0.113.0/24",no-agent-forwarding {KEY}')
        assert parsed is not None
        self.assertEqual(parsed["options_raw"], 'from="203.0.113.0/24",no-agent-forwarding')
        self.assertEqual(parsed["options"]["from"], ["203.0.113.0/24"])
        self.assertTrue(parsed["options"]["no_agent_forwarding"])
        self.assertTrue(parsed["fingerprint"].startswith("SHA256:"))

    def test_validate_users_rejects_group_members(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {"lab": {"members": ["alice"]}},
            "users": {},
        }
        errors = validate_users(users_doc, resolved)
        self.assertTrue(any("members is not allowed" in error for error in errors))

    def test_plan_active_user(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {"lab": {"description": "Lab"}},
            "users": {
                "ssntest": {
                    "status": "active",
                    "tier": "standard",
                    "groups": ["lab"],
                    "ssh_keys": {},
                }
            },
        }
        errors = validate_users(users_doc, resolved)
        self.assertEqual(errors, [])
        actions = plan_user_sync(users_doc, {"schema_version": 1, "users": {}}, resolved)
        self.assertTrue(any(action.action == "create_unix_user" for action in actions))
        self.assertTrue(any(action.action == "ensure_slurm_association" for action in actions))

    def test_gpu_profile_active_user_gets_scratch_dir_action(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssntest": {
                    "status": "active",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": None,
                }
            },
        }
        actions = plan_user_sync(users_doc, {"schema_version": 1, "users": {}}, resolved)
        self.assertTrue(any(action.action == "ensure_scratch_dir" for action in actions))

    def test_inactive_reactivation_requires_original_identity(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssntest": {
                    "status": "active",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": None,
                    "uid": 2001,
                    "gid": 2001,
                }
            },
        }
        state_doc = {
            "schema_version": 1,
            "users": {
                "ssntest": {
                    "managed": True,
                    "status": "inactive",
                    "original_uid": 2000,
                    "original_gid": 2000,
                }
            },
        }
        actions = plan_user_sync(users_doc, state_doc, resolved)
        self.assertTrue(any(action.action == "validation_error" for action in actions))


if __name__ == "__main__":
    unittest.main()
