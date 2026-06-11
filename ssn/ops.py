from __future__ import annotations

import datetime as dt
import grp
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import config_hash
from .safety import redact_for_plan


TOKEN_PREFIX = "ssnpt"
DEFAULT_TOKEN_STORE = Path("/var/lib/slurm-single-node/plan-tokens")


def command_stdout(cmd: list[str]) -> str | None:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None


def queued_jobs() -> list[str]:
    if shutil.which("squeue") is None:
        return []
    output = command_stdout(["squeue", "-h", "-o", "%i|%T|%u|%j"]) or ""
    return [line for line in output.splitlines() if line.strip()]


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


def validate_feature_gates(resolved: dict[str, Any], *, mode: str) -> list[str]:
    errors: list[str] = []
    if command_stdout(["stat", "-fc", "%T", "/sys/fs/cgroup"]) != "cgroup2fs":
        errors.append("cgroup v2 is required")
    required = ["ansible-playbook", "lua5.3"]
    if mode in {"apply", "install"}:
        required.extend(["slurmd", "sacctmgr", "sinfo", "sbatch"])
    for command in required:
        if shutil.which(command) is None:
            errors.append(f"required command is missing: {command}")
    storage = resolved["resolved_policies"]["storage"]
    paths = resolved["derived"].get("paths") or {}
    if storage.get("quotas", {}).get("fail_if_unavailable") or storage.get("job_scratch", {}).get("required_for_jobs"):
        for label in ("data", "scratch"):
            path = paths.get(label)
            if path and command_stdout(["findmnt", "-no", "TARGET", str(path)]) is None:
                errors.append(f"required storage path is not mounted: {label}={path}")
            if path and Path(path).exists() and not os.access(path, os.W_OK):
                errors.append(f"required storage path is not writable: {label}={path}")
    if resolved["derived"]["has_gpus"]:
        if shutil.which("nvidia-smi") is None:
            errors.append("GPU profile requires nvidia-smi")
        else:
            gpu_lines = command_stdout([
                "nvidia-smi",
                "--query-gpu=index,name,uuid",
                "--format=csv,noheader",
            ])
            expected = int(resolved["hardware"]["gpus"])
            actual = len([line for line in (gpu_lines or "").splitlines() if line.strip()])
            if actual != expected:
                errors.append(f"GPU profile expects {expected} GPU(s), discovered {actual}")
            for index in range(expected):
                if not Path(f"/dev/nvidia{index}").exists():
                    errors.append(f"GPU profile expects /dev/nvidia{index}")
    if mode == "apply" and shutil.which("sacctmgr") is not None:
        cluster = command_stdout(["sacctmgr", "-nP", "show", "cluster", "format=cluster"])
        if cluster is None:
            errors.append("Slurm accounting is unavailable through sacctmgr")
    return errors


def token_input_hash(report: dict[str, Any], risk: str) -> str:
    payload = {
        "command": report.get("command"),
        "profile": report.get("profile"),
        "config_hash": report.get("config_hash"),
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
