from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import unknown_keys, validate_policy_file_schema, validate_profile_schema
from .units import duration_to_seconds, memory_to_mb, normalize_duration
from .yamlutil import dump_yaml, load_yaml


REVIEW_REQUIRED = "REVIEW_REQUIRED"
POLICY_FILES = {
    "slurm_core": "slurm-core.yml",
    "tiers": "tiers.yml",
    "storage": "storage.yml",
    "cache": "cache.yml",
    "modules": "modules.yml",
    "login": "login.yml",
}

PROFILE_KEYS = {
    "schema_version",
    "profile",
    "extends",
    "identity",
    "admins",
    "services",
    "hardware",
    "policies",
    "operations",
    "overrides",
}


@dataclass(frozen=True)
class RepoPaths:
    root: Path

    @property
    def profiles(self) -> Path:
        return self.root / "profiles"

    @property
    def policies(self) -> Path:
        return self.root / "policies"


def repo_root(start: str | Path | None = None) -> Path:
    if start is None and os.environ.get("SSN_REPO"):
        candidate = Path(os.environ["SSN_REPO"]).resolve()
        if (candidate / "profiles").exists() and (candidate / "policies").exists():
            return candidate
    current = Path(start or os.getcwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "docs" / "decisions.md").exists() and (candidate / "tesla-scheduler-v2").exists():
            return candidate
    raise FileNotFoundError("could not locate repository root")


def load_profile(name: str, paths: RepoPaths) -> dict[str, Any]:
    path = paths.profiles / f"{name}.yml"
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {path}")
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"profile {name} must be a map")
    unknown = unknown_keys(data, PROFILE_KEYS)
    if unknown:
        raise ValueError(f"profile {name} has unknown top-level keys: {sorted(unknown)}")
    schema_errors = validate_profile_schema(name, data)
    if schema_errors:
        raise ValueError("\n".join(schema_errors))
    parent_name = data.get("extends")
    if parent_name:
        parent = load_profile(str(parent_name), paths)
        child = deepcopy(data)
        child.pop("extends", None)
        merged = deep_merge(parent, child)
        merged["extends"] = parent_name
        schema_errors = validate_profile_schema(name, merged, complete=True)
        if schema_errors:
            raise ValueError("\n".join(schema_errors))
        return merged
    schema_errors = validate_profile_schema(name, data, complete=True)
    if schema_errors:
        raise ValueError("\n".join(schema_errors))
    return data


def load_policy(domain: str, policy_name: str, paths: RepoPaths) -> dict[str, Any]:
    file_name = POLICY_FILES[domain]
    data = load_yaml(paths.policies / file_name)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"policy file {file_name} must use schema_version: 1")
    schema_errors = validate_policy_file_schema(domain, data)
    if schema_errors:
        raise ValueError("\n".join(schema_errors))
    policies = data.get("policies")
    if not isinstance(policies, dict) or policy_name not in policies:
        raise ValueError(f"policy {policy_name!r} not found in {file_name}")
    return deepcopy(policies[policy_name])


def resolve_profile(
    profile_name: str,
    root: str | Path | None = None,
    *,
    allow_review_required: bool = False,
) -> dict[str, Any]:
    paths = RepoPaths(repo_root(root))
    profile = load_profile(profile_name, paths)
    if profile.get("schema_version") != 1:
        raise ValueError(f"profile {profile_name} must use schema_version: 1")

    policy_selection = deepcopy(profile.get("policies") or {})
    policy_overrides = ((profile.get("overrides") or {}).get("policies") or {})
    policy_selection = deep_merge(policy_selection, policy_overrides)
    resolved_policies: dict[str, Any] = {}
    for domain, policy_name in policy_selection.items():
        if domain not in POLICY_FILES:
            raise ValueError(f"unknown policy domain {domain!r}")
        resolved_policies[domain] = load_policy(domain, str(policy_name), paths)

    resolved = deepcopy(profile)
    resolved["selected_policies"] = deepcopy(policy_selection)
    resolved["resolved_policies"] = resolved_policies
    apply_profile_overrides(resolved)
    apply_profile_defaults_to_policies(resolved)
    missing = find_review_required(resolved)
    if missing and not allow_review_required:
        joined = "\n  - ".join(missing)
        raise ValueError(f"profile contains REVIEW_REQUIRED values:\n  - {joined}")
    derive(resolved, allow_review_required=allow_review_required)
    validate_resolved(resolved, allow_review_required=allow_review_required)
    return resolved


def apply_profile_overrides(resolved: dict[str, Any]) -> None:
    overrides = resolved.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("profile overrides must be a map")
    if "policies" in overrides:
        for domain, policy_name in overrides["policies"].items():
            resolved["selected_policies"][domain] = policy_name


def apply_profile_defaults_to_policies(resolved: dict[str, Any]) -> None:
    hardware = resolved.get("hardware") or {}
    memory_defaults = hardware.get("memory_defaults") or {}
    slurm_memory = (
        resolved.get("resolved_policies", {})
        .get("slurm_core", {})
        .get("memory", {})
    )
    for key in ("def_mem_per_cpu", "max_mem_per_cpu"):
        if slurm_memory.get(key) == REVIEW_REQUIRED and memory_defaults.get(key) is not None:
            slurm_memory[key] = memory_defaults[key]


def derive(resolved: dict[str, Any], *, allow_review_required: bool = False) -> None:
    identity = resolved["identity"]
    hardware = resolved["hardware"]
    policies = resolved["resolved_policies"]
    tiers_policy = policies["tiers"]
    storage = policies["storage"]
    slurm_core = policies["slurm_core"]
    fairshare = tiers_policy["fairshare"]
    preemption = tiers_policy["preemption"]
    has_gpus = int(hardware.get("gpus") or 0) > 0 if hardware.get("gpus") != REVIEW_REQUIRED else True

    cpus_allocatable = _reviewable_int(hardware["cpus_allocatable"], allow_review_required)
    memory_allocatable_mb = _reviewable_memory_mb(hardware["memory_allocatable"], allow_review_required)
    memory_total_mb = _reviewable_memory_mb(hardware["memory_total"], allow_review_required)
    default_time = _first_tier_value(tiers_policy, "default_walltime")
    max_time = max(duration_to_seconds(t["max_walltime"]) for t in tiers_policy["tiers"].values())
    qos_prefix = identity.get("group_prefix", "ssn")

    rendered_tiers = []
    for name, tier in tiers_policy["tiers"].items():
        max_gpus = tier.get("max_gpus_per_job")
        if max_gpus == "all":
            max_gpus = _reviewable_int(hardware.get("gpus") or 0, allow_review_required)
        mem_mb = int(memory_allocatable_mb * int(tier["memory_percent"]) / 100)
        rendered_tiers.append(
            {
                "name": name,
                "qos": f"{qos_prefix}-{name}",
                "group": f"{qos_prefix}-tier-{name}",
                "max_cpus_per_job": _reviewable_int(tier["max_cpus_per_job"], allow_review_required),
                "max_gpus_per_job": None if max_gpus is None else int(max_gpus),
                "max_running_jobs": int(tier["max_running_jobs"]),
                "max_submitted_jobs": int(tier["max_submitted_jobs"]),
                "default_walltime": normalize_duration(tier["default_walltime"]),
                "max_walltime": normalize_duration(tier["max_walltime"]),
                "memory_percent": int(tier["memory_percent"]),
                "max_memory_mb": mem_mb,
                "preempt_rank": int(tier["preempt_rank"]),
            }
        )

    qos_by_tier = {tier["name"]: tier["qos"] for tier in rendered_tiers}
    for tier in rendered_tiers:
        rel = preemption.get("relationships", {}).get(tier["name"], {})
        tier["preempts"] = [qos_by_tier[name] for name in rel.get("preempts", [])]

    accounting_tres = list(slurm_core["accounting"].get("storage_tres", []))
    if not has_gpus:
        accounting_tres = [item for item in accounting_tres if not str(item).startswith("gres/gpu")]

    gres_entries: list[dict[str, Any]] = []
    if has_gpus:
        gpu_type = hardware["gpu_type"]
        affinity = hardware.get("gpu_affinity", {}).get("cores")
        for gpu_index in range(_reviewable_int(hardware["gpus"], allow_review_required)):
            entry: dict[str, Any] = {
                "name": "gpu",
                "file": f"/dev/nvidia{gpu_index}",
            }
            if gpu_type:
                entry["type"] = gpu_type
            if isinstance(affinity, dict):
                cores = affinity.get(str(gpu_index), affinity.get(gpu_index, ""))
                if cores:
                    entry["cores"] = str(cores)
            gres_entries.append(entry)

    resolved["derived"] = {
        "has_gpus": has_gpus,
        "umbrella_group": f"{qos_prefix}-users",
        "admin_group": (resolved.get("admins", {}).get("groups") or ["slurm_admins"])[0],
        "slurm_account": tiers_policy.get("slurm_account", "default"),
        "cpus_allocatable": cpus_allocatable,
        "memory_total_mb": memory_total_mb,
        "memory_allocatable_mb": memory_allocatable_mb,
        "default_walltime": normalize_duration(default_time),
        "max_walltime": normalize_duration(max_time),
        "accounting_storage_tres": accounting_tres,
        "rendered_tiers": rendered_tiers,
        "gres_entries": gres_entries,
        "paths": storage.get("paths", {}),
        "slurm_conf_dir": "/etc/slurm",
        "slurm_log_dir": "/var/log/slurm",
        "slurm_spool_dir": "/var/spool/slurm",
        "slurm_pid_dir": "/run/slurm",
        "db_name": "slurm_acct_db",
        "db_user": "slurm",
        "db_password_file": "/etc/slurm/slurmdbd-mysql.password",
        "db_host": "localhost",
        "slurmdbd_port": 6819,
        "def_mem_per_cpu_mb": _reviewable_memory_mb(hardware["memory_defaults"]["def_mem_per_cpu"], allow_review_required),
        "max_mem_per_cpu_mb": _reviewable_memory_mb(hardware["memory_defaults"]["max_mem_per_cpu"], allow_review_required),
        "priority": {
            "decay_half_life": fairshare["priority_decay_half_life"],
            "weight_fairshare": int(fairshare["priority_weight_fairshare"]),
            "weight_age": int(fairshare["priority_weight_age"]),
            "weight_qos": int(fairshare["priority_weight_qos"]),
            "tres_billing_weights": fairshare["tres_billing_weights"],
        },
        "preemption": preemption,
    }


def validate_resolved(resolved: dict[str, Any], *, allow_review_required: bool = False) -> None:
    missing = find_review_required(resolved)
    if missing and not allow_review_required:
        joined = "\n  - ".join(missing)
        raise ValueError(f"profile contains REVIEW_REQUIRED values:\n  - {joined}")

    storage_paths = resolved["resolved_policies"]["storage"].get("paths", {})
    cache_policy = resolved["resolved_policies"]["cache"]
    if cache_policy.get("requires", {}).get("scratch") and not storage_paths.get("scratch"):
        raise ValueError("selected cache policy requires scratch, but storage scratch path is disabled")

    if not resolved["derived"]["has_gpus"]:
        for tier in resolved["derived"]["rendered_tiers"]:
            if tier.get("max_gpus_per_job") not in (None, 0):
                raise ValueError("CPU-only profile selected a tier with GPU limits")

    cpus_allocatable = int(resolved["derived"]["cpus_allocatable"])
    for tier in resolved["derived"]["rendered_tiers"]:
        if int(tier["max_cpus_per_job"]) > cpus_allocatable:
            raise ValueError(
                f"tier {tier['name']} max_cpus_per_job={tier['max_cpus_per_job']} "
                f"exceeds allocatable CPUs={cpus_allocatable}"
            )
        if resolved["derived"]["has_gpus"] and tier.get("max_gpus_per_job") is not None:
            if resolved["hardware"]["gpus"] == REVIEW_REQUIRED and allow_review_required:
                continue
            gpus = int(resolved["hardware"]["gpus"])
            if int(tier["max_gpus_per_job"]) > gpus:
                raise ValueError(
                    f"tier {tier['name']} max_gpus_per_job={tier['max_gpus_per_job']} "
                    f"exceeds configured GPUs={gpus}"
                )


def find_review_required(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if value == REVIEW_REQUIRED:
        return [path]
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(find_review_required(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_review_required(item, f"{path}[{index}]"))
    return found


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(override)


def to_ansible_vars(resolved: dict[str, Any]) -> dict[str, Any]:
    return {"ssn": resolved}


def render_profile(
    profile_name: str,
    output_dir: str | Path,
    root: str | Path | None = None,
    *,
    allow_review_required: bool = False,
) -> dict[str, Any]:
    resolved = resolve_profile(profile_name, root, allow_review_required=allow_review_required)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    resolved_json = json.dumps(resolved, indent=2, sort_keys=True)
    (out / "resolved-config.json").write_text(resolved_json + "\n")
    (out / "ansible-vars.json").write_text(json.dumps(to_ansible_vars(resolved), indent=2, sort_keys=True) + "\n")
    (out / "ansible-vars.yml").write_text(dump_yaml(to_ansible_vars(resolved)))
    (out / "summary.txt").write_text(summary_text(resolved))
    return resolved


def summary_text(resolved: dict[str, Any]) -> str:
    identity = resolved["identity"]
    derived = resolved["derived"]
    lines = [
        f"Profile: {resolved['profile']}",
        f"Cluster: {identity['cluster_name']}",
        f"Node: {identity['node_name']}",
        f"Partition: {identity['default_partition']}",
        f"CPUs exposed to Slurm: {derived['cpus_allocatable']}",
        f"Memory exposed to Slurm: {derived['memory_allocatable_mb']} MB",
        f"GPU profile: {'yes' if derived['has_gpus'] else 'no'}",
        "Tiers:",
    ]
    for tier in derived["rendered_tiers"]:
        gpu = tier["max_gpus_per_job"]
        gpu_text = "" if gpu is None else f", GPUs={gpu}"
        lines.append(
            f"  - {tier['name']}: CPUs={tier['max_cpus_per_job']}{gpu_text}, "
            f"mem={tier['max_memory_mb']}MB, jobs={tier['max_running_jobs']}/{tier['max_submitted_jobs']}"
        )
    return "\n".join(lines) + "\n"


def config_hash(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _first_tier_value(tiers_policy: dict[str, Any], field: str) -> Any:
    default = tiers_policy.get("default_tier")
    if default and default in tiers_policy["tiers"]:
        return tiers_policy["tiers"][default][field]
    first = next(iter(tiers_policy["tiers"].values()))
    return first[field]


def _reviewable_int(value: Any, allow_review_required: bool) -> int:
    if value == REVIEW_REQUIRED and allow_review_required:
        return 0
    return int(value)


def _reviewable_memory_mb(value: Any, allow_review_required: bool) -> int:
    if value == REVIEW_REQUIRED and allow_review_required:
        return 0
    return memory_to_mb(value)
