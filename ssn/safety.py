from __future__ import annotations

import base64
import datetime as dt
import hashlib
from pathlib import Path
from typing import Any


SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "private_key")


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
