from __future__ import annotations

import base64
import datetime as dt
import grp
import hashlib
import os
import pwd
import stat
import shlex
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .safety import retention_candidates
from .yamlutil import dump_yaml, load_yaml


VALID_STATUSES = {"active", "suspended", "inactive"}
SSH_KEY_TYPES = ("ssh-rsa", "ssh-dss", "ssh-ed25519", "ecdsa-sha2-")
USER_DOC_KEYS = {"schema_version", "groups", "users"}
GROUP_KEYS = {"description", "notes", "adopt_existing"}
USER_KEYS = {
    "status",
    "tier",
    "full_name",
    "email",
    "uid",
    "gid",
    "groups",
    "ssh_keys",
    "notes",
    "overrides",
    "adopt_existing",
}
SSH_KEY_KEYS = {"public_key", "comment", "fingerprint", "options_raw", "options"}
OVERRIDE_KEYS = {"reason", "values", "expires_at"}
STATE_DOC_KEYS = {"schema_version", "users"}
STATE_USER_KEYS = {
    "managed",
    "status",
    "tier",
    "uid",
    "gid",
    "original_uid",
    "original_gid",
    "data_dir",
    "scratch_dir",
    "managed_groups",
    "archive_state",
    "updated_at",
}
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


def validate_state(state_doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    unknown = set(state_doc) - STATE_DOC_KEYS
    if unknown:
        errors.append(f"users-state.yml has unknown top-level keys: {sorted(unknown)}")
    users = state_doc.get("users") or {}
    if not isinstance(users, dict):
        return ["users-state.yml users must be a map"]
    for username, entry in users.items():
        if not isinstance(entry, dict):
            errors.append(f"state.users.{username} must be a map")
            continue
        unknown_entry = set(entry) - STATE_USER_KEYS
        if unknown_entry:
            errors.append(f"state.users.{username} has unknown keys: {sorted(unknown_entry)}")
    return errors


def validate_users(
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    state_doc: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    unknown = set(users_doc) - USER_DOC_KEYS
    if unknown:
        errors.append(f"users.yml has unknown top-level keys: {sorted(unknown)}")
    groups = users_doc.get("groups") or {}
    users = users_doc.get("users") or {}
    state_doc = state_doc or {}
    managed_groups = _state_managed_groups(state_doc)
    if not isinstance(groups, dict):
        errors.append("groups must be a map")
        groups = {}
    if not isinstance(users, dict):
        errors.append("users must be a map")
        users = {}
    tiers = {tier["name"] for tier in resolved["derived"]["rendered_tiers"]}
    for group_name, group_doc in groups.items():
        if not _valid_group_name(group_name):
            errors.append(f"groups.{group_name}: invalid group name")
        if not isinstance(group_doc, dict):
            errors.append(f"groups.{group_name} must be a map")
            continue
        unknown_group = set(group_doc) - GROUP_KEYS
        if unknown_group:
            errors.append(f"groups.{group_name} has unknown keys: {sorted(unknown_group)}")
        if "members" in group_doc:
            errors.append(f"groups.{group_name}.members is not allowed; user groups are authoritative")
        try:
            grp.getgrnam(group_name)
        except KeyError:
            pass
        else:
            if group_name not in managed_groups and not group_doc.get("adopt_existing"):
                errors.append(
                    f"groups.{group_name}: local group already exists; set adopt_existing: true to manage membership"
                )
    for username, user in users.items():
        if not _valid_username(username):
            errors.append(f"users.{username}: invalid username")
            continue
        if not isinstance(user, dict):
            errors.append(f"users.{username}: user entry must be a map")
            continue
        unknown_user = set(user) - USER_KEYS
        if unknown_user:
            errors.append(f"users.{username} has unknown keys: {sorted(unknown_user)}")
        status = user.get("status")
        tier = user.get("tier")
        if status not in VALID_STATUSES:
            errors.append(f"users.{username}.status must be one of {sorted(VALID_STATUSES)}")
        if tier not in tiers:
            errors.append(f"users.{username}.tier must be one of {sorted(tiers)}")
        if not isinstance(user.get("groups") or [], list):
            errors.append(f"users.{username}.groups must be a list")
        for group in user.get("groups") or []:
            if group not in groups:
                errors.append(f"users.{username}.groups references undefined group {group!r}")
        email = user.get("email")
        if email is not None and ("@" not in str(email) or str(email).startswith("@")):
            errors.append(f"users.{username}.email is invalid")
        for key in ("uid", "gid"):
            if user.get(key) is not None:
                try:
                    int(user[key])
                except (TypeError, ValueError):
                    errors.append(f"users.{username}.{key} must be an integer")
        errors.extend(_validate_ssh_keys(username, user.get("ssh_keys", None)))
        errors.extend(_validate_overrides(username, user.get("overrides") or {}))
        errors.extend(_validate_local_identity_conflicts(username, user, state_doc))
    return errors


def _state_managed_groups(state_doc: dict[str, Any]) -> set[str]:
    groups: set[str] = set()
    for entry in (state_doc.get("users") or {}).values():
        if isinstance(entry, dict):
            groups.update(entry.get("managed_groups") or [])
    return groups


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
        previous_state = managed_state.get(username) or {}
        id_error = _reactivation_identity_error(username, user, previous_state)
        if id_error:
            actions.append(UserAction(username, "validation_error", id_error, risky=True))
            continue
        status = user["status"]
        exists = _user_exists(username)
        if status == "active":
            if not exists:
                actions.append(UserAction(username, "create_unix_user", "active user missing"))
            else:
                if _account_needs_unlock(username):
                    actions.append(UserAction(username, "ensure_unlocked", "active login allowed"))
            actions.extend(_group_actions(username, user, users_doc, state_doc, resolved))
            if user.get("ssh_keys") is not None:
                if not exists or not _authorized_keys_match(username, user["ssh_keys"]):
                    actions.append(UserAction(username, "sync_authorized_keys", _key_plan_detail(user["ssh_keys"])))
            if not exists or not _user_data_dir_matches(resolved, username):
                actions.append(UserAction(username, "ensure_data_dir", _data_dir(resolved, username)))
            if (resolved["derived"].get("paths") or {}).get("scratch"):
                if not exists or not _user_scratch_dirs_match(resolved, username):
                    actions.append(UserAction(username, "ensure_scratch_dir", _scratch_dir(resolved, username)))
            if not exists or not _slurm_association_matches(username, user["tier"], resolved):
                actions.append(UserAction(username, "ensure_slurm_association", user["tier"]))
            if exists and not _state_entry_matches(username, user, previous_state, resolved):
                actions.append(UserAction(username, "update_state", "record managed state"))
        elif status == "suspended":
            if exists and _account_needs_lock(username):
                actions.append(UserAction(username, "lock_unix_account", "suspended", risky=True))
            if _slurm_association_exists(username, resolved):
                actions.append(UserAction(username, "disable_slurm_association", "suspended", risky=True))
            if _user_has_slurm_jobs(username):
                actions.append(UserAction(username, "kill_jobs", "pending/running jobs killed immediately", risky=True))
            if exists and not _state_entry_matches(username, user, previous_state, resolved):
                actions.append(UserAction(username, "update_state", "record managed state"))
        elif status == "inactive":
            actions.append(UserAction(username, "inactive_state_machine", "archive lifecycle required", risky=True))
    return actions


def _reactivation_identity_error(username: str, user: dict[str, Any], previous_state: dict[str, Any]) -> str | None:
    original_uid = previous_state.get("original_uid")
    original_gid = previous_state.get("original_gid")
    if original_uid is None or original_gid is None:
        return None
    if previous_state.get("status") != "inactive" and previous_state.get("archive_state") != "tombstoned":
        return None
    if user.get("status") != "active":
        return None
    if user.get("uid") is None or user.get("gid") is None:
        return "reactivating inactive user requires explicit original uid/gid"
    if int(user["uid"]) != int(original_uid) or int(user["gid"]) != int(original_gid):
        return f"reactivation must reuse original uid/gid {original_uid}/{original_gid}"
    try:
        entry = pwd.getpwuid(int(original_uid))
        if entry.pw_name != username:
            return f"original uid {original_uid} is already used by {entry.pw_name}"
    except KeyError:
        pass
    try:
        group = grp.getgrgid(int(original_gid))
        if group.gr_name != username:
            return f"original gid {original_gid} is already used by {group.gr_name}"
    except KeyError:
        pass
    return None


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


def backup_retention_report(backup_root: str | Path, *, retention_days: int = 90) -> dict[str, Any]:
    candidates = retention_candidates(backup_root, older_than_days=retention_days)
    return {
        "backup_root": str(backup_root),
        "retention_days": retention_days,
        "mode": "report_only",
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def write_users(path: str | Path, users_doc: dict[str, Any]) -> None:
    Path(path).write_text(dump_yaml(users_doc))


def action_dicts(actions: list[UserAction]) -> list[dict[str, Any]]:
    return [asdict(action) for action in actions]


def apply_user_actions(
    actions: list[UserAction],
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    *,
    state_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("user sync apply must run as root")
    validation_errors = [action for action in actions if action.action == "validation_error"]
    if validation_errors:
        joined = "; ".join(f"{action.username}: {action.detail}" for action in validation_errors)
        raise ValueError(joined)
    users = users_doc.get("users") or {}
    state_doc = state_doc or {"schema_version": 1, "users": {}}
    state_doc.setdefault("schema_version", 1)
    state_doc.setdefault("users", {})
    for action in actions:
        user = users.get(action.username, {})
        if action.action == "create_unix_user":
            _create_unix_user(action.username, user)
        elif action.action == "ensure_private_primary_group":
            _ensure_private_primary_group(action.username)
        elif action.action == "ensure_unlocked":
            _run(["usermod", "-U", "-e", "", action.username], check=False)
        elif action.action == "lock_unix_account":
            _run(["usermod", "-L", "-e", "1", action.username], check=False)
        elif action.action == "sync_authorized_keys":
            _write_authorized_keys(action.username, user.get("ssh_keys") or {})
        elif action.action == "ensure_data_dir":
            _ensure_data_dir(resolved, action.username)
        elif action.action == "ensure_scratch_dir":
            _ensure_scratch_dir(resolved, action.username)
        elif action.action == "ensure_slurm_association":
            _ensure_slurm_association(action.username, user.get("tier"), resolved)
        elif action.action == "disable_slurm_association":
            _disable_slurm_association(action.username, resolved)
        elif action.action == "kill_jobs":
            _run(["scancel", "-u", action.username], check=False)
        elif action.action == "reconcile_managed_groups":
            _reconcile_managed_groups(action.username, user, users_doc, resolved, state_doc)
        elif action.action == "update_state":
            pass
        elif action.action == "inactive_state_machine":
            raise ValueError(f"{action.username}: inactive lifecycle is dry-plan only in this implementation pass")
        _update_state_for_user(state_doc, action.username, users.get(action.username), resolved)
    return state_doc


def _validate_ssh_keys(username: str, ssh_keys: Any) -> list[str]:
    errors: list[str] = []
    if ssh_keys is None:
        return errors
    if not isinstance(ssh_keys, dict):
        return [f"users.{username}.ssh_keys must be a map, null, or omitted"]
    for label, key in ssh_keys.items():
        if not _valid_key_label(str(label)):
            errors.append(f"users.{username}.ssh_keys.{label}: invalid key label")
        if not isinstance(key, dict):
            errors.append(f"users.{username}.ssh_keys.{label} must be a map")
            continue
        unknown_key = set(key) - SSH_KEY_KEYS
        if unknown_key:
            errors.append(f"users.{username}.ssh_keys.{label} has unknown keys: {sorted(unknown_key)}")
        public_key = key.get("public_key")
        if not isinstance(public_key, str) or ssh_fingerprint(public_key) == "invalid":
            errors.append(f"users.{username}.ssh_keys.{label}.public_key is invalid")
        elif len(public_key.split()) != 2:
            errors.append(
                f"users.{username}.ssh_keys.{label}.public_key must contain only key type and key blob; put comments in comment"
            )
        options_raw = key.get("options_raw")
        options = key.get("options")
        if options_raw and options and parse_options_raw(str(options_raw)) != options:
            errors.append(f"users.{username}.ssh_keys.{label} options_raw disagrees with options")
    return errors


def _validate_overrides(username: str, overrides: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(overrides, dict):
        return [f"users.{username}.overrides must be a map"]
    active_fields: dict[str, str] = {}
    now = dt.datetime.now(dt.timezone.utc)
    for name, override in overrides.items():
        if not isinstance(override, dict):
            errors.append(f"users.{username}.overrides.{name} must be a map")
            continue
        unknown_override = set(override) - OVERRIDE_KEYS
        if unknown_override:
            errors.append(f"users.{username}.overrides.{name} has unknown keys: {sorted(unknown_override)}")
        if "values" in override and not isinstance(override.get("values"), dict):
            errors.append(f"users.{username}.overrides.{name}.values must be a map")
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


def _validate_local_identity_conflicts(
    username: str,
    user: dict[str, Any],
    state_doc: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    state_users = state_doc.get("users") or {}
    previous = state_users.get(username) or {}
    exists = _user_exists(username)
    if exists and not previous.get("managed") and not user.get("adopt_existing"):
        errors.append(f"users.{username}: existing unmanaged local user requires adopt_existing: true")
    uid = user.get("uid")
    if uid is not None:
        try:
            entry = pwd.getpwuid(int(uid))
        except KeyError:
            pass
        else:
            if entry.pw_name != username:
                errors.append(f"users.{username}.uid conflicts with existing user {entry.pw_name}")
    gid = user.get("gid")
    if gid is not None:
        try:
            group = grp.getgrgid(int(gid))
        except KeyError:
            pass
        else:
            if group.gr_name != username and not user.get("adopt_existing"):
                errors.append(f"users.{username}.gid conflicts with existing group {group.gr_name}")
    try:
        existing_group = grp.getgrnam(username)
    except KeyError:
        existing_group = None
    if existing_group and not exists and not user.get("adopt_existing"):
        errors.append(f"users.{username}: existing unmanaged private group requires adopt_existing: true")
    return errors


def _group_actions(
    username: str,
    user: dict[str, Any],
    users_doc: dict[str, Any],
    state_doc: dict[str, Any],
    resolved: dict[str, Any],
) -> list[UserAction]:
    derived = resolved["derived"]
    tier = next(t for t in derived["rendered_tiers"] if t["name"] == user["tier"])
    desired = [derived["umbrella_group"], tier["group"], *(user.get("groups") or [])]
    actions = []
    if not _private_primary_group_matches(username):
        actions.append(UserAction(username, "ensure_private_primary_group", username))
    if not _managed_groups_match(username, user, users_doc, resolved, state_doc):
        actions.append(UserAction(username, "reconcile_managed_groups", ",".join(desired)))
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


def _scratch_dir(resolved: dict[str, Any], username: str) -> str:
    scratch_root = resolved["derived"]["paths"].get("scratch")
    if not scratch_root:
        return "scratch path disabled"
    return f"{scratch_root}/{username}"


def _valid_username(username: str) -> bool:
    if not username or len(username) > 32:
        return False
    first = username[0]
    return (first.islower() or first == "_") and all(c.islower() or c.isdigit() or c in "_-" for c in username)


def _valid_group_name(group_name: str) -> bool:
    return _valid_username(group_name)


def _valid_key_label(label: str) -> bool:
    return bool(label) and all(c.isalnum() or c in "._-" for c in label)


def _user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def _account_needs_unlock(username: str) -> bool:
    return _account_expired(username)


def _account_needs_lock(username: str) -> bool:
    status = _passwd_status(username)
    if status and len(status) > 1 and status[1] not in {"L", "LK"}:
        return True
    return not _account_expired(username)


def _passwd_status(username: str) -> list[str] | None:
    result = _run(["passwd", "-S", username], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.split()


def _account_expired(username: str) -> bool:
    result = _run(["chage", "-l", username], check=False)
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if not line.lower().startswith("account expires"):
            continue
        value = line.partition(":")[2].strip().lower()
        return value not in {"never", ""}
    return False


def _authorized_keys_match(username: str, ssh_keys: dict[str, Any]) -> bool:
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return False
    path = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
    desired = "\n".join(render_authorized_key(key) for key in ssh_keys.values()) + ("\n" if ssh_keys else "")
    try:
        existing = path.read_text()
        path_stat = path.stat()
    except OSError:
        return False
    if existing != desired:
        return False
    mode = stat.S_IMODE(path_stat.st_mode)
    return path_stat.st_uid == entry.pw_uid and path_stat.st_gid == entry.pw_gid and mode == 0o600


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def _create_unix_user(username: str, user: dict[str, Any]) -> None:
    cmd = ["useradd", "-m", "-s", "/bin/bash"]
    gid = user.get("gid")
    if gid is not None:
        try:
            group_name = grp.getgrgid(int(gid)).gr_name
        except KeyError:
            _run(["groupadd", "-g", str(gid), username])
            group_name = username
        cmd.extend(["-g", group_name])
    else:
        try:
            grp.getgrnam(username)
        except KeyError:
            cmd.append("-U")
        else:
            cmd.extend(["-g", username])
    uid = user.get("uid")
    if uid is not None:
        cmd.extend(["-u", str(uid)])
    full_name = user.get("full_name")
    if full_name:
        cmd.extend(["-c", str(full_name)])
    cmd.append(username)
    _run(cmd)


def _ensure_private_primary_group(username: str) -> None:
    entry = pwd.getpwnam(username)
    try:
        group = grp.getgrnam(username)
    except KeyError:
        _run(["groupadd", username])
        group = grp.getgrnam(username)
    if entry.pw_gid != group.gr_gid:
        _run(["usermod", "-g", username, username])


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
    _refuse_unsafe_user_dir(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    os.chown(path, entry.pw_uid, entry.pw_gid)


def _ensure_scratch_dir(resolved: dict[str, Any], username: str) -> None:
    scratch_root = resolved["derived"]["paths"].get("scratch")
    if not scratch_root:
        return
    entry = pwd.getpwnam(username)
    for relative in ("", "cache", "tmp"):
        path = Path(scratch_root) / username
        if relative:
            path = path / relative
        _refuse_unsafe_user_dir(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        os.chown(path, entry.pw_uid, entry.pw_gid)


def _refuse_unsafe_user_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to manage symlink path {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"refusing to manage non-directory path {path}")


def _ensure_slurm_association(username: str, tier_name: str, resolved: dict[str, Any]) -> None:
    tier = next(t for t in resolved["derived"]["rendered_tiers"] if t["name"] == tier_name)
    allowed_qos = _allowed_qos_for_tier(tier_name, resolved)
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
            f"QOS={allowed_qos}",
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
            f"QOS={allowed_qos}",
            "MaxSubmitJobs=-1",
            "MaxJobs=-1",
        ],
        check=False,
    )


def _disable_slurm_association(username: str, resolved: dict[str, Any]) -> None:
    account = resolved["derived"]["slurm_account"]
    _run(["sacctmgr", "-i", "delete", "user", username, f"Account={account}"], check=False)


def _allowed_qos_for_tier(tier_name: str, resolved: dict[str, Any]) -> str:
    tiers = resolved["derived"]["rendered_tiers"]
    selected = next(t for t in tiers if t["name"] == tier_name)
    allowed = [
        tier["qos"]
        for tier in tiers
        if int(tier["preempt_rank"]) <= int(selected["preempt_rank"])
    ]
    return ",".join(allowed)


def _ensure_group_membership(username: str, group_name: str) -> None:
    try:
        grp.getgrnam(group_name)
    except KeyError:
        _run(["groupadd", group_name])
    _run(["usermod", "-aG", group_name, username])


def _reconcile_managed_groups(
    username: str,
    user: dict[str, Any],
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    state_doc: dict[str, Any],
) -> None:
    desired = set(_desired_managed_groups(user, resolved))
    managed_universe = set(desired)
    managed_universe.update(_all_policy_groups(users_doc, resolved))
    previous = (state_doc.get("users") or {}).get(username) or {}
    managed_universe.update(previous.get("managed_groups") or [])
    current = _current_supplementary_groups(username)

    for group_name in sorted(desired):
        _ensure_group_membership(username, group_name)
    for group_name in sorted((current & managed_universe) - desired):
        _run(["gpasswd", "-d", username, group_name], check=False)


def _desired_managed_groups(user: dict[str, Any], resolved: dict[str, Any]) -> list[str]:
    derived = resolved["derived"]
    tier = next(t for t in derived["rendered_tiers"] if t["name"] == user["tier"])
    return [derived["umbrella_group"], tier["group"], *(user.get("groups") or [])]


def _all_policy_groups(users_doc: dict[str, Any], resolved: dict[str, Any]) -> set[str]:
    groups = {resolved["derived"]["umbrella_group"]}
    groups.update(tier["group"] for tier in resolved["derived"]["rendered_tiers"])
    groups.update((users_doc.get("groups") or {}).keys())
    return groups


def _current_supplementary_groups(username: str) -> set[str]:
    groups = set()
    for group in grp.getgrall():
        if username in group.gr_mem:
            groups.add(group.gr_name)
    return groups


def _private_primary_group_matches(username: str) -> bool:
    try:
        entry = pwd.getpwnam(username)
        group = grp.getgrnam(username)
    except KeyError:
        return False
    return entry.pw_gid == group.gr_gid


def _managed_groups_match(
    username: str,
    user: dict[str, Any],
    users_doc: dict[str, Any],
    resolved: dict[str, Any],
    state_doc: dict[str, Any],
) -> bool:
    if not _user_exists(username):
        return False
    desired = set(_desired_managed_groups(user, resolved))
    managed_universe = set(desired)
    managed_universe.update(_all_policy_groups(users_doc, resolved))
    previous = (state_doc.get("users") or {}).get(username) or {}
    managed_universe.update(previous.get("managed_groups") or [])
    current = _current_supplementary_groups(username)
    return desired.issubset(current) and not ((current & managed_universe) - desired)


def _user_data_dir_matches(resolved: dict[str, Any], username: str) -> bool:
    data_root = resolved["derived"]["paths"].get("data")
    if not data_root:
        return True
    return _owned_private_dir_matches(Path(data_root) / username, username)


def _user_scratch_dirs_match(resolved: dict[str, Any], username: str) -> bool:
    scratch_root = resolved["derived"]["paths"].get("scratch")
    if not scratch_root:
        return True
    return all(
        _owned_private_dir_matches(Path(scratch_root) / username / relative, username)
        for relative in ("", "cache", "tmp")
    )


def _owned_private_dir_matches(path: Path, username: str) -> bool:
    try:
        entry = pwd.getpwnam(username)
        path_stat = path.lstat()
    except (KeyError, OSError):
        return False
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        return False
    mode = stat.S_IMODE(path_stat.st_mode)
    return path_stat.st_uid == entry.pw_uid and path_stat.st_gid == entry.pw_gid and mode == 0o700


def _slurm_association_matches(username: str, tier_name: str, resolved: dict[str, Any]) -> bool:
    assoc = _slurm_user_association(username, resolved)
    if not assoc:
        return False
    tier = next(t for t in resolved["derived"]["rendered_tiers"] if t["name"] == tier_name)
    allowed = set(_allowed_qos_for_tier(tier_name, resolved).split(","))
    qos_values = set(filter(None, (assoc.get("qos") or "").split(",")))
    return (
        assoc.get("default_account") == resolved["derived"]["slurm_account"]
        and assoc.get("default_qos") == tier["qos"]
        and allowed.issubset(qos_values)
    )


def _slurm_association_exists(username: str, resolved: dict[str, Any]) -> bool:
    return _slurm_user_association(username, resolved) is not None


def _slurm_user_association(username: str, resolved: dict[str, Any]) -> dict[str, str] | None:
    if shutil.which("sacctmgr") is None:
        return None
    account = resolved["derived"]["slurm_account"]
    result = _run(
        [
            "sacctmgr",
            "-nP",
            "show",
            "assoc",
            f"user={username}",
            f"account={account}",
            "format=User,Account,DefaultQOS,QOS",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4 or parts[0] != username:
            continue
        assoc = {
            "user": parts[0],
            "default_account": parts[1],
            "default_qos": parts[2],
            "qos": parts[3],
        }
        if assoc["default_account"] == account:
            return assoc
    return None


def _user_has_slurm_jobs(username: str) -> bool:
    if shutil.which("squeue") is None:
        return False
    result = _run(["squeue", "-h", "-u", username, "-o", "%i"], check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def _state_entry_matches(
    username: str,
    user: dict[str, Any],
    previous: dict[str, Any],
    resolved: dict[str, Any],
) -> bool:
    if not previous.get("managed"):
        return False
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return False
    expected = {
        "status": user.get("status"),
        "tier": user.get("tier"),
        "uid": entry.pw_uid,
        "gid": entry.pw_gid,
        "data_dir": _data_dir(resolved, username),
        "scratch_dir": _scratch_dir(resolved, username),
        "managed_groups": _desired_managed_groups(user, resolved),
    }
    return all(previous.get(key) == value for key, value in expected.items())


def _update_state_for_user(
    state_doc: dict[str, Any],
    username: str,
    user: dict[str, Any] | None,
    resolved: dict[str, Any],
) -> None:
    if not user or not _user_exists(username):
        return
    entry = pwd.getpwnam(username)
    state_users = state_doc.setdefault("users", {})
    previous = state_users.get(username) or {}
    state_users[username] = {
        **previous,
        "managed": True,
        "status": user.get("status"),
        "tier": user.get("tier"),
        "uid": entry.pw_uid,
        "gid": entry.pw_gid,
        "original_uid": previous.get("original_uid", entry.pw_uid),
        "original_gid": previous.get("original_gid", entry.pw_gid),
        "data_dir": _data_dir(resolved, username),
        "scratch_dir": _scratch_dir(resolved, username),
        "managed_groups": _desired_managed_groups(user, resolved),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
