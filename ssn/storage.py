from __future__ import annotations

import datetime as dt
import json
import os
import pwd
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import config_hash
from .units import memory_to_mb


DEFAULT_HEALTH_REPORT = Path("/run/slurm-single-node/scratch-health.json")
DEFAULT_UNHEALTHY_MARKER = Path("/run/slurm-single-node/scratch-unhealthy")
DEFAULT_FIXTURE_PREFIX = "ssn-test-"
DEFAULT_QUOTA_LABELS = ("home", "data", "scratch")
STORAGE_QUOTA_ENABLE_RISK = "storage_quota_enable"
SCRATCH_CLEANUP_RISK = "scratch_cleanup"
FIXTURE_SCRATCH_CLEANUP_RISK = "fixture_scratch_cleanup"


def command_stdout(cmd: list[str]) -> str | None:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None


def command_rc(cmd: list[str]) -> int:
    return subprocess.run(cmd, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def command_result(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"cmd": cmd, "rc": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def quota_capability_report(
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
    scope: str = "fixture",
    quota_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if scope not in {"fixture", "all_managed"}:
        raise ValueError(f"unsupported quota report scope: {scope}")
    paths = resolved["derived"].get("paths") or {}
    quotas = resolved["resolved_policies"]["storage"].get("quotas") or {}
    quota_overrides = quota_overrides or {}
    users = users_doc.get("users") or {}
    fixture_users = sorted(name for name in users if name.startswith(fixture_prefix))
    if scope == "all_managed":
        selected_users = sorted(
            name for name, user in users.items() if (user or {}).get("status") == "active"
        )
    else:
        selected_users = fixture_users
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "sync-users",
        "profile": resolved["profile"],
        "config_hash": config_hash(resolved),
        "mode": "quota_report",
        "scope": scope,
        "fixture_prefix": fixture_prefix,
        "commands": {
            "setquota": shutil.which("setquota"),
            "quotaon": shutil.which("quotaon"),
            "repquota": shutil.which("repquota"),
            "quota": shutil.which("quota"),
            "findmnt": shutil.which("findmnt"),
        },
        "mounts": {},
        "fixture_users": fixture_users,
        "managed_users": selected_users,
        "users": [],
    }
    for label in DEFAULT_QUOTA_LABELS:
        path = paths.get(label)
        limit = quota_overrides.get(label, quotas.get(label))
        if not path or not limit:
            continue
        report["mounts"][label] = _quota_mount_capability(str(path), limit)
    for username in selected_users:
        user = users.get(username) or {}
        report["users"].append(
            _quota_user_report(
                username,
                user,
                report["mounts"],
                apply_allowed=scope == "fixture" and username.startswith(fixture_prefix),
            )
        )
    return report


def apply_fixture_quotas(
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    fixture_prefix: str = DEFAULT_FIXTURE_PREFIX,
    quota_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("fixture quota apply must run as root")
    report = quota_capability_report(
        users_doc,
        resolved,
        fixture_prefix=fixture_prefix,
        scope="fixture",
        quota_overrides=quota_overrides,
    )
    report["mode"] = "fixture_quota_apply"
    actions = []
    users = users_doc.get("users") or {}
    for username in report["fixture_users"]:
        if not username.startswith(fixture_prefix) or not fixture_prefix.startswith("ssn-test-"):
            actions.append({"user": username, "status": "skipped", "reason": "not an allowed fixture user"})
            continue
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
            quota_target = mount.get("mountpoint") or mount["path"]
            rc = command_rc(["setquota", "-u", username, "0", str(hard_kb), "0", "0", quota_target])
            actions.append(
                {
                    "user": username,
                    "mount": label,
                    "path": quota_target,
                    "hard_kb": hard_kb,
                    "status": "applied" if rc == 0 else "failed",
                    "rc": rc,
                }
            )
    report["actions"] = actions
    return report


def parse_fixture_quota_overrides(values: list[str] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for raw in values or []:
        label, separator, value = raw.partition("=")
        if separator != "=" or not label or not value:
            raise ValueError(f"invalid fixture quota override: {raw}")
        if label not in DEFAULT_QUOTA_LABELS:
            raise ValueError(f"unsupported fixture quota label: {label}")
        memory_to_mb(value)
        overrides[label] = value
    return overrides


def storage_quota_operation_hash(report: dict[str, Any]) -> str:
    payload = {
        "profile": report.get("profile"),
        "mount_labels": report.get("mount_labels") or [],
        "fstab_path": report.get("fstab_path"),
        "mounts": {
            label: {
                "path": mount.get("path"),
                "mountpoint": mount.get("mountpoint"),
                "source": mount.get("source"),
                "fstype": mount.get("fstype"),
                "fstab": mount.get("fstab"),
                "proposed_options": mount.get("proposed_options"),
            }
            for label, mount in sorted((report.get("mounts") or {}).items())
        },
    }
    return config_hash({"storage_quota_enable": payload})


def storage_quota_plan(
    resolved: dict[str, Any],
    *,
    labels: list[str] | None = None,
    fstab_path: str | Path = "/etc/fstab",
) -> dict[str, Any]:
    labels = _selected_quota_labels(resolved, labels)
    report = _storage_quota_report(resolved, labels=labels, fstab_path=fstab_path, mode="plan")
    report["risk"] = STORAGE_QUOTA_ENABLE_RISK
    report["operation_hash"] = storage_quota_operation_hash(report)
    return report


def storage_quota_status(
    resolved: dict[str, Any],
    *,
    labels: list[str] | None = None,
    fstab_path: str | Path = "/etc/fstab",
) -> dict[str, Any]:
    labels = _selected_quota_labels(resolved, labels)
    report = _storage_quota_report(resolved, labels=labels, fstab_path=fstab_path, mode="status")
    report["operation_hash"] = storage_quota_operation_hash(report)
    return report


def enable_storage_quotas(
    plan: dict[str, Any],
    *,
    fstab_path: str | Path = "/etc/fstab",
    backup_root: str | Path = "/var/backups/slurm-single-node/fstab",
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("storage quota enable must run as root")
    if plan.get("operation_hash") != storage_quota_operation_hash(plan):
        raise ValueError("storage quota plan operation_hash does not match selected mounts")
    if plan.get("risk") != STORAGE_QUOTA_ENABLE_RISK:
        raise ValueError("storage quota plan has the wrong risk")
    fstab_path = Path(fstab_path)
    backup_root = Path(backup_root)
    before = fstab_path.read_text()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_dir = backup_root / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "fstab"
    backup_path.write_text(before)
    backup_path.chmod(0o640)

    report = dict(plan)
    report["mode"] = "enable"
    report["started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["fstab_backup"] = str(backup_path)
    report["actions"] = []
    report["recovery_commands"] = []
    mounts = plan.get("mounts") or {}
    skipped_mounts = [
        {"label": label, "mountpoint": mount.get("mountpoint"), "reason": "already active" if not mount.get("enable_needed") else "cannot enable"}
        for label, mount in mounts.items()
        if not mount.get("enable_needed") or not mount.get("can_enable")
    ]
    selected_mounts = _dedupe_mountpoints(
        mount.get("mountpoint")
        for mount in mounts.values()
        if mount.get("enable_needed") and mount.get("can_enable")
    )
    activation_mounts = _dedupe_mountpoints(
        mount.get("mountpoint")
        for mount in mounts.values()
        if (
            mount.get("enable_needed")
            and mount.get("can_enable")
            and not (mount.get("active_user_quota") and mount.get("active_group_quota"))
        )
    )
    for skipped in skipped_mounts:
        report["actions"].append({"action": "skip", **skipped})
    try:
        cannot_enable = [item for item in skipped_mounts if item.get("reason") == "cannot enable"]
        if cannot_enable:
            labels = ", ".join(str(item.get("label")) for item in cannot_enable)
            raise RuntimeError(f"cannot enable quotas for selected mount(s): {labels}")
        updated, changed_mounts = _fstab_with_quota_options(before, selected_mounts)
        if updated != before:
            _atomic_write(fstab_path, updated)
            report["actions"].append({"action": "update_fstab", "status": "changed", "mounts": changed_mounts})
        else:
            report["actions"].append({"action": "update_fstab", "status": "ok", "mounts": []})
        for mountpoint in selected_mounts:
            for command in (["mount", "-o", "remount", mountpoint],):
                result = command_result(command)
                report["actions"].append({"action": command[0], "mountpoint": mountpoint, **result})
                if result["rc"] != 0:
                    raise RuntimeError(f"{' '.join(command)} failed: {result['stderr'] or result['stdout']}")
        for mountpoint in activation_mounts:
            for command in (["quotacheck", "-cugm", mountpoint], ["quotaon", "-ug", mountpoint]):
                result = command_result(command)
                report["actions"].append({"action": command[0], "mountpoint": mountpoint, **result})
                if result["rc"] != 0:
                    raise RuntimeError(f"{' '.join(command)} failed: {result['stderr'] or result['stdout']}")
        report["status"] = "enabled"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        active_after_failure = [
            mountpoint
            for mountpoint in selected_mounts
            if _quota_active_for_mount(mountpoint).get("active_user_quota") or _quota_active_for_mount(mountpoint).get("active_group_quota")
        ]
        report["active_after_failure"] = active_after_failure
        if not active_after_failure:
            try:
                fstab_path.write_text(before)
                for mountpoint in selected_mounts:
                    command_result(["mount", "-o", "remount", mountpoint])
                report["rollback"] = "restored_fstab_backup"
            except Exception as rollback_exc:
                report["rollback"] = f"failed: {rollback_exc}"
        else:
            report["rollback"] = "skipped_partial_quota_activation"
        report["recovery_commands"] = [
            f"cp {backup_path} {fstab_path}",
            *[f"mount -o remount {mountpoint}" for mountpoint in selected_mounts],
            *[f"quotaoff -ug {mountpoint}" for mountpoint in active_after_failure],
        ]
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["post_status"] = {
        label: _quota_mount_capability(
            str(mount.get("path")),
            mount.get("policy_quota"),
            fstab_path=fstab_path,
        )
        for label, mount in mounts.items()
        if mount.get("path") and mount.get("policy_quota")
    }
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
    cleanup_users: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    jobs_root = Path(jobs_root).resolve()
    candidates = []
    if root.exists():
        cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - age_days * 86400
        if cleanup_users:
            for username in sorted(set(cleanup_users)):
                for base_name in ("cache", "tmp"):
                    base = root / username / base_name
                    if not base.exists() or base.is_symlink() or not base.is_dir():
                        continue
                    for child in sorted(base.iterdir()):
                        if child == jobs_root or child.is_symlink():
                            continue
                        try:
                            child_stat = child.stat()
                        except OSError:
                            continue
                        if child_stat.st_mtime > cutoff:
                            continue
                        candidates.append(_cleanup_candidate(child, username=username, base=str(base)))
        else:
            for child in sorted(root.iterdir()):
                if child == jobs_root or child.is_symlink():
                    continue
                try:
                    child_stat = child.stat()
                except OSError:
                    continue
                if child_stat.st_mtime > cutoff:
                    continue
                candidates.append(_cleanup_candidate(child))
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "scratch-cleanup",
        "mode": "report_only",
        "risk": SCRATCH_CLEANUP_RISK if cleanup_users else FIXTURE_SCRATCH_CLEANUP_RISK,
        "profile": profile,
        "config_hash": config_hash(resolved) if resolved is not None else None,
        "root": str(root),
        "jobs_root_excluded": str(jobs_root),
        "age_days": age_days,
        "cleanup_users": sorted(set(cleanup_users or [])),
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


def apply_user_scratch_cleanup(
    report: dict[str, Any],
    *,
    allowed_users: set[str],
    require_scratch_root: bool = True,
) -> dict[str, Any]:
    root = Path(str(report.get("root", ""))).resolve()
    jobs_root = Path(str(report.get("jobs_root_excluded", ""))).resolve()
    if require_scratch_root and str(root) != "/scratch":
        raise ValueError(f"refusing scratch cleanup outside /scratch: {root}")
    if require_scratch_root and str(jobs_root) != "/scratch/jobs":
        raise ValueError(f"refusing jobs root outside /scratch/jobs: {jobs_root}")
    if report.get("operation_hash") != cleanup_operation_hash(report):
        raise ValueError("scratch cleanup report operation_hash does not match candidates")
    if not allowed_users:
        raise ValueError("scratch cleanup apply requires at least one exact --allow-cleanup-user")
    if not set(report.get("cleanup_users") or []).issubset(allowed_users):
        raise ValueError("scratch cleanup token/report includes users that were not explicitly allowlisted")
    results = []
    for candidate in report.get("candidates") or []:
        username = str(candidate.get("username") or "")
        path = Path(str(candidate.get("path", ""))).resolve()
        if username not in allowed_users:
            results.append({"path": str(path), "status": "skipped", "reason": "user not allowlisted"})
            continue
        if _user_has_active_slurm_job(username):
            results.append({"path": str(path), "status": "skipped", "reason": "user has active Slurm jobs"})
            continue
        if not _safe_user_cleanup_path(path, root=root, username=username):
            results.append({"path": str(path), "status": "skipped", "reason": "not an allowed user scratch cache/tmp path"})
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
        results.append({"path": str(path), "status": "deleted", "user": username})
    applied = dict(report)
    applied["mode"] = "allowlisted_user_apply"
    applied["deletion_results"] = results
    return applied


def _quota_mount_capability(path: str, limit: Any, *, fstab_path: str | Path = "/etc/fstab") -> dict[str, Any]:
    limit_mb = memory_to_mb(limit)
    mount_info = _findmnt_info(path)
    mountpoint = mount_info.get("target") or path
    fstab = _find_fstab_entry(mountpoint, fstab_path=fstab_path)
    quota = _quota_active_for_mount(mountpoint)
    proposed_options = _options_with_quota((fstab or {}).get("options", ""))
    mount = {
        "path": path,
        "mountpoint": mountpoint,
        "source": mount_info.get("source"),
        "fstype": mount_info.get("fstype"),
        "options": mount_info.get("options"),
        "policy_quota": limit,
        "hard_kb": limit_mb * 1024,
        "findmnt": mount_info.get("raw"),
        "quotaon": quota.get("quotaon"),
        "repquota_user_rc": quota.get("repquota_user_rc"),
        "repquota_group_rc": quota.get("repquota_group_rc"),
        "setquota": shutil.which("setquota"),
        "fstab": fstab,
        "proposed_options": proposed_options,
        "quota_files": {
            "user": str(Path(mountpoint) / "aquota.user"),
            "group": str(Path(mountpoint) / "aquota.group"),
            "user_exists": (Path(mountpoint) / "aquota.user").exists(),
            "group_exists": (Path(mountpoint) / "aquota.group").exists(),
        },
    }
    mount["active_user_quota"] = quota.get("active_user_quota")
    mount["active_group_quota"] = quota.get("active_group_quota")
    mount["quota_options_present"] = _has_quota_options((fstab or {}).get("options", ""))
    mount["enable_needed"] = not (
        mount["active_user_quota"]
        and mount["active_group_quota"]
        and mount["quota_options_present"]
    )
    mount["can_enable"] = bool(mount_info.get("fstype") == "ext4" and fstab and shutil.which("quotacheck") and shutil.which("quotaon"))
    mount["can_apply"] = bool(mount["setquota"] and mount["active_user_quota"])
    if not mount["can_apply"]:
        mount["reason"] = "user quota is not active or setquota is missing"
    return mount


def _quota_user_report(
    username: str,
    user: dict[str, Any],
    mounts: dict[str, Any],
    *,
    apply_allowed: bool,
) -> dict[str, Any]:
    entry = _pwd_entry(username)
    user_report: dict[str, Any] = {
        "username": username,
        "status": user.get("status"),
        "tier": user.get("tier"),
        "exists": entry is not None,
        "apply_allowed": bool(apply_allowed and entry is not None),
        "targets": {},
    }
    if entry is None:
        user_report["reason"] = "user does not exist"
    elif not apply_allowed:
        user_report["reason"] = "report-only scope"
    for label, mount in sorted(mounts.items()):
        target = {
            "path": mount.get("path"),
            "mountpoint": mount.get("mountpoint"),
            "policy_quota": mount.get("policy_quota"),
            "hard_kb": mount.get("hard_kb"),
            "can_apply": mount.get("can_apply"),
            "current": _current_user_quota(username, str(mount.get("mountpoint") or "")) if entry else None,
        }
        current_hard = (target.get("current") or {}).get("hard_kb")
        target["drift"] = (
            "unknown"
            if current_hard is None
            else "ok"
            if int(current_hard) == int(mount.get("hard_kb") or 0)
            else "different"
        )
        user_report["targets"][label] = target
    return user_report


def _current_user_quota(username: str, mountpoint: str) -> dict[str, Any]:
    if not mountpoint or shutil.which("repquota") is None:
        return {"available": False, "reason": "repquota unavailable"}
    entry = _pwd_entry(username)
    if entry is None:
        return {"available": False, "reason": "user missing"}
    result = command_result(["repquota", "-uP", mountpoint])
    evidence = {
        "available": result["rc"] == 0,
        "mountpoint": mountpoint,
        "rc": result["rc"],
    }
    if result["rc"] != 0:
        evidence["stderr"] = result["stderr"]
        return evidence
    wanted = {username, f"#{entry.pw_uid}"}
    for line in result["stdout"].splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in wanted:
            continue
        try:
            evidence.update({"used_kb": int(parts[2]), "soft_kb": int(parts[3]), "hard_kb": int(parts[4])})
        except (ValueError, IndexError):
            evidence["raw"] = line
        break
    return evidence


def _pwd_entry(username: str) -> pwd.struct_passwd | None:
    try:
        return pwd.getpwnam(username)
    except KeyError:
        return None


def _selected_quota_labels(resolved: dict[str, Any], labels: list[str] | None) -> list[str]:
    paths = resolved["derived"].get("paths") or {}
    quotas = resolved["resolved_policies"]["storage"].get("quotas") or {}
    selected = labels or list(DEFAULT_QUOTA_LABELS)
    result = []
    for label in selected:
        if label not in DEFAULT_QUOTA_LABELS:
            raise ValueError(f"unsupported quota mount label: {label}")
        if paths.get(label) and quotas.get(label):
            result.append(label)
    return result


def _storage_quota_report(
    resolved: dict[str, Any],
    *,
    labels: list[str],
    fstab_path: str | Path,
    mode: str,
) -> dict[str, Any]:
    paths = resolved["derived"].get("paths") or {}
    quotas = resolved["resolved_policies"]["storage"].get("quotas") or {}
    return {
        "schema_version": 1,
        "command": "storage-quotas",
        "profile": resolved["profile"],
        "config_hash": config_hash(resolved),
        "mode": mode,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "fstab_path": str(fstab_path),
        "mount_labels": labels,
        "commands": {
            "quotacheck": shutil.which("quotacheck"),
            "quotaon": shutil.which("quotaon"),
            "quotaoff": shutil.which("quotaoff"),
            "setquota": shutil.which("setquota"),
            "repquota": shutil.which("repquota"),
            "findmnt": shutil.which("findmnt"),
            "mount": shutil.which("mount"),
        },
        "mounts": {
            label: _quota_mount_capability(str(paths[label]), quotas[label], fstab_path=fstab_path)
            for label in labels
        },
    }


def _findmnt_info(path: str) -> dict[str, Any]:
    raw = command_stdout(["findmnt", "-T", path, "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"])
    if raw:
        try:
            filesystems = json.loads(raw).get("filesystems") or []
        except json.JSONDecodeError:
            filesystems = []
        if filesystems:
            info = filesystems[0]
            return {
                "target": info.get("target"),
                "source": info.get("source"),
                "fstype": info.get("fstype"),
                "options": info.get("options"),
                "raw": raw,
            }
    fallback = command_stdout(["findmnt", "-T", path, "-no", "TARGET,SOURCE,FSTYPE,OPTIONS"])
    if not fallback:
        return {"raw": None}
    parts = fallback.split(None, 3)
    while len(parts) < 4:
        parts.append(None)
    return {"target": parts[0], "source": parts[1], "fstype": parts[2], "options": parts[3], "raw": fallback}


def _quota_active_for_mount(mountpoint: str) -> dict[str, Any]:
    quotaon = command_stdout(["quotaon", "-p", mountpoint]) if shutil.which("quotaon") else None
    repquota_user_rc = command_rc(["repquota", "-u", mountpoint]) if shutil.which("repquota") else 127
    repquota_group_rc = command_rc(["repquota", "-g", mountpoint]) if shutil.which("repquota") else 127
    text = str(quotaon or "").lower()
    return {
        "quotaon": quotaon,
        "repquota_user_rc": repquota_user_rc,
        "repquota_group_rc": repquota_group_rc,
        "active_user_quota": ("user quota" in text and " is on" in text) or repquota_user_rc == 0,
        "active_group_quota": ("group quota" in text and " is on" in text) or repquota_group_rc == 0,
    }


def _find_fstab_entry(mountpoint: str, *, fstab_path: str | Path = "/etc/fstab") -> dict[str, Any] | None:
    path = Path(fstab_path)
    if not path.exists():
        return None
    for index, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        if parts[1] == mountpoint:
            return {
                "line": index,
                "spec": parts[0],
                "mountpoint": parts[1],
                "fstype": parts[2],
                "options": parts[3],
                "dump": parts[4] if len(parts) > 4 else "0",
                "pass": parts[5] if len(parts) > 5 else "0",
            }
    return None


def _options_with_quota(options: str) -> str:
    parts = [part for part in str(options or "defaults").split(",") if part]
    for required in ("usrquota", "grpquota"):
        if required not in parts:
            parts.append(required)
    return ",".join(parts)


def _has_quota_options(options: str) -> bool:
    parts = set(str(options or "").split(","))
    return "usrquota" in parts and "grpquota" in parts


def _fstab_with_quota_options(text: str, mountpoints: list[str]) -> tuple[str, list[str]]:
    wanted = set(mountpoints)
    changed: list[str] = []
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue
        parts = stripped.split()
        if len(parts) < 4 or parts[1] not in wanted:
            output.append(line)
            continue
        proposed = _options_with_quota(parts[3])
        if proposed != parts[3]:
            parts[3] = proposed
            changed.append(parts[1])
        output.append("\t".join(parts))
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(output) + suffix, changed


def _dedupe_mountpoints(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value or value in result:
            continue
        result.append(str(value))
    return result


def _atomic_write(path: Path, content: str) -> None:
    original = path.stat()
    with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.chmod(stat.S_IMODE(original.st_mode))
    if os.geteuid() == 0:
        os.chown(temp_path, original.st_uid, original.st_gid)
    temp_path.replace(path)


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


def _cleanup_candidate(path: Path, *, username: str | None = None, base: str | None = None) -> dict[str, Any]:
    path_stat = path.stat()
    candidate = {
        "path": str(path),
        "mtime": dt.datetime.fromtimestamp(path_stat.st_mtime, dt.timezone.utc).isoformat(),
        "type": "directory" if path.is_dir() else "file",
    }
    if username:
        candidate["username"] = username
    if base:
        candidate["base"] = base
    return candidate


def _safe_fixture_cleanup_path(path: Path, *, root: Path, fixture_prefix: str) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path.parent == root and path.name.startswith(fixture_prefix) and fixture_prefix.startswith("ssn-test-")


def _safe_user_cleanup_path(path: Path, *, root: Path, username: str) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    parts = relative.parts
    if len(parts) < 3:
        return False
    if parts[0] != username or parts[1] not in {"cache", "tmp"}:
        return False
    protected_roots = {root / username / "cache", root / username / "tmp", root / "jobs"}
    return path not in protected_roots


def _user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def _user_has_active_slurm_job(username: str) -> bool:
    if shutil.which("squeue") is None:
        return False
    output = command_stdout(["squeue", "-h", "-u", username, "-o", "%T"]) or ""
    active = {"BOOT_FAIL", "CONFIGURING", "COMPLETING", "RESIZING", "RUNNING", "SUSPENDED"}
    return any(line.strip() in active for line in output.splitlines())
