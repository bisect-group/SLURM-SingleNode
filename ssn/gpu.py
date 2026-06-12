from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .config import config_hash
from .login import GPU_STATUS_SNAPSHOT, gpu_snapshot_status
from .ops import command_stdout, slurm_jobs


GPU_RECOVERY_RISK = "cpu_only_recovery"
GPU_RECOVERY_STATE = Path("/var/lib/slurm-single-node/gpu-recovery-state.json")


def gpu_verification_report(
    resolved: dict[str, Any],
    *,
    conf_dir: str | Path = "/etc/slurm",
    snapshot_path: str | Path = GPU_STATUS_SNAPSHOT,
) -> dict[str, Any]:
    expected = int(resolved["hardware"].get("gpus") or 0)
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "gpu-verify",
        "profile": resolved["profile"],
        "config_hash": config_hash(resolved),
        "generated_at": _now(),
        "expected_gpus": expected,
        "profile_has_gpus": bool(resolved["derived"]["has_gpus"]),
        "checks": [],
        "nvidia": _nvidia_query(),
        "devices": _device_report(expected),
        "gres": _gres_report(resolved, conf_dir=conf_dir),
        "slurm": _slurm_gpu_report(resolved),
        "snapshot": gpu_snapshot_status(snapshot_path),
        "modes": _gpu_mode_report(resolved),
    }
    _add_checks(report)
    report["healthy"] = not any(check["status"] == "FAIL" for check in report["checks"])
    return report


def gpu_verification_errors(report: dict[str, Any]) -> list[str]:
    return [f"{check['name']}: {check['detail']}" for check in report.get("checks", []) if check.get("status") == "FAIL"]


def gpu_recovery_plan(
    resolved: dict[str, Any],
    recovery_resolved: dict[str, Any],
    *,
    fixture_prefix: str = "ssn-test-",
) -> dict[str, Any]:
    jobs = gpu_jobs()
    fixture_jobs = [job for job in jobs if str(job.get("user", "")).startswith(fixture_prefix)]
    nonfixture_jobs = [job for job in jobs if not str(job.get("user", "")).startswith(fixture_prefix)]
    operation = {
        "profile": resolved["profile"],
        "recovery_profile": recovery_resolved["profile"],
        "fixture_prefix": fixture_prefix,
        "gpu_jobs": [
            {key: job.get(key) for key in ("id", "state", "user", "name", "gpu_count")}
            for job in jobs
        ],
    }
    return {
        "schema_version": 1,
        "command": "gpu-recovery",
        "profile": resolved["profile"],
        "recovery_profile": recovery_resolved["profile"],
        "config_hash": config_hash(resolved),
        "recovery_config_hash": config_hash(recovery_resolved),
        "risk": GPU_RECOVERY_RISK,
        "operation_hash": config_hash(operation),
        "generated_at": _now(),
        "fixture_prefix": fixture_prefix,
        "gpu_jobs": jobs,
        "fixture_gpu_jobs": fixture_jobs,
        "nonfixture_gpu_jobs": nonfixture_jobs,
        "actions": _recovery_actions(fixture_jobs),
    }


def gpu_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job in slurm_jobs():
        detail = command_stdout(["scontrol", "show", "job", str(job["id"])]) or ""
        gpu_count = _parse_gpu_count(detail)
        if gpu_count < 1:
            continue
        jobs.append(
            {
                "id": job["id"],
                "state": job.get("state", "UNKNOWN"),
                "user": job.get("user", ""),
                "name": job.get("name", ""),
                "gpu_count": gpu_count,
                "detail": _compact_slurm_detail(detail),
            }
        )
    return jobs


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    path.chmod(0o640)


def _nvidia_query() -> dict[str, Any]:
    if shutil.which("nvidia-smi") is None:
        return {"available": False, "query": None, "gpus": []}
    query = command_stdout(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,pci.bus_id,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus = []
    for line in (query or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 5:
            gpus.append(
                {
                    "index": parts[0],
                    "name": parts[1],
                    "uuid": parts[2],
                    "pci_bus_id": parts[3],
                    "memory_total_mb": parts[4],
                }
            )
    return {"available": query is not None, "query": query, "gpus": gpus}


def _device_report(expected: int) -> dict[str, Any]:
    devices = {}
    for index in range(expected):
        path = Path(f"/dev/nvidia{index}")
        devices[str(path)] = {"exists": path.exists(), "is_char_device": _is_char_device(path)}
    for name in ("nvidiactl", "nvidia-uvm", "nvidia-modeset"):
        path = Path("/dev") / name
        devices[str(path)] = {"exists": path.exists(), "is_char_device": _is_char_device(path)}
    return devices


def _gres_report(resolved: dict[str, Any], *, conf_dir: str | Path) -> dict[str, Any]:
    conf_root = Path(conf_dir)
    gres_conf = _read_text(conf_root / "gres.conf")
    slurm_conf = _read_text(conf_root / "slurm.conf")
    return {
        "rendered_entries": resolved["derived"].get("gres_entries") or [],
        "installed_gres_conf": gres_conf,
        "installed_slurm_has_gres_types": "GresTypes=gpu" in slurm_conf,
        "installed_entry_count": sum(1 for line in gres_conf.splitlines() if line.strip().startswith("Name=gpu")),
    }


def _slurm_gpu_report(resolved: dict[str, Any]) -> dict[str, Any]:
    node = resolved["identity"]["node_name"]
    node_detail = command_stdout(["scontrol", "show", "node", node]) or ""
    return {
        "node": node,
        "node_detail": _compact_slurm_detail(node_detail),
        "node_has_gres": "Gres=gpu" in node_detail,
        "gpu_jobs": gpu_jobs(),
    }


def _gpu_mode_report(resolved: dict[str, Any]) -> dict[str, Any]:
    modes = resolved.get("hardware", {}).get("gpu_modes") or {}
    mig_query = command_stdout(["nvidia-smi", "--query-gpu=mig.mode.current", "--format=csv,noheader,nounits"])
    mps_process = subprocess.run(
        ["pgrep", "-f", "nvidia-cuda-mps-control"],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0 if shutil.which("pgrep") else False
    mig_enabled = any(line.strip().lower() == "enabled" for line in (mig_query or "").splitlines())
    return {
        "policy": modes,
        "mig_query": mig_query,
        "mig_enabled": mig_enabled,
        "mps_process_running": mps_process,
        "shared_gpu_config_detected": False,
    }


def _add_checks(report: dict[str, Any]) -> None:
    if not report["profile_has_gpus"]:
        report["checks"].append(_check("profile_has_gpus", True, "CPU-only profile; GPU verification is informational."))
        return
    expected = int(report["expected_gpus"])
    discovered = len(report.get("nvidia", {}).get("gpus") or [])
    report["checks"].append(_check("nvidia_smi", report["nvidia"]["available"], "nvidia-smi query"))
    report["checks"].append(_check("gpu_count", discovered == expected, f"expected={expected} discovered={discovered}"))
    for path, detail in sorted((report.get("devices") or {}).items()):
        if path.startswith("/dev/nvidia") and path[len("/dev/nvidia"):].isdigit():
            report["checks"].append(_check(f"device_{Path(path).name}", detail["exists"] and detail["is_char_device"], path))
    gres = report.get("gres") or {}
    report["checks"].append(_check("rendered_gres_entries", len(gres.get("rendered_entries") or []) == expected, f"expected={expected}"))
    report["checks"].append(_check("installed_gres_conf", int(gres.get("installed_entry_count") or 0) == expected, f"expected={expected} installed={gres.get('installed_entry_count')}"))
    report["checks"].append(_check("installed_slurm_gres_types", bool(gres.get("installed_slurm_has_gres_types")), "GresTypes=gpu"))
    slurm = report.get("slurm") or {}
    report["checks"].append(_check("slurm_node_gres", bool(slurm.get("node_has_gres")), slurm.get("node", "")))
    snapshot = report.get("snapshot") or {}
    report["checks"].append(_check("gpu_status_snapshot", bool(snapshot.get("exists")) and bool(snapshot.get("fresh")), str(snapshot.get("path", ""))))
    active_gpu_jobs = slurm.get("gpu_jobs") or []
    if active_gpu_jobs:
        snapshot_jobs = 0
        snapshot_path = Path(snapshot.get("path") or "")
        if snapshot_path.exists():
            try:
                payload = json.loads(snapshot_path.read_text())
                snapshot_jobs = len(payload.get("slurm_jobs") or [])
            except Exception:
                snapshot_jobs = 0
        report["checks"].append(_check("slurm_gpu_job_mapping", snapshot_jobs >= len(active_gpu_jobs), f"snapshot_jobs={snapshot_jobs} active_gpu_jobs={len(active_gpu_jobs)}"))
    else:
        report["checks"].append(_check("slurm_gpu_job_mapping", True, "no active GPU jobs"))
    modes = report.get("modes") or {}
    policy = modes.get("policy") or {}
    report["checks"].append(_check("mig_fail_closed", not (policy.get("mig") == "fail_closed" and modes.get("mig_enabled")), f"mig_enabled={modes.get('mig_enabled')}"))
    report["checks"].append(_check("mps_fail_closed", not (policy.get("mps") == "fail_closed" and modes.get("mps_process_running")), f"mps_running={modes.get('mps_process_running')}"))
    report["checks"].append(_check("shared_gpu_fail_closed", not (policy.get("shared_gpu") == "fail_closed" and modes.get("shared_gpu_config_detected")), f"shared_gpu={modes.get('shared_gpu_config_detected')}"))


def _recovery_actions(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    for job in jobs:
        state = str(job.get("state", ""))
        action = "hold" if state in {"PENDING", "CONFIGURING"} else "cancel"
        actions.append({"job_id": job.get("id"), "user": job.get("user"), "state": state, "action": action})
    return actions


def _parse_gpu_count(detail: str) -> int:
    count = 0
    for match in re.finditer(r"(?:gres/)?gpu(?::[^:=,\s]+)?[:=](\d+)", detail):
        count = max(count, int(match.group(1)))
    return count


def _compact_slurm_detail(detail: str) -> str:
    return " ".join(detail.split())


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _is_char_device(path: Path) -> bool:
    try:
        return path.exists() and stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
