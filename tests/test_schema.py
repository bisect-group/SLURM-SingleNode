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


if __name__ == "__main__":
    unittest.main()
