from __future__ import annotations

import datetime as dt
import grp
import json
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .ops import command_stdout
from .units import memory_to_mb


DEFAULT_FIXTURE_PREFIX = "ssn-test-"
LOGIN_STATE_PATH = Path("/etc/slurm-single-node/login-isolation.json")
GPU_MODE_PATH = Path("/etc/slurm-single-node/gpu-isolation-mode")
GPU_STATUS_SNAPSHOT = Path("/run/slurm-single-node/gpu-status.json")


def login_isolation_report(
    users_doc: dict[str, Any],
    state_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
    mode: str = "cgroup",
) -> dict[str, Any]:
    targets = _login_targets(users_doc, state_doc, resolved, fixture_prefix=fixture_prefix)
    return {
        "schema_version": 1,
        "command": "login-isolation",
        "profile": resolved["profile"],
        "mode": mode,
        "fixture_prefix": fixture_prefix,
        "generated_at": _now(),
        "limits": _login_limits(resolved),
        "gpu": {
            "profile_has_gpus": bool(resolved["derived"]["has_gpus"]),
            "requested_denial": (resolved["resolved_policies"]["login"].get("gpu_outside_slurm") or {}).get("direct_access"),
            "mode_file": str(GPU_MODE_PATH),
        },
        "targets": targets,
    }


def apply_login_isolation(
    users_doc: dict[str, Any],
    state_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
    mode: str = "cgroup",
    report_path: str | Path = LOGIN_STATE_PATH,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("login isolation apply must run as root")
    if mode not in {"cgroup", "acl", "limits", "disabled"}:
        raise ValueError(f"invalid login isolation mode: {mode}")

    report = login_isolation_report(
        users_doc,
        state_doc,
        resolved,
        fixture_prefix=fixture_prefix,
        mode=mode,
    )
    report["applied_at"] = _now()
    report["actions"] = []
    enabled = mode != "disabled"
    gpu_mode = "disabled"
    if enabled and resolved["derived"]["has_gpus"]:
        gpu_mode = "acl" if mode == "acl" else "cgroup" if mode == "cgroup" else "disabled"

    for target in report["targets"]:
        dropin = Path(target["dropin"])
        dropin.parent.mkdir(parents=True, exist_ok=True)
        content = _slice_dropin_content(resolved, gpu_mode=gpu_mode, enabled=enabled)
        dropin.write_text(content)
        dropin.chmod(0o644)
        report["actions"].append({"user": target["user"], "action": "write_slice_dropin", "path": str(dropin)})

    GPU_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GPU_MODE_PATH.write_text(gpu_mode + "\n")
    GPU_MODE_PATH.chmod(0o644)
    report["actions"].append({"action": "write_gpu_mode", "path": str(GPU_MODE_PATH), "mode": gpu_mode})

    if mode == "acl" and resolved["derived"]["has_gpus"]:
        report["gpu"]["acl_baseline"] = apply_gpu_acl_baseline()

    _systemctl_daemon_reload(report)
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report_path.chmod(0o644)
    return report


def login_isolation_status(
    users_doc: dict[str, Any],
    state_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
) -> dict[str, Any]:
    report = login_isolation_report(
        users_doc,
        state_doc,
        resolved,
        fixture_prefix=fixture_prefix,
        mode=_read_text(GPU_MODE_PATH).strip() or "unknown",
    )
    report["status"] = []
    report["snapshot"] = gpu_snapshot_status()
    for target in report["targets"]:
        uid = str(target["uid"])
        unit = f"user-{uid}.slice"
        status = {
            "user": target["user"],
            "uid": target["uid"],
            "unit": unit,
            "dropin_exists": Path(target["dropin"]).exists(),
            "cgroup": _systemctl_show(unit, ["ControlGroup"]),
            "properties": _systemctl_show(
                unit,
                ["CPUQuotaPerSecUSec", "MemoryMax", "TasksMax", "IOWeight", "DevicePolicy"],
            ),
        }
        report["status"].append(status)
    return report


def login_isolation_status_for_report(resolved: dict[str, Any]) -> dict[str, Any]:
    try:
        from .users import load_state, load_users

        users_doc = load_users("/etc/slurm-single-node/users.yml")
        state_doc = load_state("/var/lib/slurm-single-node/users-state.yml")
        return login_isolation_status(users_doc, state_doc, resolved)
    except Exception as exc:
        return {"schema_version": 1, "status": "unavailable", "error": str(exc)}


def apply_gpu_acl_baseline() -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("GPU ACL baseline must run as root")
    actions = []
    for device in gpu_device_paths(include_caps=True):
        try:
            os.chown(device, 0, 0, follow_symlinks=False)
            device.chmod(0o660)
            actions.append({"path": str(device), "status": "restricted", "mode": "0660"})
        except OSError as exc:
            actions.append({"path": str(device), "status": "error", "error": str(exc)})
    return {"mode": "acl", "devices": actions}


def restore_gpu_device_permissions() -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("GPU device restore must run as root")
    actions = []
    for device in gpu_device_paths(include_caps=False):
        try:
            device.chmod(0o666)
            actions.append({"path": str(device), "status": "world_rw", "mode": "0666"})
        except OSError as exc:
            actions.append({"path": str(device), "status": "error", "error": str(exc)})
    return {"mode": "restored", "devices": actions}


def collect_gpu_status_snapshot(
    resolved: dict[str, Any] | None = None,
    *,
    snapshot_path: str | Path = GPU_STATUS_SNAPSHOT,
) -> dict[str, Any]:
    has_gpus = True if resolved is None else bool(resolved.get("derived", {}).get("has_gpus"))
    snapshot = {
        "schema_version": 1,
        "generated_at": _now(),
        "source": "root_service_collector",
        "profile": resolved.get("profile") if resolved else None,
        "gpus": _query_gpus() if has_gpus else [],
        "slurm_jobs": _slurm_gpu_jobs() if has_gpus else [],
    }
    _attach_jobs_to_single_gpu(snapshot)
    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    path.chmod(0o644)
    return snapshot


def gpu_snapshot_status(snapshot_path: str | Path = GPU_STATUS_SNAPSHOT) -> dict[str, Any]:
    path = Path(snapshot_path)
    if not path.exists():
        return {"path": str(path), "exists": False, "fresh": False}
    try:
        payload = json.loads(path.read_text())
        generated_at = dt.datetime.fromisoformat(payload.get("generated_at", ""))
        age = (dt.datetime.now(dt.timezone.utc) - generated_at).total_seconds()
    except Exception:
        return {"path": str(path), "exists": True, "fresh": False, "error": "unreadable snapshot"}
    return {
        "path": str(path),
        "exists": True,
        "fresh": age <= 30,
        "age_seconds": age,
        "gpu_count": len(payload.get("gpus") or []),
        "job_count": len(payload.get("slurm_jobs") or []),
    }


def gpu_device_paths(*, include_caps: bool = True) -> list[Path]:
    paths: list[Path] = []
    for name in ("nvidiactl", "nvidia-uvm", "nvidia-uvm-tools", "nvidia-modeset"):
        path = Path("/dev") / name
        if path.exists():
            paths.append(path)
    paths.extend(sorted(Path("/dev").glob("nvidia[0-9]*")))
    if include_caps:
        caps = Path("/dev/nvidia-caps")
        if caps.is_dir():
            paths.extend(sorted(item for item in caps.iterdir() if _is_char_device(item)))
    return [path for path in paths if _is_char_device(path)]


def _login_targets(
    users_doc: dict[str, Any],
    state_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str,
) -> list[dict[str, Any]]:
    admins = set((resolved.get("admins") or {}).get("users") or [])
    targets = []
    for username, user in sorted((users_doc.get("users") or {}).items()):
        state = (state_doc.get("users") or {}).get(username) or {}
        if not username.startswith(fixture_prefix):
            continue
        if user.get("status") != "active":
            continue
        if username in admins:
            continue
        if not state.get("managed"):
            continue
        try:
            entry = pwd.getpwnam(username)
        except KeyError:
            targets.append({"user": username, "status": "missing_unix_user"})
            continue
        targets.append(
            {
                "user": username,
                "uid": entry.pw_uid,
                "gid": entry.pw_gid,
                "slice": f"user-{entry.pw_uid}.slice",
                "dropin": str(_dropin_path(entry.pw_uid)),
                "status": "targeted",
            }
        )
    return targets


def _login_limits(resolved: dict[str, Any]) -> dict[str, Any]:
    policy = resolved["resolved_policies"]["login"]["non_admin_limits"]
    cpus = int(policy["cpus"])
    memory_mb = memory_to_mb(policy["memory"])
    return {
        "cpus": cpus,
        "cpu_quota": f"{cpus * 100}%",
        "memory": policy["memory"],
        "memory_max": f"{memory_mb}M",
        "tasks": int(policy["tasks"]),
        "io_weight": _io_weight(policy.get("io_weight")),
        "applies_to_slurm_jobs": bool(policy.get("applies_to_slurm_jobs")),
    }


def _slice_dropin_content(resolved: dict[str, Any], *, gpu_mode: str, enabled: bool) -> str:
    if not enabled:
        return "\n".join(
            [
                "# Managed by Slurm Single-Node.",
                "# Login isolation is disabled; this file intentionally resets SSN controls.",
                "[Slice]",
                "CPUQuota=",
                "MemoryMax=",
                "TasksMax=",
                "IOWeight=",
                "DevicePolicy=auto",
                "DeviceAllow=",
                "",
            ]
        )
    limits = _login_limits(resolved)
    lines = [
        "# Managed by Slurm Single-Node.",
        "# Fixture-scoped login confinement; Slurm job cgroups remain Slurm-owned.",
        "[Slice]",
        f"CPUQuota={limits['cpu_quota']}",
        f"MemoryMax={limits['memory_max']}",
        f"TasksMax={limits['tasks']}",
        f"IOWeight={limits['io_weight']}",
    ]
    if gpu_mode == "cgroup":
        lines.extend(
            [
                "DevicePolicy=closed",
                "DeviceAllow=/dev/null rw",
                "DeviceAllow=/dev/zero rw",
                "DeviceAllow=/dev/full rw",
                "DeviceAllow=/dev/random r",
                "DeviceAllow=/dev/urandom r",
                "DeviceAllow=/dev/tty rw",
                "DeviceAllow=/dev/ptmx rw",
                "DeviceAllow=char-pts rw",
                "DeviceAllow=char-tty rw",
            ]
        )
    else:
        lines.append("DevicePolicy=auto")
    lines.append("")
    return "\n".join(lines)


def _dropin_path(uid: int) -> Path:
    return Path("/etc/systemd/system") / f"user-{uid}.slice.d" / "ssn-login-isolation.conf"


def _systemctl_daemon_reload(report: dict[str, Any]) -> None:
    rc = subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).returncode
    report["actions"].append({"action": "systemctl_daemon_reload", "rc": rc})


def _systemctl_show(unit: str, properties: list[str]) -> dict[str, str]:
    if shutil.which("systemctl") is None:
        return {}
    cmd = ["systemctl", "show", unit]
    for prop in properties:
        cmd.extend(["-p", prop])
    output = command_stdout(cmd) or ""
    values = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key:
            values[key] = value
    return values


def _io_weight(value: Any) -> int:
    if str(value).lower() == "low":
        return 50
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 50
    return max(1, min(10000, parsed))


def _query_gpus() -> list[dict[str, Any]]:
    nvidia_smi = _nvidia_smi_path()
    if not nvidia_smi:
        return []
    output = command_stdout(
        [
            nvidia_smi,
            "--query-gpu=index,name,uuid,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if not output:
        return []
    gpus = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 8:
            continue
        gpus.append(
            {
                "index": parts[0],
                "name": parts[1],
                "uuid": parts[2],
                "pci_bus_id": parts[3],
                "utilization_gpu_percent": _int_or_none(parts[4]),
                "memory_used_mb": _int_or_none(parts[5]),
                "memory_total_mb": _int_or_none(parts[6]),
                "temperature_c": _int_or_none(parts[7]),
                "slurm_jobs": [],
            }
        )
    return gpus


def _slurm_gpu_jobs() -> list[dict[str, Any]]:
    if shutil.which("squeue") is None or shutil.which("scontrol") is None:
        return []
    output = command_stdout(["squeue", "-h", "-o", "%i|%u|%T|%j"]) or ""
    jobs = []
    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        job_id, user, state, name = parts
        detail = command_stdout(["scontrol", "show", "job", "-o", job_id]) or ""
        gpu_count = _parse_gpu_count(detail)
        if gpu_count < 1:
            continue
        jobs.append(
            {
                "job_id": job_id,
                "user": user,
                "state": state,
                "name": name,
                "gpu_count": gpu_count,
                "raw": detail,
            }
        )
    return jobs


def _attach_jobs_to_single_gpu(snapshot: dict[str, Any]) -> None:
    gpus = snapshot.get("gpus") or []
    if len(gpus) != 1:
        return
    for job in snapshot.get("slurm_jobs") or []:
        gpus[0].setdefault("slurm_jobs", []).append(
            {key: job[key] for key in ("job_id", "user", "state", "name", "gpu_count") if key in job}
        )


def _parse_gpu_count(detail: str) -> int:
    count = 0
    for token in detail.replace(",", " ").split():
        if "gres/gpu" not in token and "gpu:" not in token:
            continue
        for sep in ("=", ":"):
            if sep in token:
                tail = token.rsplit(sep, 1)[-1]
                if tail.isdigit():
                    count = max(count, int(tail))
    return count


def _nvidia_smi_path() -> str | None:
    for candidate in ("/usr/bin/nvidia-smi", "/bin/nvidia-smi"):
        if Path(candidate).exists():
            return candidate
    return shutil.which("nvidia-smi")


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _is_char_device(path: Path) -> bool:
    try:
        return stat.S_ISCHR(path.stat().st_mode)
    except OSError:
        return False


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
