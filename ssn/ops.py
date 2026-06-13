from __future__ import annotations

import datetime as dt
import grp
import json
import os
import platform
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import config_hash
from .safety import redact_for_plan


TOKEN_PREFIX = "ssnpt"
DEFAULT_TOKEN_STORE = Path("/var/lib/slurm-single-node/plan-tokens")
ACTIVE_JOB_STATES = {"BOOT_FAIL", "CONFIGURING", "COMPLETING", "RESIZING", "RUNNING", "SUSPENDED"}


def command_stdout(cmd: list[str]) -> str | None:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None


def command_rc(cmd: list[str]) -> int:
    return subprocess.run(cmd, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def slurm_jobs() -> list[dict[str, str]]:
    if shutil.which("squeue") is None:
        return []
    output = command_stdout(["squeue", "-h", "-o", "%i|%T|%u|%j"]) or ""
    jobs = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            jobs.append({"id": line.strip(), "state": "UNKNOWN", "user": "", "name": ""})
            continue
        jobs.append({"id": parts[0], "state": parts[1], "user": parts[2], "name": parts[3]})
    return jobs


def queued_jobs() -> list[str]:
    return [f"{job['id']}|{job['state']}|{job['user']}|{job['name']}" for job in slurm_jobs()]


def active_jobs() -> list[dict[str, str]]:
    return [job for job in slurm_jobs() if job.get("state") in ACTIVE_JOB_STATES]


def drain_node(node_name: str, reason: str) -> dict[str, Any]:
    if shutil.which("scontrol") is None:
        raise RuntimeError("scontrol is required for --drain")
    state_before = command_stdout(["sinfo", "-h", "-N", "-n", node_name, "-o", "%T"]) or ""
    already_drained = "drain" in state_before.lower()
    command = ["scontrol", "update", f"NodeName={node_name}", "State=DRAIN", f"Reason={reason}"]
    rc = command_rc(command)
    if rc != 0:
        raise RuntimeError("failed to drain node with scontrol")
    return {
        "node": node_name,
        "state_before": state_before,
        "initiated_by_ssn": not already_drained,
        "reason": reason,
    }


def resume_node(node_name: str) -> None:
    if shutil.which("scontrol") is None:
        raise RuntimeError("scontrol is required to resume node")
    rc = command_rc(["scontrol", "update", f"NodeName={node_name}", "State=RESUME"])
    if rc != 0:
        raise RuntimeError("failed to resume node with scontrol")


def wait_for_no_active_jobs(timeout_seconds: int, *, poll_seconds: int = 2) -> list[dict[str, str]]:
    deadline = time.time() + timeout_seconds
    last = active_jobs()
    while time.time() <= deadline:
        last = active_jobs()
        if not last:
            return []
        time.sleep(poll_seconds)
    return last


def secure_path(path: Path, *, group: str = "slurm_admins") -> None:
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        gid = None
    paths = [path]
    if path.is_dir():
        paths.extend(path.rglob("*"))
    for item in paths:
        try:
            if os.geteuid() == 0 and gid is not None:
                os.chown(item, 0, gid, follow_symlinks=False)
            item.chmod(0o750 if item.is_dir() else 0o640)
        except OSError:
            continue


def write_protected_json(path: Path, payload: dict[str, Any], *, group: str = "slurm_admins") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_for_plan(payload), indent=2, sort_keys=True) + "\n")
    secure_path(path.parent, group=group)


def collect_capabilities(resolved: dict[str, Any], *, mode: str) -> dict[str, Any]:
    commands = [
        "ansible-playbook",
        "scontrol",
        "squeue",
        "sacctmgr",
        "sinfo",
        "sbatch",
        "slurmd",
        "lua5.3",
        "nvidia-smi",
        "findmnt",
        "mount",
        "quotacheck",
        "quotaon",
        "setquota",
        "repquota",
    ]
    paths = resolved.get("derived", {}).get("paths") or {}
    mount_paths = dict.fromkeys(
        str(value)
        for value in ["/home", paths.get("home"), paths.get("data"), paths.get("scratch"), paths.get("archive")]
        if value
    )
    package_names = [
        "slurm-wlm",
        "slurmd",
        "slurmctld",
        "slurmdbd",
        "mariadb-server",
        "munge",
        "lua5.3",
        "liblua5.3-dev",
    ]
    versions = {
        "slurm": command_stdout(["sinfo", "--version"]),
        "mariadb": command_stdout(["mariadb", "--version"]),
        "lua": command_stdout(["lua5.3", "-v"]),
        "nvidia_smi": command_stdout(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]),
    }
    capabilities: dict[str, Any] = {
        "mode": mode,
        "os": {
            "pretty_name": _os_pretty_name(),
            "kernel": platform.release(),
        },
        "commands": {command: shutil.which(command) for command in commands},
        "packages": {name: _package_version(name) for name in package_names},
        "cgroup_fs": command_stdout(["stat", "-fc", "%T", "/sys/fs/cgroup"]),
        "mounts": {path: _mount_capability(path) for path in mount_paths},
        "versions": versions,
        "runtime_versions": versions,
        "slurm": {
            "accounting_cluster": command_stdout(["sacctmgr", "-nP", "show", "cluster", "format=cluster"]),
            "config_select": command_stdout(["scontrol", "show", "config"]),
            "cli_filter_lua_plugin": _find_slurm_plugin("cli_filter_lua.so"),
        },
    }
    if resolved.get("derived", {}).get("has_gpus"):
        gpu_lines = command_stdout([
            "nvidia-smi",
            "--query-gpu=index,name,uuid,pci.bus_id,memory.total",
            "--format=csv,noheader,nounits",
        ])
        capabilities["nvidia"] = {
            "query": gpu_lines,
            "devices": {f"/dev/nvidia{index}": Path(f"/dev/nvidia{index}").exists() for index in range(int(resolved["hardware"]["gpus"]))},
        }
    return capabilities


def validate_feature_gates(resolved: dict[str, Any], *, mode: str, capabilities: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    capabilities = capabilities or collect_capabilities(resolved, mode=mode)
    if capabilities.get("cgroup_fs") != "cgroup2fs":
        errors.append("cgroup v2 is required")
    required = ["ansible-playbook", "lua5.3"]
    if mode in {"apply", "install"}:
        required.extend(["scontrol", "squeue", "slurmd", "sacctmgr", "sinfo", "sbatch"])
    for command in required:
        if capabilities.get("commands", {}).get(command) is None:
            errors.append(f"required command is missing: {command}")
    client_filter = (
        resolved.get("resolved_policies", {})
        .get("slurm_core", {})
        .get("submit_filter", {})
        .get("client_filter", {})
    )
    if client_filter.get("enabled") and mode in {"apply", "install"}:
        if not capabilities.get("slurm", {}).get("cli_filter_lua_plugin"):
            errors.append("Slurm cli_filter/lua plugin is required but cli_filter_lua.so was not found")
    storage = resolved["resolved_policies"]["storage"]
    paths = resolved["derived"].get("paths") or {}
    if storage.get("quotas", {}).get("fail_if_unavailable") or storage.get("job_scratch", {}).get("required_for_jobs"):
        for label in ("data", "scratch"):
            path = paths.get(label)
            detail = capabilities.get("mounts", {}).get(str(path), {})
            if path and detail.get("findmnt") is None:
                errors.append(f"required storage path is not mounted: {label}={path}")
            if path and detail.get("exists") and not detail.get("writable"):
                errors.append(f"required storage path is not writable: {label}={path}")
    if storage.get("quotas", {}).get("fail_if_unavailable") and mode in {"apply", "install"}:
        from .storage import quota_capability_report

        quota = quota_capability_report({"schema_version": 1, "groups": {}, "users": {}}, resolved)
        for label, mount in quota.get("mounts", {}).items():
            if label == "scratch" and not paths.get("scratch"):
                continue
            if not mount.get("active_user_quota"):
                errors.append(
                    f"required user quota is inactive for {label}={mount.get('path')} "
                    f"(mount {mount.get('mountpoint')})"
                )
    if resolved["derived"]["has_gpus"]:
        if capabilities.get("commands", {}).get("nvidia-smi") is None:
            errors.append("GPU profile requires nvidia-smi")
        else:
            gpu_lines = capabilities.get("nvidia", {}).get("query")
            expected = int(resolved["hardware"]["gpus"])
            actual = len([line for line in (gpu_lines or "").splitlines() if line.strip()])
            if actual != expected:
                errors.append(f"GPU profile expects {expected} GPU(s), discovered {actual}")
            for index in range(expected):
                if not capabilities.get("nvidia", {}).get("devices", {}).get(f"/dev/nvidia{index}"):
                    errors.append(f"GPU profile expects /dev/nvidia{index}")
    if mode == "apply" and capabilities.get("commands", {}).get("sacctmgr") is not None:
        if capabilities.get("slurm", {}).get("accounting_cluster") is None:
            errors.append("Slurm accounting is unavailable through sacctmgr")
    return errors


def validate_resolved_slurm_features(resolved: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    slurm_core = resolved["resolved_policies"]["slurm_core"]
    if slurm_core.get("select", {}).get("type") != "select/cons_tres":
        errors.append("Slurm SelectType must be select/cons_tres")
    task = slurm_core.get("task_plugin", {})
    for key in ("cgroup_v2", "constrain_cores", "constrain_ram", "constrain_devices"):
        if task.get(key) is not True:
            errors.append(f"Slurm task_plugin.{key} must be true")
    if slurm_core.get("submit_filter", {}).get("plugin") != "job_submit.lua":
        errors.append("Slurm submit_filter.plugin must be job_submit.lua")
    client_filter = slurm_core.get("submit_filter", {}).get("client_filter", {})
    if client_filter.get("enabled"):
        if client_filter.get("plugin") != "cli_filter/lua":
            errors.append("Slurm submit_filter.client_filter.plugin must be cli_filter/lua")
        if not client_filter.get("path"):
            errors.append("Slurm submit_filter.client_filter.path must be set")
    if resolved["derived"]["has_gpus"]:
        expected = int(resolved["hardware"]["gpus"])
        if len(resolved["derived"].get("gres_entries", [])) != expected:
            errors.append(f"GPU profile should render {expected} GRES entries")
        if not any(str(item).startswith("gres/gpu") for item in resolved["derived"].get("accounting_storage_tres", [])):
            errors.append("GPU profile should include gres/gpu accounting TRES")
    else:
        if resolved["derived"].get("gres_entries"):
            errors.append("CPU-only profile must not render GPU GRES entries")
        if any(str(item).startswith("gres/gpu") for item in resolved["derived"].get("accounting_storage_tres", [])):
            errors.append("CPU-only profile must not include gres/gpu accounting TRES")
    return errors


def validate_installed_slurm_features(resolved: dict[str, Any], *, conf_dir: str | Path = "/etc/slurm") -> list[str]:
    errors: list[str] = []
    conf_root = Path(conf_dir)
    slurm_conf = _read_text(conf_root / "slurm.conf")
    cgroup_conf = _read_text(conf_root / "cgroup.conf")
    gres_conf = _read_text(conf_root / "gres.conf")
    expected = {
        "SelectType=select/cons_tres": slurm_conf,
        "ProctrackType=proctrack/cgroup": slurm_conf,
        "TaskPlugin=task/cgroup": slurm_conf,
        "JobSubmitPlugins=lua": slurm_conf,
        "CgroupPlugin=cgroup/v2": cgroup_conf,
        "ConstrainCores=yes": cgroup_conf,
        "ConstrainRAMSpace=yes": cgroup_conf,
        "ConstrainDevices=yes": cgroup_conf,
    }
    for needle, haystack in expected.items():
        if needle not in haystack:
            errors.append(f"installed Slurm config missing {needle}")
    client_filter = (
        resolved.get("resolved_policies", {})
        .get("slurm_core", {})
        .get("submit_filter", {})
        .get("client_filter", {})
    )
    if client_filter.get("enabled"):
        if "CliFilterPlugins=cli_filter/lua" not in slurm_conf:
            errors.append("installed Slurm config missing CliFilterPlugins=cli_filter/lua")
        configured_path = Path(str(client_filter.get("path") or conf_root / "cli_filter.lua"))
        if not configured_path.exists():
            errors.append(f"installed Slurm cli_filter.lua is missing: {configured_path}")
    if resolved["derived"]["has_gpus"]:
        if "GresTypes=gpu" not in slurm_conf:
            errors.append("GPU profile installed config missing GresTypes=gpu")
        if "Name=gpu" not in gres_conf:
            errors.append("GPU profile installed config missing gres.conf entries")
    else:
        if "GresTypes=gpu" in slurm_conf or "gres/gpu" in slurm_conf or "Name=gpu" in gres_conf:
            errors.append("CPU-only installed config must not include GPU GRES/TRES")
    return errors


def _mount_capability(path: str) -> dict[str, Any]:
    target = Path(path)
    free_mb = None
    if target.exists():
        try:
            free_mb = int(shutil.disk_usage(path).free / 1024 / 1024)
        except OSError:
            free_mb = None
    return {
        "findmnt": command_stdout(["findmnt", "-no", "TARGET,SOURCE,FSTYPE,OPTIONS", path]),
        "exists": target.exists(),
        "writable": os.access(path, os.W_OK) if target.exists() else False,
        "free_mb": free_mb,
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _package_version(package: str) -> str | None:
    return command_stdout(["dpkg-query", "-W", "-f=${Version}", package]) or None


def _find_slurm_plugin(filename: str) -> str | None:
    candidates = [
        Path("/usr/lib/x86_64-linux-gnu/slurm-wlm") / filename,
        Path("/usr/lib/slurm-wlm") / filename,
        Path("/usr/lib64/slurm") / filename,
        Path("/usr/lib/slurm") / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    for base in (Path("/usr/lib"), Path("/usr/lib64")):
        if not base.exists():
            continue
        try:
            matches = sorted(base.glob(f"*/slurm*/{filename}"))
        except OSError:
            matches = []
        if matches:
            return str(matches[0])
    return None


def _os_pretty_name() -> str:
    path = Path("/etc/os-release")
    if not path.exists():
        return "unknown"
    for line in path.read_text().splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.partition("=")[2].strip().strip('"')
    return "unknown"


def token_input_hash(report: dict[str, Any], risk: str) -> str:
    payload = {
        "command": report.get("command"),
        "profile": report.get("profile"),
        "config_hash": report.get("config_hash"),
        "operation_hash": report.get("operation_hash"),
        "risk": risk,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return config_hash({"token_input": raw.decode(errors="ignore")})


def token_hash(token: str) -> str:
    return config_hash({"token": token})


def create_plan_token(
    plan_path: str | Path,
    *,
    risk: str,
    reason: str,
    store_root: str | Path = DEFAULT_TOKEN_STORE,
    expires_hours: int = 24,
) -> tuple[str, dict[str, Any]]:
    plan_path = Path(plan_path)
    report = json.loads(plan_path.read_text())
    if not report.get("config_hash"):
        raise ValueError("plan report has no config_hash; cannot create a bound token")
    if not report.get("command"):
        raise ValueError("plan report has no command intent; cannot create a bound token")
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    token = f"{TOKEN_PREFIX}_{token_id}_{secret}"
    now = dt.datetime.now(dt.timezone.utc)
    record = {
        "schema_version": 1,
        "token_id": token_id,
        "token_hash": token_hash(token),
        "command": report.get("command"),
        "profile": report.get("profile"),
        "config_hash": report.get("config_hash"),
        "risk": risk,
        "input_hash": token_input_hash(report, risk),
        "reason": reason,
        "plan": str(plan_path),
        "created_at": now.isoformat(),
        "expires_at": (now + dt.timedelta(hours=expires_hours)).isoformat(),
        "used_at": None,
    }
    store = Path(store_root)
    store.mkdir(parents=True, exist_ok=True)
    record_path = store / f"{token_id}.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    secure_path(store)
    return token, record


def validate_plan_token(
    token: str,
    report: dict[str, Any],
    *,
    risk: str,
    store_root: str | Path = DEFAULT_TOKEN_STORE,
    mark_used: bool = True,
) -> dict[str, Any]:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise ValueError("invalid plan token format")
    token_id = parts[1]
    path = Path(store_root) / f"{token_id}.json"
    if not path.exists():
        raise ValueError("plan token record not found")
    record = json.loads(path.read_text())
    if record.get("token_hash") != token_hash(token):
        raise ValueError("plan token secret does not match")
    if record.get("used_at"):
        raise ValueError("plan token has already been used")
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = dt.datetime.fromisoformat(record["expires_at"])
    if expires_at <= now:
        raise ValueError("plan token has expired")
    expected = token_input_hash(report, risk)
    if record.get("input_hash") != expected:
        raise ValueError("plan token is not bound to this plan/input")
    if mark_used:
        record["used_at"] = now.isoformat()
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        secure_path(path.parent)
    return record
