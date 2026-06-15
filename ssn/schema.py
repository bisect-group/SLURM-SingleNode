from __future__ import annotations

from typing import Any


ANY = "*"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


PROFILE_RAW_REQUIRED = [
    ("schema_version",),
    ("profile",),
]

PROFILE_COMPLETE_REQUIRED = [
    ("schema_version",),
    ("profile",),
    ("identity", "cluster_name"),
    ("identity", "node_name"),
    ("identity", "command_prefix"),
    ("identity", "group_prefix"),
    ("identity", "default_partition"),
    ("admins", "users"),
    ("admins", "groups"),
    ("services", "munge", "key_mode"),
    ("services", "munge", "auto_rotate"),
    ("services", "munge", "backup"),
    ("services", "munge", "validate_permissions"),
    ("services", "munge", "require_service_healthy"),
    ("hardware", "gpus"),
    ("hardware", "gpu_vendor"),
    ("hardware", "cpus_total"),
    ("hardware", "cpus_allocatable"),
    ("hardware", "memory_total"),
    ("hardware", "memory_allocatable"),
    ("hardware", "reserved_cpus"),
    ("hardware", "reserved_memory"),
    ("hardware", "memory_defaults", "def_mem_per_cpu"),
    ("hardware", "memory_defaults", "max_mem_per_cpu"),
    ("policies", "slurm_core"),
    ("policies", "tiers"),
    ("policies", "storage"),
    ("policies", "cache"),
    ("policies", "modules"),
    ("policies", "login"),
    ("operations", "capability_checks", "ubuntu"),
    ("operations", "capability_checks", "slurm"),
    ("operations", "capability_checks", "database"),
    ("operations", "capability_checks", "gpu"),
    ("operations", "plan_artifacts", "root"),
    ("operations", "plan_artifacts", "retention"),
    ("operations", "plan_artifacts", "owner"),
    ("operations", "plan_artifacts", "group"),
    ("operations", "plan_artifacts", "directory_mode"),
    ("operations", "plan_artifacts", "file_mode"),
    ("operations", "plan_artifacts", "risky_apply_requires_token"),
    ("operations", "plan_artifacts", "token_policy", "redacted"),
    ("operations", "plan_artifacts", "token_policy", "input_hash_bound"),
    ("operations", "plan_artifacts", "token_policy", "expires_after"),
    ("operations", "plan_artifacts", "token_policy", "single_use"),
    ("operations", "reconcile", "model"),
    ("operations", "reconcile", "resume_partial_transitions"),
    ("operations", "reconcile", "rollback"),
    ("operations", "backups", "users_yml", "root"),
    ("operations", "backups", "users_yml", "retention"),
    ("operations", "backups", "users_state_yml", "root"),
    ("operations", "backups", "users_state_yml", "retention"),
    ("operations", "gpu_verification", "run_after_boot"),
    ("operations", "gpu_verification", "run_after_apply"),
    ("operations", "gpu_verification", "health_gate"),
]

PROFILE_GPU_REQUIRED = [
    ("hardware", "gpu_modes", "mig"),
    ("hardware", "gpu_modes", "mps"),
    ("hardware", "gpu_modes", "shared_gpu"),
    ("hardware", "gpu_affinity", "mode"),
    ("hardware", "gpu_affinity", "render_gres_cores"),
    ("hardware", "gpu_affinity", "cores"),
]

POLICY_REQUIRED: dict[str, list[tuple[str, ...]]] = {
    "cache": [
        ("requires", "scratch"),
        ("injection", "login_shells"),
        ("injection", "slurm_jobs"),
        ("injection", "mode"),
        ("injection", "slurm_job_temp_override"),
        ("roots",),
        ("env",),
    ],
    "login": [
        ("cgroup",),
        ("non_admin_limits", "scope"),
        ("non_admin_limits", "cpus"),
        ("non_admin_limits", "memory"),
        ("non_admin_limits", "tasks"),
        ("non_admin_limits", "io_weight"),
        ("non_admin_limits", "applies_to_slurm_jobs"),
        ("non_admin_limits", "slurm_job_cgroups_owned_by_slurm"),
        ("admins_exempt",),
        ("remote_ides",),
        ("gpu_outside_slurm", "direct_access"),
        ("gpu_outside_slurm", "enforcement"),
        ("gpu_outside_slurm", "applies_to"),
        ("gpu_outside_slurm", "slurm_jobs_receive_allocated_devices"),
        ("gpu_outside_slurm", "fail_closed_if_unavailable"),
        ("gpu_outside_slurm", "friendly_path_wrappers"),
        ("gpu_outside_slurm", "status_wrapper"),
        ("gpu_outside_slurm", "status_collector"),
        ("gpu_outside_slurm", "refresh"),
    ],
    "modules": [
        ("roots", "apps"),
        ("roots", "modules"),
        ("roots", "containers"),
        ("lmod",),
        ("updates", "mode"),
        ("updates", "versioned_roots"),
        ("updates", "smoke_checks_required"),
        ("updates", "rollback_targets"),
        ("updates", "unattended_updates"),
        ("shared_env_base", "type"),
        ("shared_env_base", "root"),
        ("cuda", "toolkit_mode"),
        ("cuda", "managed_updates"),
        ("cuda", "modules", "default"),
        ("cuda", "modules", "versioned"),
        ("cuda", "smoke_checks", "module_load_unload"),
        ("cuda", "smoke_checks", "nvcc_version_if_present"),
        ("cuda", "smoke_checks", "nvidia_smi"),
        ("cuda", "smoke_checks", "library_path_sanity"),
        ("cuda", "smoke_checks", "optional_sample_compile_run"),
        ("apptainer", "enabled"),
        ("apptainer", "container_root"),
    ],
    "slurm_core": [
        ("select", "type"),
        ("select", "parameters"),
        ("select", "consumable", "cpu"),
        ("select", "consumable", "memory"),
        ("select", "consumable", "gres"),
        ("task_plugin", "cgroup_v2"),
        ("task_plugin", "constrain_cores"),
        ("task_plugin", "constrain_ram"),
        ("task_plugin", "constrain_devices"),
        ("accounting", "storage_tres"),
        ("accounting", "gpu_tres_when_available"),
        ("memory", "default_mode"),
        ("memory", "def_mem_per_cpu"),
        ("memory", "max_mem_per_cpu"),
        ("memory", "reserve_base"),
        ("memory", "tier_max_percent_enforced"),
        ("memory", "render_qos_max_tres_mem"),
        ("submit_filter", "plugin"),
        ("submit_filter", "mode"),
        ("submit_filter", "client_filter", "enabled"),
        ("submit_filter", "client_filter", "plugin"),
        ("submit_filter", "client_filter", "path"),
        ("submit_filter", "reject"),
        ("submit_filter", "disallow_slow_io"),
        ("submit_filter", "disallow_runtime_state_checks"),
        ("submit_filter", "final_enforcement"),
    ],
    "storage": [
        ("paths", "home"),
        ("paths", "data"),
        ("paths", "scratch"),
        ("paths", "archive"),
        ("durability", "data"),
        ("durability", "archive"),
        ("durability", "active_data_backup"),
        ("durability", "nondurable_ack_required"),
        ("quotas", "home"),
        ("quotas", "data"),
        ("quotas", "scratch"),
        ("quotas", "fail_if_unavailable"),
        ("job_scratch", "implementation"),
        ("job_scratch", "required_for_jobs"),
        ("job_scratch", "root"),
        ("inactive_archive", "requires_archive_root"),
        ("inactive_archive", "service_identity"),
        ("inactive_archive", "slurm_account"),
        ("inactive_archive", "qos"),
        ("inactive_archive", "preemptible"),
        ("inactive_archive", "outside_user_limits"),
        ("inactive_archive", "monitored_by"),
        ("inactive_archive", "user_account_until_success"),
        ("inactive_archive", "removal_requires_backup_success"),
        ("inactive_archive", "removal_override"),
        ("inactive_archive", "backup_hook", "required_for_durability"),
        ("inactive_archive", "backup_hook", "directory"),
        ("inactive_archive", "backup_hook", "missing_hook_action"),
        ("inactive_archive", "backup_hook", "local_only_override"),
        ("inactive_archive", "slurm_unavailable"),
        ("inactive_archive", "external_backup_required_for_durability"),
        ("inactive_archive", "state_substates"),
        ("inactive_archive", "compression"),
        ("inactive_archive", "apply_requires_plan_token"),
        ("inactive_archive", "prune_manifest"),
        ("inactive_archive", "symlinks"),
        ("inactive_archive", "delete_fixed_paths"),
        ("inactive_archive", "recursive_marker_rules"),
        ("inactive_archive", "report_only_names"),
    ],
    "tiers": [
        ("slurm_account",),
        ("default_tier",),
        ("memory_percent_base",),
        ("fairshare", "priority_type"),
        ("fairshare", "priority_decay_half_life"),
        ("fairshare", "priority_weight_fairshare"),
        ("fairshare", "priority_weight_age"),
        ("fairshare", "priority_weight_qos"),
        ("fairshare", "account_user_shares"),
        ("fairshare", "tres_billing_weights"),
        ("preemption", "type"),
        ("preemption", "mode"),
        ("preemption", "grace_time"),
        ("preemption", "job_requeue"),
        ("preemption", "allow_user_no_requeue"),
        ("preemption", "submit_rejects_no_requeue"),
        ("preemption", "interactive_jobs", "allowed"),
        ("preemption", "interactive_jobs", "on_preempt"),
        ("preemption", "relationships"),
        ("tiers",),
    ],
}


def unknown_keys(data: dict[str, Any], allowed: set[str]) -> set[str]:
    return {key for key in data if key not in allowed and not str(key).startswith("x_")}


def validate_schema(value: Any, schema: Any, path: str) -> list[str]:
    if value == REVIEW_REQUIRED:
        if _review_required_allowed(path):
            return []
        return [f"{path} may not be REVIEW_REQUIRED"]
    if _is_x_path(path):
        return []
    if schema is None:
        return []
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return [f"{path} must be a map"]
        errors: list[str] = []
        allowed = {key for key in schema if key != ANY}
        for key, item in value.items():
            key = str(key)
            if key.startswith("x_"):
                continue
            if key in schema:
                errors.extend(validate_schema(item, schema[key], f"{path}.{key}"))
            elif ANY in schema:
                errors.extend(validate_schema(item, schema[ANY], f"{path}.{key}"))
            else:
                errors.append(f"{path} has unknown key {key!r}")
        return errors
    return []


def validate_types(value: Any, path: str) -> list[str]:
    if _is_x_path(path):
        return []
    if value == REVIEW_REQUIRED:
        if _review_required_allowed(path):
            return []
        return [f"{path} may not be REVIEW_REQUIRED"]

    errors: list[str] = []
    expected = _expected_type(path)
    if expected and not _matches_type(value, expected):
        errors.append(f"{path} must be {expected}")
        return errors

    if isinstance(value, dict):
        for key, item in value.items():
            errors.extend(validate_types(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(validate_types(item, f"{path}[{index}]"))
    return errors


def validate_required(data: Any, required: list[tuple[str, ...]], path: str) -> list[str]:
    errors: list[str] = []
    for key_path in required:
        current = data
        missing = False
        for key in key_path:
            if not isinstance(current, dict) or key not in current:
                missing = True
                break
            current = current[key]
        if missing:
            errors.append(f"{path} is missing required key {'.'.join(key_path)}")
    return errors


def validate_schema_version(data: dict[str, Any], path: str) -> list[str]:
    if data.get("schema_version") != 1:
        return [f"{path} must use schema_version: 1"]
    return []


def _is_x_path(path: str) -> bool:
    return any(part.startswith("x_") for part in path.replace("[", ".").split("."))


def _review_required_allowed(path: str) -> bool:
    allowed_suffixes = {
        ".identity.node_name",
        ".hardware.gpus",
        ".hardware.gpu_type",
        ".hardware.gpu_affinity.cores",
        ".hardware.cpus_total",
        ".hardware.cpus_allocatable",
        ".hardware.memory_total",
        ".hardware.memory_allocatable",
        ".hardware.memory_defaults.def_mem_per_cpu",
        ".hardware.memory_defaults.max_mem_per_cpu",
        ".memory.def_mem_per_cpu",
        ".memory.max_mem_per_cpu",
    }
    if any(path.endswith(suffix) for suffix in allowed_suffixes):
        return True
    parts = path.split(".")
    if len(parts) >= 2 and parts[-1] in {"max_cpus_per_job", "max_gpus_per_job"} and "tiers" in parts:
        return True
    return False


def _expected_type(path: str) -> str | None:
    clean = path.replace("[", ".").replace("]", "")
    parts = clean.split(".")
    last = parts[-1]
    suffix2 = ".".join(parts[-2:])
    suffix3 = ".".join(parts[-3:])

    if last == "schema_version":
        return "schema_version"
    if path.startswith("profile ") and suffix2.startswith("policies."):
        return "str_or_null"
    if path.startswith("profile ") and suffix3.startswith("overrides.policies."):
        return "str_or_null"
    if path.endswith(".hardware.gpu_affinity.render_gres_cores"):
        return "str_or_null"
    if suffix2 == "requires.scratch":
        return "bool"
    if suffix3 == "select.consumable.memory":
        return "bool"
    if last == "tres_billing_weights":
        return "map"
    if "tres_billing_weights" in parts:
        return "number"
    if path.startswith("policy slurm_core") and last == "memory":
        return "map"
    if suffix2 == "non_admin_limits.memory":
        return "scalar_or_null"
    if suffix2 == "paths.scratch":
        return "scalar_or_null"
    if suffix2 == "quotas.scratch":
        return "scalar_or_null"
    if suffix2 == "quotas.data_scratch_capacity_isolation":
        return "str_or_null"
    if suffix2 == "roots.home_config":
        return "scalar_or_null"
    if suffix2 == "roots.modules":
        return "scalar_or_null"
    if suffix2 == "env.home_config":
        return "map"
    if suffix2 == "cuda.modules":
        return "map"
    if suffix2 == "scratch_cleanup.report":
        return "bool"
    if suffix2 in {"runtime_failure.mark_scratch_unhealthy", "runtime_failure.block_new_jobs"}:
        return "bool"

    if last in {
        "auto_rotate",
        "validate_permissions",
        "require_service_healthy",
        "login_shells",
        "slurm_jobs",
        "lmod",
        "versioned_roots",
        "smoke_checks_required",
        "unattended_updates",
        "module_load_unload",
        "nvcc_version_if_present",
        "nvidia_smi",
        "library_path_sanity",
        "optional_sample_compile_run",
        "enabled",
        "cpu",
        "gres",
        "cgroup_v2",
        "constrain_cores",
        "constrain_ram",
        "constrain_devices",
        "gpu_tres_when_available",
        "tier_max_percent_enforced",
        "render_qos_max_tres_mem",
        "disallow_slow_io",
        "disallow_runtime_state_checks",
        "job_requeue",
        "allow_user_no_requeue",
        "submit_rejects_no_requeue",
        "allowed",
        "nondurable_ack_required",
        "fail_if_unavailable",
        "same_pool_allowed_with_project_quotas",
        "exclude_job_scratch",
        "exclude_active_jobs",
        "required_for_jobs",
        "fallback_temp",
        "avoid_node_drain_where_slurm_permits",
        "requires_archive_root",
        "preemptible",
        "outside_user_limits",
        "removal_requires_backup_success",
        "required_for_durability",
        "external_backup_required_for_durability",
        "apply_requires_plan_token",
        "prune_manifest",
        "risky_apply_requires_token",
        "redacted",
        "input_hash_bound",
        "single_use",
        "resume_partial_transitions",
        "run_after_boot",
        "run_after_apply",
        "health_gate",
        "nondurable_data",
        "admins_exempt",
        "applies_to_slurm_jobs",
        "slurm_job_cgroups_owned_by_slurm",
        "slurm_jobs_receive_allocated_devices",
        "fail_closed_if_unavailable",
        "friendly_path_wrappers",
    }:
        return "bool"

    if last in {
        "gpus",
        "cpus_total",
        "cpus_allocatable",
        "reserved_cpus",
        "priority_weight_fairshare",
        "priority_weight_age",
        "priority_weight_qos",
        "max_cpus_per_job",
        "max_running_jobs",
        "max_submitted_jobs",
        "memory_percent",
        "preempt_rank",
        "cpus",
        "tasks",
    }:
        return "int"

    if last in {
        "users",
        "groups",
        "compatibility_aliases",
        "storage_tres",
        "reject",
        "checks",
        "preflight_checks",
        "task_prolog_exports",
        "state_substates",
        "delete_fixed_paths",
        "report_only_names",
        "preempts",
    }:
        return "list"

    if last in {
        "identity",
        "admins",
        "services",
        "munge",
        "hardware",
        "gpu_modes",
        "gpu_affinity",
        "memory_defaults",
        "policies",
        "operations",
        "capability_checks",
        "plan_artifacts",
        "token_policy",
        "redaction",
        "emails",
        "sensitive_manifests",
        "reconcile",
        "backups",
        "users_yml",
        "users_state_yml",
        "gpu_verification",
        "recovery",
        "storage_acknowledgements",
        "overrides",
        "requires",
        "injection",
        "roots",
        "env",
        "scratch",
        "persistent",
        "home_config",
        "non_admin_limits",
        "gpu_outside_slurm",
        "updates",
        "shared_env_base",
        "cuda",
        "modules",
        "smoke_checks",
        "apptainer",
        "select",
        "consumable",
        "task_plugin",
        "accounting",
        "submit_filter",
        "paths",
        "durability",
        "quotas",
        "scratch_cleanup",
        "job_scratch",
        "runtime_failure",
        "inactive_archive",
        "backup_hook",
        "recursive_marker_rules",
        "fairshare",
        "tres_billing_weights",
        "preemption",
        "interactive_jobs",
        "relationships",
        "tiers",
    }:
        return "map"

    if suffix2 in {"memory.total", "memory.allocatable"}:
        return None
    if last in {
        "memory_total",
        "memory_allocatable",
        "reserved_memory",
        "def_mem_per_cpu",
        "max_mem_per_cpu",
        "home",
        "data",
        "scratch",
        "archive",
        "root",
        "age",
        "memory",
        "retention",
        "expires_after",
        "grace_time",
        "default_walltime",
        "max_walltime",
        "refresh",
        "home",
        "data",
        "scratch",
    }:
        return "scalar_or_null"

    if last in {
        "profile",
        "extends",
        "cluster_name",
        "node_name",
        "command_prefix",
        "group_prefix",
        "default_partition",
        "key_mode",
        "backup",
        "discovered_at",
        "gpu_vendor",
        "gpu_type",
        "mig",
        "mps",
        "shared_gpu",
        "mode",
        "ubuntu",
        "slurm",
        "database",
        "gpu",
        "owner",
        "group",
        "directory_mode",
        "file_mode",
        "secrets",
        "ssh_keys",
        "terminal",
        "root_json",
        "model",
        "rollback",
        "health_gate",
        "on_failure",
        "type",
        "running_gpu_jobs",
        "pending_gpu_jobs",
        "gpu_gres",
        "cpu_jobs",
        "scratch_cache",
        "persistent_cache",
        "persistent_cache_fallback",
        "home_config",
        "scratch_tmp",
        "slurm_job_temp_override",
        "toolkit_mode",
        "managed_updates",
        "default",
        "versioned",
        "apps",
        "containers",
        "parameters",
        "default_mode",
        "reserve_base",
        "plugin",
        "final_enforcement",
        "active_data_backup",
        "implementation",
        "report",
        "unhealthy_action",
        "affected_job",
        "mark_scratch_unhealthy",
        "block_new_jobs",
        "fallback_temp",
        "env_var",
        "create_with",
        "export_with",
        "cleanup_with",
        "job_container_tmpfs",
        "service_identity",
        "slurm_account",
        "qos",
        "monitored_by",
        "user_account_until_success",
        "removal_override",
        "missing_hook_action",
        "local_only_override",
        "directory",
        "slurm_unavailable",
        "compression",
        "symlinks",
        "marker_file",
        "default_tier",
        "memory_percent_base",
        "priority_type",
        "priority_decay_half_life",
        "account_user_shares",
        "on_preempt",
        "direct_access",
        "enforcement",
        "applies_to",
        "status_wrapper",
        "status_collector",
        "io_weight",
        "remote_ides",
        "cgroup",
        "rollback_targets",
    }:
        return "str_or_null"

    return None


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "schema_version":
        return isinstance(value, int) and value == 1
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "list":
        return isinstance(value, list)
    if expected == "map":
        return isinstance(value, dict)
    if expected == "str_or_null":
        return value is None or isinstance(value, str)
    if expected == "scalar_or_null":
        return value is None or isinstance(value, (str, int, float)) and not isinstance(value, bool)
    return True


PROFILE_SCHEMA: dict[str, Any] = {
    "schema_version": None,
    "profile": None,
    "extends": None,
    "identity": {
        "cluster_name": None,
        "node_name": None,
        "command_prefix": None,
        "group_prefix": None,
        "default_partition": None,
        "compatibility_aliases": None,
    },
    "admins": {
        "users": None,
        "groups": None,
    },
    "services": {
        "munge": {
            "key_mode": None,
            "auto_rotate": None,
            "backup": None,
            "validate_permissions": None,
            "require_service_healthy": None,
        },
    },
    "hardware": {
        "discovered_at": None,
        "gpus": None,
        "gpu_vendor": None,
        "gpu_type": None,
        "gpu_modes": {
            "mig": None,
            "mps": None,
            "shared_gpu": None,
        },
        "gpu_affinity": {
            "mode": None,
            "render_gres_cores": None,
            "cores": {ANY: None},
        },
        "cpus_total": None,
        "cpus_allocatable": None,
        "memory_total": None,
        "memory_allocatable": None,
        "reserved_cpus": None,
        "reserved_memory": None,
        "memory_defaults": {
            "def_mem_per_cpu": None,
            "max_mem_per_cpu": None,
        },
    },
    "policies": {
        "slurm_core": None,
        "tiers": None,
        "storage": None,
        "cache": None,
        "modules": None,
        "login": None,
    },
    "operations": {
        "capability_checks": {
            "ubuntu": None,
            "slurm": None,
            "database": None,
            "gpu": None,
        },
        "plan_artifacts": {
            "root": None,
            "retention": None,
            "owner": None,
            "group": None,
            "directory_mode": None,
            "file_mode": None,
            "risky_apply_requires_token": None,
            "token_policy": {
                "redacted": None,
                "input_hash_bound": None,
                "expires_after": None,
                "single_use": None,
            },
            "redaction": {
                "secrets": None,
                "ssh_keys": None,
                "emails": {
                    "terminal": None,
                    "root_json": None,
                },
                "sensitive_manifests": {
                    "terminal": None,
                    "root_json": None,
                },
            },
        },
        "reconcile": {
            "model": None,
            "resume_partial_transitions": None,
            "rollback": None,
        },
        "backups": {
            "users_yml": {
                "root": None,
                "retention": None,
            },
            "users_state_yml": {
                "root": None,
                "retention": None,
            },
        },
        "gpu_verification": {
            "run_after_boot": None,
            "run_after_apply": None,
            "health_gate": None,
            "on_failure": None,
            "recovery": {
                "type": None,
                "running_gpu_jobs": None,
                "pending_gpu_jobs": None,
                "gpu_gres": None,
                "cpu_jobs": None,
            },
            "checks": None,
        },
        "storage_acknowledgements": {
            "nondurable_data": None,
        },
    },
    "overrides": {
        "policies": {
            "slurm_core": None,
            "tiers": None,
            "storage": None,
            "cache": None,
            "modules": None,
            "login": None,
        },
    },
}


POLICY_SCHEMAS: dict[str, dict[str, Any]] = {
    "cache": {
        "requires": {"scratch": None},
        "injection": {
            "login_shells": None,
            "slurm_jobs": None,
            "mode": None,
            "slurm_job_temp_override": None,
        },
        "ttl": None,
        "roots": {
            "scratch_cache": None,
            "persistent_cache": None,
            "persistent_cache_fallback": None,
            "home_config": None,
            "scratch_tmp": None,
        },
        "env": {
            "scratch": {ANY: None},
            "persistent": {ANY: None},
            "home_config": {ANY: None},
        },
    },
    "login": {
        "cgroup": None,
        "non_admin_limits": {
            "scope": None,
            "cpus": None,
            "memory": None,
            "tasks": None,
            "io_weight": None,
            "applies_to_slurm_jobs": None,
            "slurm_job_cgroups_owned_by_slurm": None,
        },
        "admins_exempt": None,
        "remote_ides": None,
        "gpu_outside_slurm": {
            "direct_access": None,
            "enforcement": None,
            "applies_to": None,
            "slurm_jobs_receive_allocated_devices": None,
            "fail_closed_if_unavailable": None,
            "friendly_path_wrappers": None,
            "status_wrapper": None,
            "status_collector": None,
            "refresh": None,
        },
    },
    "modules": {
        "roots": {
            "apps": None,
            "modules": None,
            "containers": None,
        },
        "lmod": None,
        "updates": {
            "mode": None,
            "versioned_roots": None,
            "smoke_checks_required": None,
            "rollback_targets": None,
            "unattended_updates": None,
        },
        "shared_env_base": {
            "type": None,
            "root": None,
        },
        "cuda": {
            "toolkit_mode": None,
            "managed_updates": None,
            "modules": {
                "default": None,
                "versioned": None,
            },
            "smoke_checks": {
                "module_load_unload": None,
                "nvcc_version_if_present": None,
                "nvidia_smi": None,
                "library_path_sanity": None,
                "optional_sample_compile_run": None,
            },
        },
        "apptainer": {
            "enabled": None,
            "container_root": None,
        },
    },
    "slurm_core": {
        "select": {
            "type": None,
            "parameters": None,
            "consumable": {
                "cpu": None,
                "memory": None,
                "gres": None,
            },
        },
        "task_plugin": {
            "cgroup_v2": None,
            "constrain_cores": None,
            "constrain_ram": None,
            "constrain_devices": None,
        },
        "accounting": {
            "storage_tres": None,
            "gpu_tres_when_available": None,
        },
        "memory": {
            "default_mode": None,
            "def_mem_per_cpu": None,
            "max_mem_per_cpu": None,
            "reserve_base": None,
            "tier_max_percent_enforced": None,
            "render_qos_max_tres_mem": None,
        },
        "submit_filter": {
            "plugin": None,
            "mode": None,
            "client_filter": {
                "enabled": None,
                "plugin": None,
                "path": None,
            },
            "reject": None,
            "disallow_slow_io": None,
            "disallow_runtime_state_checks": None,
            "final_enforcement": None,
        },
    },
    "storage": {
        "paths": {
            "home": None,
            "data": None,
            "scratch": None,
            "archive": None,
        },
        "durability": {
            "data": None,
            "archive": None,
            "active_data_backup": None,
            "nondurable_ack_required": None,
        },
        "quotas": {
            "home": None,
            "data": None,
            "scratch": None,
            "fail_if_unavailable": None,
            "data_scratch_capacity_isolation": None,
            "same_pool_allowed_with_project_quotas": None,
        },
        "scratch_cleanup": {
            "enabled": None,
            "age": None,
            "implementation": None,
            "report": None,
            "exclude_job_scratch": None,
            "exclude_active_jobs": None,
        },
        "job_scratch": {
            "implementation": None,
            "required_for_jobs": None,
            "unhealthy_action": None,
            "runtime_failure": {
                "affected_job": None,
                "mark_scratch_unhealthy": None,
                "block_new_jobs": None,
                "fallback_temp": None,
                "avoid_node_drain_where_slurm_permits": None,
            },
            "preflight_checks": None,
            "env_var": None,
            "task_prolog_exports": None,
            "root": None,
            "create_with": None,
            "export_with": None,
            "cleanup_with": None,
            "job_container_tmpfs": None,
        },
        "inactive_archive": {
            "requires_archive_root": None,
            "service_identity": None,
            "slurm_account": None,
            "qos": None,
            "preemptible": None,
            "outside_user_limits": None,
            "monitored_by": None,
            "user_account_until_success": None,
            "removal_requires_backup_success": None,
            "removal_override": None,
            "backup_hook": {
                "directory": None,
                "required_for_durability": None,
                "missing_hook_action": None,
                "local_only_override": None,
            },
            "slurm_unavailable": None,
            "external_backup_required_for_durability": None,
            "state_substates": None,
            "compression": None,
            "apply_requires_plan_token": None,
            "prune_manifest": None,
            "symlinks": None,
            "delete_fixed_paths": None,
            "recursive_marker_rules": {ANY: {"marker_file": None}},
            "report_only_names": None,
        },
    },
    "tiers": {
        "slurm_account": None,
        "default_tier": None,
        "memory_percent_base": None,
        "fairshare": {
            "priority_type": None,
            "priority_decay_half_life": None,
            "priority_weight_fairshare": None,
            "priority_weight_age": None,
            "priority_weight_qos": None,
            "account_user_shares": None,
            "tres_billing_weights": {ANY: None},
        },
        "preemption": {
            "type": None,
            "mode": None,
            "grace_time": None,
            "job_requeue": None,
            "allow_user_no_requeue": None,
            "submit_rejects_no_requeue": None,
            "interactive_jobs": {
                "allowed": None,
                "on_preempt": None,
            },
            "relationships": {ANY: {"preempts": None}},
        },
        "tiers": {
            ANY: {
                "max_gpus_per_job": None,
                "max_cpus_per_job": None,
                "max_running_jobs": None,
                "max_submitted_jobs": None,
                "default_walltime": None,
                "max_walltime": None,
                "memory_percent": None,
                "preempt_rank": None,
            }
        },
    },
}


def validate_profile_schema(profile_name: str, data: dict[str, Any], *, complete: bool = False) -> list[str]:
    path = f"profile {profile_name}"
    errors = [
        *validate_schema_version(data, path),
        *validate_schema(data, PROFILE_SCHEMA, path),
        *validate_types(data, path),
        *validate_required(data, PROFILE_RAW_REQUIRED, path),
    ]
    if complete:
        required = [*PROFILE_COMPLETE_REQUIRED]
        hardware = data.get("hardware") if isinstance(data.get("hardware"), dict) else {}
        gpu_count = hardware.get("gpus") if isinstance(hardware, dict) else None
        gpu_vendor = hardware.get("gpu_vendor") if isinstance(hardware, dict) else None
        if gpu_count == REVIEW_REQUIRED or (isinstance(gpu_count, int) and gpu_count > 0) or gpu_vendor == "nvidia":
            required.extend(PROFILE_GPU_REQUIRED)
        errors.extend(validate_required(data, required, path))
    return errors


def validate_policy_file_schema(domain: str, data: dict[str, Any]) -> list[str]:
    path = f"policy {domain}"
    if domain not in POLICY_SCHEMAS:
        return [f"unknown policy domain {domain!r}"]
    domain_schema = POLICY_SCHEMAS[domain]
    schema = {
        "schema_version": None,
        "policies": {ANY: domain_schema},
    }
    errors = [
        *validate_schema_version(data, path),
        *validate_schema(data, schema, path),
        *validate_types(data, path),
        *validate_required(data, [("schema_version",), ("policies",)], path),
    ]
    policies = data.get("policies")
    if isinstance(policies, dict):
        if not policies:
            errors.append(f"{path}.policies must contain at least one policy")
        for policy_name, policy in policies.items():
            if not isinstance(policy, dict):
                errors.append(f"{path}.policies.{policy_name} must be a map")
                continue
            errors.extend(
                validate_required(
                    policy,
                    POLICY_REQUIRED.get(domain, []),
                    f"{path}.policies.{policy_name}",
                )
            )
            if domain == "tiers":
                tiers = policy.get("tiers")
                if isinstance(tiers, dict):
                    for tier_name, tier in tiers.items():
                        if not isinstance(tier, dict):
                            errors.append(f"{path}.policies.{policy_name}.tiers.{tier_name} must be a map")
                            continue
                        errors.extend(
                            validate_required(
                                tier,
                                [
                                    ("max_cpus_per_job",),
                                    ("max_running_jobs",),
                                    ("max_submitted_jobs",),
                                    ("default_walltime",),
                                    ("max_walltime",),
                                    ("memory_percent",),
                                    ("preempt_rank",),
                                ],
                                f"{path}.policies.{policy_name}.tiers.{tier_name}",
                            )
                        )
    return errors
