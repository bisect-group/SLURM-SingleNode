from __future__ import annotations

import unittest

from ssn.schema import validate_policy_file_schema, validate_profile_schema


class SchemaTests(unittest.TestCase):
    def test_profile_schema_rejects_nested_unknown_field(self) -> None:
        errors = validate_profile_schema(
            "bad",
            {
                "schema_version": 1,
                "profile": "bad",
                "identity": {"cluster_name": "c", "node_name": "n", "surprise": True},
            },
        )
        self.assertTrue(any("unknown key 'surprise'" in error for error in errors))

    def test_profile_schema_allows_x_escape(self) -> None:
        errors = validate_profile_schema(
            "ok",
            {
                "schema_version": 1,
                "profile": "ok",
                "x_notes": {"anything": True},
                "identity": {"x_local": {"free": "form"}},
            },
        )
        self.assertEqual(errors, [])

    def test_policy_schema_rejects_unknown_field(self) -> None:
        errors = validate_policy_file_schema(
            "cache",
            {
                "schema_version": 1,
                "policies": {
                    "bad-cache": {
                        "requires": {"scratch": False},
                        "surprise": True,
                    }
                },
            },
        )
        self.assertTrue(any("unknown key 'surprise'" in error for error in errors))

    def test_review_required_map_field_is_allowed_by_schema(self) -> None:
        errors = validate_profile_schema(
            "review",
            {
                "schema_version": 1,
                "profile": "review",
                "hardware": {
                    "gpu_affinity": {
                        "cores": "REVIEW_REQUIRED",
                    }
                },
            },
        )
        self.assertEqual(errors, [])

    def test_profile_schema_requires_schema_version_one(self) -> None:
        errors = validate_profile_schema("bad", {"schema_version": "1", "profile": "bad"})
        self.assertTrue(any("schema_version: 1" in error or "must be schema_version" in error for error in errors))

    def test_complete_profile_schema_requires_merged_fields(self) -> None:
        errors = validate_profile_schema("bad", {"schema_version": 1, "profile": "bad"}, complete=True)
        self.assertTrue(any("missing required key identity.cluster_name" in error for error in errors))

    def test_profile_schema_rejects_wrong_container_type(self) -> None:
        errors = validate_profile_schema(
            "bad",
            {
                "schema_version": 1,
                "profile": "bad",
                "admins": {"users": "root"},
            },
        )
        self.assertTrue(any("admins.users must be list" in error for error in errors))

    def test_review_required_is_rejected_outside_review_fields(self) -> None:
        errors = validate_profile_schema(
            "bad",
            {
                "schema_version": 1,
                "profile": "bad",
                "services": {"munge": {"auto_rotate": "REVIEW_REQUIRED"}},
            },
        )
        self.assertTrue(any("may not be REVIEW_REQUIRED" in error for error in errors))

    def test_policy_schema_rejects_invalid_domain(self) -> None:
        errors = validate_policy_file_schema("nope", {"schema_version": 1, "policies": {}})
        self.assertTrue(any("unknown policy domain" in error for error in errors))

    def test_policy_schema_requires_policy_map(self) -> None:
        errors = validate_policy_file_schema("cache", {"schema_version": 1, "policies": []})
        self.assertTrue(any("policy cache.policies must be a map" in error for error in errors))

    def test_policy_schema_rejects_wrong_primitive_type(self) -> None:
        errors = validate_policy_file_schema(
            "cache",
            {
                "schema_version": 1,
                "policies": {
                    "bad-cache": {
                        "requires": {"scratch": "yes"},
                        "injection": {
                            "login_shells": True,
                            "slurm_jobs": True,
                            "mode": "default_only",
                            "slurm_job_temp_override": "none",
                        },
                        "roots": {},
                        "env": {},
                    }
                },
            },
        )
        self.assertTrue(any("requires.scratch must be bool" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
