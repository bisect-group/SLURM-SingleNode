from __future__ import annotations

import datetime as dt
import json
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .config import config_hash
from .units import memory_to_mb


DEFAULT_HEALTH_REPORT = Path("/run/slurm-single-node/scratch-health.json")
DEFAULT_UNHEALTHY_MARKER = Path("/run/slurm-single-node/scratch-unhealthy")
DEFAULT_FIXTURE_PREFIX = "ssn-test-"


def command_stdout(cmd: list[str]) -> str | None:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None


def command_rc(cmd: list[str]) -> int:
    return subprocess.run(cmd, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def quota_capability_report(
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
) -> dict[str, Any]:
    paths = resolved["derived"].get("paths") or {}
    quotas = resolved["resolved_policies"]["storage"].get("quotas") or {}
    users = users_doc.get("users") or {}
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "sync-users",
        "profile": resolved["profile"],
        "config_hash": config_hash(resolved),
        "mode": "quota_report",
        "fixture_prefix": fixture_prefix,
        "commands": {
            "setquota": shutil.which("setquota"),
            "quotaon": shutil.which("quotaon"),
            "repquota": shutil.which("repquota"),
            "findmnt": shutil.which("findmnt"),
        },
        "mounts": {},
        "fixture_users": sorted(name for name in users if name.startswith(fixture_prefix)),
    }
    for label in ("data", "scratch"):
        path = paths.get(label)
        limit = quotas.get(label)
        if not path or not limit:
            continue
        report["mounts"][label] = _quota_mount_capability(str(path), limit)
    return report


def apply_fixture_quotas(
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("fixture quota apply must run as root")
    report = quota_capability_report(users_doc, resolved, fixture_prefix=fixture_prefix)
    report["mode"] = "fixture_quota_apply"
    actions = []
    users = users_doc.get("users") or {}
    for username in report["fixture_users"]:
        if username not in users or not _user_exists(username):
            actions.append({"user": username, "status": "skipped", "reason": "fixture user does not exist"})
            continue
        for label, mount in report["mounts"].items():
            if not mount.get("can_apply"):
                actions.append(
                    {
                        "user": username,
                        "mount": label,
                        "status": "skipped",
                        "reason": mount.get("reason") or "quota support is not active",
                    }
                )
                continue
            hard_kb = int(mount["hard_kb"])
            rc = command_rc(["setquota", "-u", username, "0", str(hard_kb), "0", "0", mount["path"]])
            actions.append(
                {
                    "user": username,
                    "mount": label,
                    "path": mount["path"],
                    "hard_kb": hard_kb,
                    "status": "applied" if rc == 0 else "failed",
                    "rc": rc,
                }
            )
    report["actions"] = actions
    return report


def scratch_health_report(
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
) -> dict[str, Any]:
    storage = resolved["resolved_policies"]["storage"]
    job_scratch = storage.get("job_scratch") or {}
    paths = resolved["derived"].get("paths") or {}
    scratch_root = paths.get("scratch")
    jobs_root = job_scratch.get("root")
    checks: list[dict[str, str]] = []
    if not job_scratch.get("required_for_jobs") or not scratch_root:
        checks.append({"name": "scratch_policy", "status": "PASS", "detail": "scratch not required"})
        return _scratch_report(resolved, checks)

    checks.extend(
        [
            _path_check("scratch_root_exists", Path(str(scratch_root)), expect_dir=True),
            _not_symlink_check("scratch_root_not_symlink", Path(str(scratch_root))),
            _mount_check("scratch_mounted", str(scratch_root)),
            _writable_check("scratch_root_writable", Path(str(scratch_root))),
        ]
    )
    if jobs_root:
        jobs = Path(str(jobs_root))
        checks.extend(
            [
                _path_check("jobs_root_exists", jobs, expect_dir=True),
                _not_symlink_check("jobs_root_not_symlink", jobs),
                _writable_check("jobs_root_writable", jobs),
            ]
        )

    users = users_doc.get("users") or {}
    for username, user in sorted(users.items()):
        if user.get("status") != "active":
            continue
        for relative in ("", "cache", "tmp"):
            path = Path(str(scratch_root)) / username
            if relative:
                path = path / relative
            checks.append(_user_scratch_dir_check(username, path))
    return _scratch_report(resolved, checks)


def write_scratch_health_state(
    report: dict[str, Any],
    *,
    report_path: str | Path = DEFAULT_HEALTH_REPORT,
    marker_path: str | Path = DEFAULT_UNHEALTHY_MARKER,
) -> None:
    report_path = Path(report_path)
    marker_path = Path(marker_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report_path.chmod(0o644)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    if report.get("healthy"):
        try:
            marker_path.unlink()
        except FileNotFoundError:
            pass
    else:
        marker_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        marker_path.chmod(0o644)


def cleanup_operation_hash(report: dict[str, Any]) -> str:
    payload = {
        "root": report.get("root"),
        "jobs_root_excluded": report.get("jobs_root_excluded"),
        "age_days": report.get("age_days"),
        "candidates": report.get("candidates") or [],
    }
    return config_hash(payload)


def scratch_cleanup_report(
    *,
    root: str | Path,
    jobs_root: str | Path,
    age_days: int,
    profile: str | None = None,
    resolved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    jobs_root = Path(jobs_root).resolve()
    candidates = []
    if root.exists():
        cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - age_days * 86400
        for child in sorted(root.iterdir()):
            if child == jobs_root or child.is_symlink():
                continue
            try:
                child_stat = child.stat()
            except OSError:
                continue
            if child_stat.st_mtime > cutoff:
                continue
            candidates.append(
                {
                    "path": str(child),
                    "mtime": dt.datetime.fromtimestamp(child_stat.st_mtime, dt.timezone.utc).isoformat(),
                    "type": "directory" if child.is_dir() else "file",
                }
            )
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "scratch-cleanup",
        "mode": "report_only",
        "profile": profile,
        "config_hash": config_hash(resolved) if resolved is not None else None,
        "root": str(root),
        "jobs_root_excluded": str(jobs_root),
        "age_days": age_days,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    report["operation_hash"] = cleanup_operation_hash(report)
    return report


def apply_fixture_scratch_cleanup(
    report: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
) -> dict[str, Any]:
    root = Path(str(report.get("root", ""))).resolve()
    jobs_root = Path(str(report.get("jobs_root_excluded", ""))).resolve()
    if str(root) != "/scratch":
        raise ValueError(f"refusing scratch cleanup outside /scratch: {root}")
    if str(jobs_root) != "/scratch/jobs":
        raise ValueError(f"refusing jobs root outside /scratch/jobs: {jobs_root}")
    if report.get("operation_hash") != cleanup_operation_hash(report):
        raise ValueError("scratch cleanup report operation_hash does not match candidates")
    results = []
    for candidate in report.get("candidates") or []:
        path = Path(str(candidate.get("path", ""))).resolve()
        if not _safe_fixture_cleanup_path(path, root=root, fixture_prefix=fixture_prefix):
            results.append({"path": str(path), "status": "skipped", "reason": "not an allowed fixture path"})
            continue
        if path == jobs_root or path.is_symlink():
            results.append({"path": str(path), "status": "skipped", "reason": "protected path or symlink"})
            continue
        if not path.exists():
            results.append({"path": str(path), "status": "skipped", "reason": "already absent"})
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        results.append({"path": str(path), "status": "deleted"})
    applied = dict(report)
    applied["mode"] = "fixture_apply"
    applied["deletion_results"] = results
    return applied


def _quota_mount_capability(path: str, limit: Any) -> dict[str, Any]:
    limit_mb = memory_to_mb(limit)
    mount = {
        "path": path,
        "policy_quota": limit,
        "hard_kb": limit_mb * 1024,
        "findmnt": command_stdout(["findmnt", "-no", "TARGET,SOURCE,FSTYPE,OPTIONS", path]),
        "quotaon": command_stdout(["quotaon", "-p", path]),
        "repquota_rc": command_rc(["repquota", "-u", path]) if shutil.which("repquota") else 127,
        "setquota": shutil.which("setquota"),
    }
    active = (mount["quotaon"] is not None and "user quota" in str(mount["quotaon"]).lower()) or mount["repquota_rc"] == 0
    mount["active_user_quota"] = active
    mount["can_apply"] = bool(mount["setquota"] and active)
    if not mount["can_apply"]:
        mount["reason"] = "user quota is not active or setquota is missing"
    return mount


def _scratch_report(resolved: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] != "PASS"]
    return {
        "schema_version": 1,
        "command": "scratch-health",
        "profile": resolved["profile"],
        "config_hash": config_hash(resolved),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "healthy": not failed,
        "checks": checks,
    }


def _path_check(name: str, path: Path, *, expect_dir: bool) -> dict[str, str]:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        return {"name": name, "status": "FAIL", "detail": f"{path}: {exc}"}
    if expect_dir and not stat.S_ISDIR(path_stat.st_mode):
        return {"name": name, "status": "FAIL", "detail": f"{path} is not a directory"}
    return {"name": name, "status": "PASS", "detail": str(path)}


def _not_symlink_check(name: str, path: Path) -> dict[str, str]:
    try:
        if path.is_symlink():
            return {"name": name, "status": "FAIL", "detail": f"{path} is a symlink"}
    except OSError as exc:
        return {"name": name, "status": "FAIL", "detail": f"{path}: {exc}"}
    return {"name": name, "status": "PASS", "detail": str(path)}


def _mount_check(name: str, path: str) -> dict[str, str]:
    mount = command_stdout(["findmnt", "-no", "TARGET", path])
    return {
        "name": name,
        "status": "PASS" if mount else "FAIL",
        "detail": mount or f"{path} is not mounted",
    }


def _writable_check(name: str, path: Path) -> dict[str, str]:
    return {
        "name": name,
        "status": "PASS" if os.access(path, os.W_OK | os.X_OK) else "FAIL",
        "detail": str(path),
    }


def _user_scratch_dir_check(username: str, path: Path) -> dict[str, str]:
    try:
        entry = pwd.getpwnam(username)
        path_stat = path.lstat()
    except (KeyError, OSError) as exc:
        return {"name": f"user_scratch_{username}_{path.name}", "status": "FAIL", "detail": f"{path}: {exc}"}
    mode = stat.S_IMODE(path_stat.st_mode)
    owner_bits = mode & 0o700
    ok = (
        stat.S_ISDIR(path_stat.st_mode)
        and not stat.S_ISLNK(path_stat.st_mode)
        and path_stat.st_uid == entry.pw_uid
        and path_stat.st_gid == entry.pw_gid
        and owner_bits == 0o700
    )
    detail = f"{path} uid={path_stat.st_uid} gid={path_stat.st_gid} mode={mode:o}"
    return {"name": f"user_scratch_{username}_{path.name}", "status": "PASS" if ok else "FAIL", "detail": detail}


def _safe_fixture_cleanup_path(path: Path, *, root: Path, fixture_prefix: str) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path.parent == root and path.name.startswith(fixture_prefix) and fixture_prefix.startswith("ssn-test-")


def _user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False
