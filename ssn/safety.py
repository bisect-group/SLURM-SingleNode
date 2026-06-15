from __future__ import annotations

import base64
import datetime as dt
import hashlib
import shutil
from pathlib import Path
from typing import Any

from .config import config_hash


SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "private_key")
RETENTION_DELETE_RISK = "retention_delete"
ALLOWED_RETENTION_ROOTS = {
    "/var/lib/slurm-single-node/plans": ("apply-", "install-", "storage-quotas-", "gpu-recovery-", "ssn-test-", "tmp-ssn-test-"),
    "/var/backups/slurm-single-node/users": ("users.yml.", "users-state.yml.", "ssn-test-", "tmp-ssn-test-"),
    "/var/backups/slurm-single-node/fstab": ("ssn-test-", "tmp-ssn-test-"),
}


def mask_email(value: str) -> str:
    if "@" not in value:
        return value
    local, domain = value.split("@", 1)
    if not local:
        return "***@" + domain
    return local[0] + "***@" + domain


def public_key_fingerprint(public_key: str) -> str:
    parts = public_key.split()
    if len(parts) < 2:
        return "invalid"
    try:
        raw = base64.b64decode(parts[1].encode(), validate=True)
    except Exception:
        return "invalid"
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return f"SHA256:{digest}"


def redact_for_plan(value: Any, *, terminal: bool = False, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SECRET_KEY_PARTS):
        return "[REDACTED]"
    if lowered == "public_key" and isinstance(value, str):
        return public_key_fingerprint(value)
    if lowered == "email" and isinstance(value, str) and terminal:
        return mask_email(value)
    if isinstance(value, dict):
        return {
            str(item_key): redact_for_plan(item_value, terminal=terminal, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_for_plan(item, terminal=terminal, key=key) for item in value]
    return value


def retention_candidates(root: str | Path, *, older_than_days: int) -> list[dict[str, Any]]:
    root = Path(root)
    if not root.exists():
        return []
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - older_than_days * 86400
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        candidates.append(
            {
                "path": str(path),
                "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                "type": "directory" if path.is_dir() else "file",
            }
        )
    return candidates


def retention_operation_hash(report: dict[str, Any]) -> str:
    payload = {
        "root": report.get("root"),
        "older_than_days": report.get("older_than_days"),
        "candidates": report.get("candidates") or [],
    }
    return config_hash({"retention_cleanup": payload})


def retention_cleanup_report(
    root: str | Path,
    *,
    older_than_days: int,
    profile: str | None = None,
    config_hash_value: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "retention-cleanup",
        "profile": profile,
        "config_hash": config_hash_value,
        "risk": RETENTION_DELETE_RISK,
        "mode": "report_only",
        "root": str(root),
        "production_root_allowed": str(root) in ALLOWED_RETENTION_ROOTS,
        "older_than_days": older_than_days,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidates": retention_candidates(root, older_than_days=older_than_days),
    }
    report["candidate_count"] = len(report["candidates"])
    report["operation_hash"] = retention_operation_hash(report)
    return report


def apply_test_retention_cleanup(
    report: dict[str, Any],
    *,
    fixture_prefix: str = "ssn-test-",
) -> dict[str, Any]:
    return apply_retention_cleanup(
        report,
        fixture_prefix=fixture_prefix,
        allow_production_roots=False,
    )


def apply_retention_cleanup(
    report: dict[str, Any],
    *,
    fixture_prefix: str = "ssn-test-",
    allow_production_roots: bool = False,
) -> dict[str, Any]:
    if report.get("operation_hash") != retention_operation_hash(report):
        raise ValueError("retention report operation_hash does not match candidates")
    root = Path(str(report.get("root", ""))).resolve()
    production_root = str(root) in ALLOWED_RETENTION_ROOTS
    if production_root and not allow_production_roots:
        raise ValueError("production SSN retention root cleanup requires --allow-production-roots")
    if not production_root and allow_production_roots:
        raise ValueError(f"not an allowed SSN retention root: {root}")
    results = []
    for candidate in report.get("candidates") or []:
        raw_path = Path(str(candidate.get("path", "")))
        try:
            parent = raw_path.parent.resolve()
        except OSError:
            parent = raw_path.parent
        display_path = str(raw_path)
        if parent != root:
            results.append({"path": display_path, "status": "skipped", "reason": "outside retention root"})
            continue
        if raw_path.is_symlink():
            results.append({"path": display_path, "status": "skipped", "reason": "symlink"})
            continue
        if not _safe_retention_path(raw_path, root=root, fixture_prefix=fixture_prefix, production_root=production_root):
            reason = "not an allowed SSN retention artifact" if production_root else "not an allowed SSN test artifact"
            results.append({"path": display_path, "status": "skipped", "reason": reason})
            continue
        if not raw_path.exists():
            results.append({"path": display_path, "status": "skipped", "reason": "already absent"})
            continue
        if raw_path.is_dir():
            shutil.rmtree(raw_path)
        else:
            raw_path.unlink()
        results.append({"path": display_path, "status": "deleted"})
    applied = dict(report)
    applied["mode"] = "production_root_apply" if production_root else "test_artifact_apply"
    applied["deletion_results"] = results
    return applied


def _safe_retention_test_path(path: Path, *, fixture_prefix: str) -> bool:
    if path.name.startswith(fixture_prefix) or path.name.startswith("tmp-ssn-test-"):
        return True
    return False


def _safe_retention_path(path: Path, *, root: Path, fixture_prefix: str, production_root: bool) -> bool:
    if _safe_retention_test_path(path, fixture_prefix=fixture_prefix):
        return True
    if not production_root:
        return False
    prefixes = ALLOWED_RETENTION_ROOTS.get(str(root), ())
    if str(root) == "/var/backups/slurm-single-node/fstab":
        return path.is_dir() and _timestamp_like_name(path.name)
    return any(path.name.startswith(prefix) for prefix in prefixes)


def _timestamp_like_name(value: str) -> bool:
    return len(value) >= 14 and value[:14].isdigit()
