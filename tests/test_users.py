from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest import mock

from ssn.config import resolve_profile
from ssn.users import (
    INACTIVE_ARCHIVE_RISK,
    INACTIVE_LOCAL_ONLY_RISK,
    _allowed_qos_for_tier,
    apply_user_actions,
    inactive_plan_report,
    inactive_prune_manifest,
    parse_authorized_key,
    plan_user_sync,
    validate_state,
    validate_users,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDK4gL+vIo0nZYQjH9hDq8qjZi8e75g4uT6pXKte9c7T"
AUTHORIZED_KEY = f"{PUBLIC_KEY} test@example"


class UserTests(unittest.TestCase):
    def test_authorized_key_options_are_preserved(self) -> None:
        parsed = parse_authorized_key(f'from="203.0.113.0/24",no-agent-forwarding {AUTHORIZED_KEY}')
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

    def test_validate_users_rejects_unknown_user_field(self) -> None:
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
                    "surprise": True,
                }
            },
        }
        errors = validate_users(users_doc, resolved)
        self.assertTrue(any("unknown keys" in error for error in errors))

    def test_validate_users_rejects_uid_conflict(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssntest": {
                    "status": "active",
                    "tier": "standard",
                    "uid": 0,
                    "groups": [],
                    "ssh_keys": None,
                }
            },
        }
        errors = validate_users(users_doc, resolved)
        self.assertTrue(any("uid conflicts" in error for error in errors))

    def test_validate_state_rejects_unknown_fields(self) -> None:
        errors = validate_state({"schema_version": 1, "users": {}, "extra": True})
        self.assertTrue(any("unknown top-level keys" in error for error in errors))

    def test_ssh_options_raw_mismatch_fails(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssntest": {
                    "status": "active",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": {
                        "laptop": {
                            "public_key": PUBLIC_KEY,
                            "options_raw": "no-agent-forwarding",
                            "options": {"no_x11_forwarding": True},
                        }
                    },
                }
            },
        }
        errors = validate_users(users_doc, resolved)
        self.assertTrue(any("options_raw disagrees" in error for error in errors))

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
        self.assertTrue(any(action.action == "reconcile_managed_groups" for action in actions))
        self.assertTrue(any(action.action == "ensure_slurm_association" for action in actions))

    def test_plan_active_user_is_noop_when_current_state_matches(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
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
        state_doc = {"schema_version": 1, "users": {"ssntest": {"managed": True}}}
        with (
            mock.patch("ssn.users._user_exists", return_value=True),
            mock.patch("ssn.users._account_needs_unlock", return_value=False),
            mock.patch("ssn.users._private_primary_group_matches", return_value=True),
            mock.patch("ssn.users._managed_groups_match", return_value=True),
            mock.patch("ssn.users._authorized_keys_match", return_value=True),
            mock.patch("ssn.users._user_data_dir_matches", return_value=True),
            mock.patch("ssn.users._user_scratch_dirs_match", return_value=True),
            mock.patch("ssn.users._slurm_association_matches", return_value=True),
            mock.patch("ssn.users._state_entry_matches", return_value=True),
        ):
            actions = plan_user_sync(users_doc, state_doc, resolved)
        self.assertEqual(actions, [])

    def test_plan_active_user_detects_only_authorized_key_drift(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssntest": {
                    "status": "active",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": {},
                }
            },
        }
        with (
            mock.patch("ssn.users._user_exists", return_value=True),
            mock.patch("ssn.users._account_needs_unlock", return_value=False),
            mock.patch("ssn.users._private_primary_group_matches", return_value=True),
            mock.patch("ssn.users._managed_groups_match", return_value=True),
            mock.patch("ssn.users._authorized_keys_match", return_value=False),
            mock.patch("ssn.users._user_data_dir_matches", return_value=True),
            mock.patch("ssn.users._slurm_association_matches", return_value=True),
            mock.patch("ssn.users._state_entry_matches", return_value=True),
        ):
            actions = plan_user_sync(users_doc, {"schema_version": 1, "users": {"ssntest": {"managed": True}}}, resolved)
        self.assertEqual([action.action for action in actions], ["sync_authorized_keys"])

    def test_plan_suspended_user_is_noop_when_already_disabled(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssntest": {
                    "status": "suspended",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": None,
                }
            },
        }
        with (
            mock.patch("ssn.users._user_exists", return_value=True),
            mock.patch("ssn.users._account_needs_lock", return_value=False),
            mock.patch("ssn.users._slurm_association_exists", return_value=False),
            mock.patch("ssn.users._user_has_slurm_jobs", return_value=False),
            mock.patch("ssn.users._state_entry_matches", return_value=True),
        ):
            actions = plan_user_sync(users_doc, {"schema_version": 1, "users": {"ssntest": {"managed": True}}}, resolved)
        self.assertEqual(actions, [])

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

    def test_allowed_qos_is_capped_by_tier_rank(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        self.assertEqual(_allowed_qos_for_tier("standard", resolved), "ssn-standard")
        self.assertEqual(_allowed_qos_for_tier("priority", resolved), "ssn-standard,ssn-priority")

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

    def test_inactive_plan_report_is_token_bindable(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-inactive": {
                    "status": "inactive",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": None,
                }
            },
        }
        state_doc = {
            "schema_version": 1,
            "users": {
                "ssn-test-inactive": {
                    "managed": True,
                    "status": "active",
                    "uid": 2401,
                    "gid": 2401,
                    "original_uid": 2401,
                    "original_gid": 2401,
                }
            },
        }
        with (
            mock.patch("ssn.users._current_uid", return_value=2401),
            mock.patch("ssn.users._current_gid", return_value=2401),
            mock.patch("ssn.users._current_home", return_value="/home/ssn-test-inactive"),
            mock.patch("ssn.users._backup_hook_report", return_value={"directory": "/hooks", "required": True, "executable_hooks": []}),
        ):
            actions = plan_user_sync(users_doc, state_doc, resolved)
            report = inactive_plan_report(actions, users_doc, state_doc, resolved)
            report_again = inactive_plan_report(actions, users_doc, state_doc, resolved)
        self.assertEqual(report["command"], "sync-users")
        self.assertEqual(report["risk"], INACTIVE_ARCHIVE_RISK)
        self.assertIn(INACTIVE_LOCAL_ONLY_RISK, report["risks"])
        self.assertEqual(report["operation_hash"], report_again["operation_hash"])
        self.assertEqual(report["inactive_users"][0]["username"], "ssn-test-inactive")
        self.assertTrue(report["inactive_users"][0]["fixture_apply_allowed"])
        self.assertTrue(report["inactive_users"][0]["backup_required"])

    def test_inactive_backup_required_blocks_without_hook_before_mutation(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "ssn-test-prod": {
                    "status": "inactive",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": None,
                }
            },
        }
        actions = plan_user_sync(users_doc, {"schema_version": 1, "users": {}}, resolved)
        with (
            mock.patch("os.geteuid", return_value=0),
            mock.patch("ssn.users._user_exists", return_value=True),
            mock.patch("ssn.users._archive_compressor", return_value="/usr/bin/7zz"),
            mock.patch("ssn.users._backup_hook_report", return_value={"directory": "/hooks", "required": True, "executable_hooks": []}),
            mock.patch("ssn.users._run") as run_mock,
            self.assertRaisesRegex(ValueError, "requires an executable backup hook"),
        ):
            apply_user_actions(
                actions,
                users_doc,
                resolved,
                state_doc={"schema_version": 1, "users": {}},
                allow_inactive_fixture=True,
            )
        run_mock.assert_not_called()

    def test_inactive_prune_manifest_is_allowlisted_and_symlink_safe(self) -> None:
        resolved = resolve_profile("gpu-bisect-quadro-p620", ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".cache").mkdir(parents=True)
            (home / ".conda" / "pkgs").mkdir(parents=True)
            (home / "project" / ".venv").mkdir(parents=True)
            (home / "project" / ".venv" / "pyvenv.cfg").write_text("")
            (home / "project" / "node_modules").mkdir(parents=True)
            (home / ".pixi").mkdir(parents=True)
            (home / ".pixi" / "envs").symlink_to(Path(tmp) / "outside")
            manifest = inactive_prune_manifest(home, resolved)
        delete_paths = {entry["relative_path"]: entry for entry in manifest["delete_candidates"]}
        report_only = {entry["relative_path"] for entry in manifest["report_only"]}
        self.assertIn(".cache", delete_paths)
        self.assertIn(".conda/pkgs", delete_paths)
        self.assertIn("project/.venv", delete_paths)
        self.assertEqual(delete_paths[".pixi/envs"]["type"], "symlink")
        self.assertEqual(delete_paths[".pixi/envs"]["symlink_action"], "remove_link_only")
        self.assertIn("project/node_modules", report_only)

    def test_inactive_apply_is_fixture_limited(self) -> None:
        resolved = resolve_profile("cpu-dev-local", ROOT)
        users_doc = {
            "schema_version": 1,
            "groups": {},
            "users": {
                "someone": {
                    "status": "inactive",
                    "tier": "standard",
                    "groups": [],
                    "ssh_keys": None,
                }
            },
        }
        actions = plan_user_sync(users_doc, {"schema_version": 1, "users": {}}, resolved)
        with (
            mock.patch("os.geteuid", return_value=0),
            self.assertRaisesRegex(ValueError, "fixture-limited"),
        ):
            apply_user_actions(
                actions,
                users_doc,
                resolved,
                state_doc={"schema_version": 1, "users": {}},
                allow_inactive_fixture=True,
            )


if __name__ == "__main__":
    unittest.main()
