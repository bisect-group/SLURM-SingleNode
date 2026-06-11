from __future__ import annotations

from typing import Any


ANY = "*"


def unknown_keys(data: dict[str, Any], allowed: set[str]) -> set[str]:
    return {key for key in data if key not in allowed and not str(key).startswith("x_")}


def validate_schema(value: Any, schema: Any, path: str) -> list[str]:
    if value == "REVIEW_REQUIRED":
        return []
    if schema is None or str(path).split(".")[-1].startswith("x_"):
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


def validate_profile_schema(profile_name: str, data: dict[str, Any]) -> list[str]:
    return validate_schema(data, PROFILE_SCHEMA, f"profile {profile_name}")


def validate_policy_file_schema(domain: str, data: dict[str, Any]) -> list[str]:
    domain_schema = POLICY_SCHEMAS[domain]
    schema = {
        "schema_version": None,
        "policies": {ANY: domain_schema},
    }
    return validate_schema(data, schema, f"policy {domain}")
