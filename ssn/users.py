from __future__ import annotations

import base64
import datetime as dt
import grp
import hashlib
import os
import pwd
import shlex
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .yamlutil import dump_yaml, load_yaml


VALID_STATUSES = {"active", "suspended", "inactive"}
SSH_KEY_TYPES = ("ssh-rsa", "ssh-dss", "ssh-ed25519", "ecdsa-sha2-")
DEFAULT_EXCLUDES = {
    "root",
    "nobody",
    "nfsnobody",
    "sshd",
    "systemd-network",
    "systemd-resolve",
    "systemd-timesync",
    "messagebus",
    "syslog",
    "mysql",
    "redis",
    "postgres",
    "mongodb",
    "munge",
    "slurm",
    "ubuntu",
    "kube",
}


@dataclass
class UserAction:
    username: str
    action: str
    detail: str = ""
    risky: bool = False


def load_users(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "groups": {}, "users": {}}
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML map")
    if data.get("schema_version") != 1:
        raise ValueError(f"{path} must use schema_version: 1")
    data.setdefault("groups", {})
    data.setdefault("users", {})
    return data


def load_state(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "users": {}}
    data = load_yaml(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"{path} must use schema_version: 1")
    data.setdefault("users", {})
    return data


def validate_users(users_doc: dict[str, Any], resolved: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    groups = users_doc.get("groups") or {}
    users = users_doc.get("users") or {}
    tiers = {tier["name"] for tier in resolved["derived"]["rendered_tiers"]}
    for group_name, group_doc in groups.items():
        if not isinstance(group_doc, dict):
            errors.append(f"groups.{group_name} must be a map")
        if isinstance(group_doc, dict) and "members" in group_doc:
            errors.append(f"groups.{group_name}.members is not allowed; user groups are authoritative")
    for username, user in users.items():
        if not _valid_username(username):
            errors.append(f"users.{username}: invalid username")
            continue
        if not isinstance(user, dict):
            errors.append(f"users.{username}: user entry must be a map")
            continue
        status = user.get("status")
        tier = user.get("tier")
        if status not in VALID_STATUSES:
            errors.append(f"users.{username}.status must be one of {sorted(VALID_STATUSES)}")
        if tier not in tiers:
            errors.append(f"users.{username}.tier must be one of {sorted(tiers)}")
        for group in user.get("groups") or []:
            if group not in groups:
                errors.append(f"users.{username}.groups references undefined group {group!r}")
        errors.extend(_validate_ssh_keys(username, user.get("ssh_keys", None)))
        errors.extend(_validate_overrides(username, user.get("overrides") or {}))
    return errors


def plan_user_sync(
    users_doc: dict[str, Any],
    state_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    single_user: str | None = None,
) -> list[UserAction]:
    actions: list[UserAction] = []
    users = users_doc.get("users") or {}
    managed_state = state_doc.get("users") or {}
    desired_names = set(users)
    for username in sorted(managed_state):
        if managed_state[username].get("managed") and username not in desired_names:
            actions.append(
                UserAction(
                    username,
                    "validation_error",
                    "managed user removed from users.yml; mark inactive instead",
                    risky=True,
                )
            )

    for username, user in sorted(users.items()):
        if single_user and username != single_user:
            continue
        status = user["status"]
        exists = _user_exists(username)
        if status == "active":
            if not exists:
                actions.append(UserAction(username, "create_unix_user", "active user missing"))
            else:
                actions.append(UserAction(username, "ensure_unlocked", "active login allowed"))
            actions.extend(_group_actions(username, user, resolved))
            if user.get("ssh_keys") is not None:
                actions.append(UserAction(username, "sync_authorized_keys", _key_plan_detail(user["ssh_keys"])))
            else:
                actions.append(UserAction(username, "leave_authorized_keys_unmanaged", "ssh_keys omitted or null"))
            actions.append(UserAction(username, "ensure_data_dir", _data_dir(resolved, username)))
            actions.append(UserAction(username, "ensure_slurm_association", user["tier"]))
        elif status == "suspended":
            if exists:
                actions.append(UserAction(username, "lock_unix_account", "suspended", risky=True))
            actions.append(UserAction(username, "disable_slurm_association", "suspended", risky=True))
            actions.append(UserAction(username, "kill_jobs", "pending/running jobs killed immediately", risky=True))
        elif status == "inactive":
            actions.append(UserAction(username, "inactive_state_machine", "archive lifecycle required", risky=True))
    return actions


def discover_users(uid_min: int = 1000, uid_max: int = 60000, excludes: set[str] | None = None) -> dict[str, Any]:
    excludes = DEFAULT_EXCLUDES | (excludes or set())
    users: dict[str, Any] = {}
    today = dt.date.today().isoformat()
    for entry in sorted(pwd.getpwall(), key=lambda item: item.pw_uid):
        if entry.pw_uid < uid_min or entry.pw_uid > uid_max:
            continue
        if entry.pw_name in excludes:
            continue
        keys = discover_authorized_keys(Path(entry.pw_dir) / ".ssh" / "authorized_keys")
        users[entry.pw_name] = {
            "status": "active",
            "tier": "standard",
            "full_name": (entry.pw_gecos or "").split(",")[0].strip() or None,
            "email": None,
            "uid": entry.pw_uid,
            "gid": entry.pw_gid,
            "groups": [],
            "ssh_keys": keys if keys else None,
            "notes": f"discovered {today}",
        }
    return {"schema_version": 1, "groups": {}, "users": users}


def discover_authorized_keys(path: Path) -> dict[str, Any]:
    try:
        exists = path.exists()
    except OSError:
        return {}
    if not exists:
        return {}
    keys: dict[str, Any] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {}
    index = 1
    for line in lines:
        parsed = parse_authorized_key(line)
        if parsed is None:
            continue
        label = f"imported-{index}"
        keys[label] = parsed
        index += 1
    return keys


def parse_authorized_key(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    key_index = None
    for index, part in enumerate(parts):
        if part.startswith(SSH_KEY_TYPES):
            key_index = index
            break
    if key_index is None or key_index + 1 >= len(parts):
        return None
    options_raw = " ".join(parts[:key_index]) or None
    public_key = " ".join(parts[key_index : key_index + 2])
    comment = " ".join(parts[key_index + 2 :]) or None
    parsed = {
        "public_key": public_key,
        "comment": comment,
        "fingerprint": ssh_fingerprint(public_key),
    }
    if options_raw:
        parsed["options_raw"] = options_raw
        parsed["options"] = parse_options_raw(options_raw)
    return parsed


def render_authorized_key(key: dict[str, Any]) -> str:
    prefix = key.get("options_raw")
    public_key = key["public_key"].strip()
    comment = key.get("comment")
    pieces = []
    if prefix:
        pieces.append(prefix)
    pieces.append(public_key)
    if comment:
        pieces.append(str(comment))
    return " ".join(pieces).rstrip()


def ssh_fingerprint(public_key: str) -> str:
    parts = public_key.split()
    if len(parts) < 2:
        return "invalid"
    try:
        raw = base64.b64decode(parts[1].encode(), validate=True)
    except Exception:
        return "invalid"
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return f"SHA256:{digest}"


def parse_options_raw(options_raw: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    lexer = shlex.shlex(options_raw, posix=True)
    lexer.whitespace = ","
    lexer.whitespace_split = True
    for token in lexer:
        if "=" in token:
            key, value = token.split("=", 1)
            key = key.replace("-", "_")
            if key == "from":
                parsed[key] = [value]
            else:
                parsed[key] = value
        else:
            parsed[token.replace("-", "_")] = True
    return parsed


def backup_file(path: str | Path, backup_root: str | Path) -> Path | None:
    path = Path(path)
    if not path.exists():
        return None
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = backup_root / f"{path.name}.{stamp}"
    shutil.copy2(path, backup)
    return backup


def write_users(path: str | Path, users_doc: dict[str, Any]) -> None:
    Path(path).write_text(dump_yaml(users_doc))


def action_dicts(actions: list[UserAction]) -> list[dict[str, Any]]:
    return [asdict(action) for action in actions]


def apply_user_actions(actions: list[UserAction], users_doc: dict[str, Any], resolved: dict[str, Any]) -> None:
    if os.geteuid() != 0:
        raise PermissionError("user sync apply must run as root")
    users = users_doc.get("users") or {}
    for action in actions:
        if action.action == "validation_error":
            raise ValueError(f"{action.username}: {action.detail}")
        user = users.get(action.username, {})
        if action.action == "create_unix_user":
            _run(["useradd", "-m", "-s", "/bin/bash", "-U", action.username])
        elif action.action == "ensure_unlocked":
            _run(["usermod", "-U", "-e", "", action.username], check=False)
        elif action.action == "lock_unix_account":
            _run(["usermod", "-L", "-e", "1", action.username], check=False)
        elif action.action == "sync_authorized_keys":
            _write_authorized_keys(action.username, user.get("ssh_keys") or {})
        elif action.action == "ensure_data_dir":
            _ensure_data_dir(resolved, action.username)
        elif action.action == "ensure_slurm_association":
            _ensure_slurm_association(action.username, user.get("tier"), resolved)
        elif action.action == "disable_slurm_association":
            _run(["sacctmgr", "-i", "modify", "user", action.username, "set", "MaxSubmitJobs=0", "MaxJobs=0"], check=False)
        elif action.action == "kill_jobs":
            _run(["scancel", "-u", action.username], check=False)
        elif action.action in {"ensure_primary_group", "ensure_project_group", "ensure_tier_group", "ensure_umbrella_group"}:
            _ensure_group_membership(action.username, action.detail)


def _validate_ssh_keys(username: str, ssh_keys: Any) -> list[str]:
    errors: list[str] = []
    if ssh_keys is None:
        return errors
    if not isinstance(ssh_keys, dict):
        return [f"users.{username}.ssh_keys must be a map, null, or omitted"]
    for label, key in ssh_keys.items():
        if not isinstance(key, dict):
            errors.append(f"users.{username}.ssh_keys.{label} must be a map")
            continue
        public_key = key.get("public_key")
        if not isinstance(public_key, str) or ssh_fingerprint(public_key) == "invalid":
            errors.append(f"users.{username}.ssh_keys.{label}.public_key is invalid")
        options_raw = key.get("options_raw")
        options = key.get("options")
        if options_raw and options and parse_options_raw(str(options_raw)) != options:
            errors.append(f"users.{username}.ssh_keys.{label} options_raw disagrees with options")
    return errors


def _validate_overrides(username: str, overrides: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    active_fields: dict[str, str] = {}
    now = dt.datetime.now(dt.timezone.utc)
    for name, override in overrides.items():
        if not isinstance(override, dict):
            errors.append(f"users.{username}.overrides.{name} must be a map")
            continue
        expires_at = override.get("expires_at")
        active = True
        if expires_at:
            try:
                active = dt.datetime.fromisoformat(str(expires_at)).astimezone(dt.timezone.utc) > now
            except ValueError:
                errors.append(f"users.{username}.overrides.{name}.expires_at is invalid")
        if not active:
            continue
        for field in (override.get("values") or {}):
            if field in active_fields:
                errors.append(
                    f"users.{username}.overrides.{name} overlaps field {field} with {active_fields[field]}"
                )
            active_fields[field] = name
    return errors


def _group_actions(username: str, user: dict[str, Any], resolved: dict[str, Any]) -> list[UserAction]:
    derived = resolved["derived"]
    tier = next(t for t in derived["rendered_tiers"] if t["name"] == user["tier"])
    actions = [
        UserAction(username, "ensure_primary_group", username),
        UserAction(username, "ensure_umbrella_group", derived["umbrella_group"]),
        UserAction(username, "ensure_tier_group", tier["group"]),
    ]
    for group in user.get("groups") or []:
        actions.append(UserAction(username, "ensure_project_group", group))
    return actions


def _key_plan_detail(ssh_keys: dict[str, Any]) -> str:
    if not ssh_keys:
        return "managed empty key set"
    pieces = []
    for label, key in ssh_keys.items():
        fingerprint = key.get("fingerprint") or ssh_fingerprint(key.get("public_key", ""))
        pieces.append(f"{label}:{fingerprint}")
    return ", ".join(pieces)


def _data_dir(resolved: dict[str, Any], username: str) -> str:
    data_root = resolved["derived"]["paths"].get("data")
    if not data_root:
        return "data path disabled"
    return f"{data_root}/{username}"


def _valid_username(username: str) -> bool:
    if not username or len(username) > 32:
        return False
    first = username[0]
    return (first.islower() or first == "_") and all(c.islower() or c.isdigit() or c in "_-" for c in username)


def _user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def _write_authorized_keys(username: str, ssh_keys: dict[str, Any]) -> None:
    entry = pwd.getpwnam(username)
    ssh_dir = Path(entry.pw_dir) / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    os.chown(ssh_dir, entry.pw_uid, entry.pw_gid)
    path = ssh_dir / "authorized_keys"
    content = "\n".join(render_authorized_key(key) for key in ssh_keys.values()) + ("\n" if ssh_keys else "")
    path.write_text(content)
    path.chmod(0o600)
    os.chown(path, entry.pw_uid, entry.pw_gid)


def _ensure_data_dir(resolved: dict[str, Any], username: str) -> None:
    data_root = resolved["derived"]["paths"].get("data")
    if not data_root:
        return
    entry = pwd.getpwnam(username)
    path = Path(data_root) / username
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(path, entry.pw_uid, entry.pw_gid)


def _ensure_slurm_association(username: str, tier_name: str, resolved: dict[str, Any]) -> None:
    tier = next(t for t in resolved["derived"]["rendered_tiers"] if t["name"] == tier_name)
    account = resolved["derived"]["slurm_account"]
    _run(["sacctmgr", "-i", "add", "account", account], check=False)
    _run(
        [
            "sacctmgr",
            "-i",
            "modify",
            "account",
            account,
            "set",
            "QOS=" + ",".join(t["qos"] for t in resolved["derived"]["rendered_tiers"]),
        ],
        check=False,
    )
    _run(
        [
            "sacctmgr",
            "-i",
            "add",
            "user",
            username,
            f"Account={account}",
            f"DefaultAccount={account}",
            f"DefaultQOS={tier['qos']}",
        ],
        check=False,
    )
    _run(
        [
            "sacctmgr",
            "-i",
            "modify",
            "user",
            username,
            "set",
            f"DefaultAccount={account}",
            f"DefaultQOS={tier['qos']}",
            "MaxSubmitJobs=-1",
            "MaxJobs=-1",
        ],
        check=False,
    )


def _ensure_group_membership(username: str, group_name: str) -> None:
    try:
        grp.getgrnam(group_name)
    except KeyError:
        _run(["groupadd", group_name])
    _run(["usermod", "-aG", group_name, username])
