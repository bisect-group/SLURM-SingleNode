#!/usr/bin/env python3
"""
sync_users.py — Reconcile users.csv against the system state.

Reads /etc/tesla-cluster/users.csv (or --csv PATH), validates each row,
then for each user:

  active    -> ensure Unix account, tier group, SSH key, NAS dir, SLURM account
  inactive  -> tar /home → /storage/nas/_archive/, userdel -r, leave NAS data
               (admin manually purges /storage/nas/$user later)

After running, writes back any newly-assigned UID/GID/created_date to the
CSV (with file lock + daily backup). Existing values are preserved.

Usage:
    sudo sync_users.py                 # apply
    sudo sync_users.py --check         # dry run
    sudo sync_users.py --csv FILE
    sudo sync_users.py --user alice    # operate on just one row
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import grp
import logging
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("sync_users")

# ─── Constants matched to group_vars/all.yml ────────────────────────────
DEFAULT_CSV = "/etc/tesla-cluster/users.csv"
DEFAULT_LOCK = "/var/lock/tesla-users-csv.lock"
DEFAULT_BACKUP_DIR = "/storage/nas/_archive/users-csv-backups"
ARCHIVE_ROOT = "/storage/nas/_archive"
NAS_ROOT = "/storage/nas"
HOME_ROOT = "/home"
TESLA_USERS_GROUP = "tesla_users"

VALID_TIERS = {"none", "gpu1", "gpu2", "gpu3", "gpu4", "deadline"}
DEFAULT_TIER = "gpu1"
VALID_STATUS = {"active", "inactive"}

CSV_COLUMNS = [
    "username", "full_name", "email", "ssh_pubkey",
    "tier", "status", "expiry_date",
    "uid", "gid", "created_date", "notes",
]

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SSH_KEY_RE = re.compile(r"^(ssh-(rsa|dss|ed25519)|ecdsa-sha2-\S+)\s+\S+(\s+\S+)?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─── Data classes ───────────────────────────────────────────────────────

@dataclass
class UserRow:
    username: str
    full_name: str = ""
    email: str = ""
    ssh_pubkey: str = ""
    tier: str = DEFAULT_TIER
    status: str = "active"
    expiry_date: str = ""
    uid: str = ""
    gid: str = ""
    created_date: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in CSV_COLUMNS}


@dataclass
class ValidationError:
    username: str
    field: str
    value: str
    reason: str

    def __str__(self) -> str:
        return f"{self.username:20s}  field={self.field:14s}  value={self.value!r:30s}  reason={self.reason}"


@dataclass
class Action:
    """A single change we will/would apply."""
    username: str
    kind: str
    detail: str = ""


# ─── CSV I/O with locking ───────────────────────────────────────────────

class CSVFile:
    def __init__(self, path: str, lock_path: str = DEFAULT_LOCK):
        self.path = Path(path)
        self.lock_path = Path(lock_path)
        self._lockfd: Optional[int] = None

    def __enter__(self) -> "CSVFile":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lockfd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self._lockfd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if self._lockfd is not None:
            fcntl.flock(self._lockfd, fcntl.LOCK_UN)
            os.close(self._lockfd)

    def read(self) -> list[UserRow]:
        if not self.path.exists():
            return []
        rows: list[UserRow] = []
        with self.path.open(newline="") as f:
            reader = csv.DictReader(f)
            missing = set(CSV_COLUMNS) - set(reader.fieldnames or [])
            if missing:
                LOG.warning("CSV missing columns: %s (will be added on save)", missing)
            for raw in reader:
                row = UserRow(**{k: (raw.get(k) or "").strip() for k in CSV_COLUMNS if k in raw})
                rows.append(row)
        return rows

    def write(self, rows: list[UserRow]) -> None:
        # Daily backup before overwriting
        backup_dir = Path(DEFAULT_BACKUP_DIR)
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            today = dt.date.today().isoformat()
            backup = backup_dir / f"users.csv.{today}"
            if self.path.exists() and not backup.exists():
                shutil.copy2(self.path, backup)
        except OSError as e:
            LOG.warning("CSV backup failed (continuing): %s", e)

        tmp = self.path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            for r in rows:
                writer.writerow(r.to_dict())
        tmp.replace(self.path)


# ─── Validation ─────────────────────────────────────────────────────────

def validate_rows(rows: list[UserRow]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    seen_usernames: set[str] = set()

    for r in rows:
        u = r.username
        if not u:
            errors.append(ValidationError("(blank)", "username", "", "empty"))
            continue
        if not USERNAME_RE.match(u):
            errors.append(ValidationError(u, "username", u, "must match ^[a-z_][a-z0-9_-]{0,31}$"))
        if u in seen_usernames:
            errors.append(ValidationError(u, "username", u, "duplicate row"))
        seen_usernames.add(u)

        if r.tier and r.tier not in VALID_TIERS:
            errors.append(ValidationError(u, "tier", r.tier, f"must be one of {sorted(VALID_TIERS)}"))

        if r.status and r.status not in VALID_STATUS:
            errors.append(ValidationError(u, "status", r.status, f"must be one of {sorted(VALID_STATUS)}"))

        # ssh_pubkey is fully optional. If present, format-check it. If empty for
        # an active user, sync_users.py will simply not manage authorized_keys —
        # admin enrolls keys manually. (This matches the "defer keys" workflow.)
        if r.ssh_pubkey and not SSH_KEY_RE.match(r.ssh_pubkey):
            errors.append(ValidationError(u, "ssh_pubkey", r.ssh_pubkey[:40] + "…",
                                          "not a recognized ssh public key format"))

        if r.email and r.email != "EMAIL_PLACEHOLDER" and not EMAIL_RE.match(r.email):
            errors.append(ValidationError(u, "email", r.email, "not a valid email"))

        if r.expiry_date and not DATE_RE.match(r.expiry_date):
            errors.append(ValidationError(u, "expiry_date", r.expiry_date,
                                          "must be YYYY-MM-DD or empty"))

        if r.created_date and not DATE_RE.match(r.created_date):
            errors.append(ValidationError(u, "created_date", r.created_date,
                                          "must be YYYY-MM-DD or empty"))

        if r.uid and not r.uid.isdigit():
            errors.append(ValidationError(u, "uid", r.uid, "must be numeric or empty"))

        if r.gid and not r.gid.isdigit():
            errors.append(ValidationError(u, "gid", r.gid, "must be numeric or empty"))

    return errors


# ─── Shell helpers ──────────────────────────────────────────────────────

def run(cmd: list[str], dry_run: bool = False, check: bool = True,
        input_str: Optional[str] = None) -> subprocess.CompletedProcess:
    LOG.debug("CMD: %s", " ".join(cmd))
    if dry_run:
        LOG.info("DRY RUN: would run: %s", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, capture_output=True, text=True,
                          check=check, input=input_str)


def user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def get_uid_gid(username: str) -> tuple[int, int]:
    p = pwd.getpwnam(username)
    return p.pw_uid, p.pw_gid


def user_groups(username: str) -> set[str]:
    try:
        p = pwd.getpwnam(username)
    except KeyError:
        return set()
    primary = grp.getgrgid(p.pw_gid).gr_name
    return {primary} | {g.gr_name for g in grp.getgrall() if username in g.gr_mem}


def sacctmgr_user_exists(username: str) -> bool:
    res = run(["sacctmgr", "-nP", "show", "user", username, "format=user"],
              check=False)
    return res.returncode == 0 and username in res.stdout


# ─── Per-user operations ────────────────────────────────────────────────

class Reconciler:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.actions: list[Action] = []

    def log_action(self, username: str, kind: str, detail: str = "") -> None:
        a = Action(username, kind, detail)
        self.actions.append(a)
        prefix = "[DRY] " if self.dry_run else ""
        LOG.info("%s%s: %s %s", prefix, username, kind, detail)

    # ── Active user ─────────────────────────────────────────────────────
    def apply_active(self, row: UserRow) -> UserRow:
        u = row.username

        # 1. Create Unix user if missing
        if not user_exists(u):
            gecos = row.full_name or "FULL_NAME_PLACEHOLDER"
            cmd = ["useradd", "-m", "-s", "/bin/bash", "-c", gecos, u]
            run(cmd, dry_run=self.dry_run)
            self.log_action(u, "create_unix_user")
            row.created_date = dt.date.today().isoformat() if not row.created_date else row.created_date

        # 2. Populate UID/GID write-back
        if not self.dry_run and user_exists(u):
            uid, gid = get_uid_gid(u)
            row.uid = str(uid)
            row.gid = str(gid)

        # 3. Membership: tesla_users + correct tier group, remove other tier groups
        current = user_groups(u)
        wanted_tier_group = f"tesla-{row.tier}"
        for tier_name in VALID_TIERS:
            other_group = f"tesla-{tier_name}"
            if other_group in current and other_group != wanted_tier_group:
                run(["gpasswd", "-d", u, other_group], dry_run=self.dry_run, check=False)
                self.log_action(u, "remove_from_group", other_group)
        if wanted_tier_group not in current:
            run(["usermod", "-aG", wanted_tier_group, u], dry_run=self.dry_run)
            self.log_action(u, "add_to_group", wanted_tier_group)
        if TESLA_USERS_GROUP not in current:
            run(["usermod", "-aG", TESLA_USERS_GROUP, u], dry_run=self.dry_run)
            self.log_action(u, "add_to_group", TESLA_USERS_GROUP)

        # 4. SSH authorized_keys (overwrite — single source of truth)
        if row.ssh_pubkey:
            self.write_authorized_keys(u, row.ssh_pubkey)

        # 5. NAS dir
        self.ensure_nas_dir(u, row)

        # 6. SLURM accounting: add user to tier account if missing, set DefaultAccount
        self.ensure_slurm_user(u, row.tier)

        # 7. Quota for safety — ensure 200GB applied (in case user_policy ran before this user existed)
        # (storage_quotas role handles bulk apply; per-user safety re-apply here is cheap)
        if not self.dry_run and user_exists(u) and u not in ("root",):
            run(["setquota", "-u", u,
                 str((200 - 10) * 1024 * 1024), str(200 * 1024 * 1024),
                 "0", "0", "/home"], check=False)

        return row

    # ── Inactive user ───────────────────────────────────────────────────
    def apply_inactive(self, row: UserRow) -> UserRow:
        u = row.username
        if not user_exists(u):
            LOG.info("%s: status=inactive but user doesn't exist on system — nothing to do", u)
            return row

        # 1. Archive /home → /storage/nas/_archive/home_{user}_{ISO}.tar.gz
        home = Path(HOME_ROOT) / u
        if home.exists():
            self.archive_home(u)

        # 2. Remove from SLURM
        if sacctmgr_user_exists(u):
            run(["sacctmgr", "-i", "remove", "user", u], dry_run=self.dry_run, check=False)
            self.log_action(u, "sacctmgr_remove")

        # 3. userdel -r (removes /home/$u and mail spool)
        run(["userdel", "-r", u], dry_run=self.dry_run, check=False)
        self.log_action(u, "userdel_r")

        # 4. /storage/nas/$u is intentionally LEFT INTACT per policy.
        nas_dir = Path(NAS_ROOT) / u
        if nas_dir.exists():
            LOG.info("%s: leaving %s intact (admin manual cleanup)", u, nas_dir)

        return row

    # ── Helpers ─────────────────────────────────────────────────────────
    def write_authorized_keys(self, username: str, pubkey: str) -> None:
        if self.dry_run:
            self.log_action(username, "write_authorized_keys", "(dry run)")
            return
        try:
            p = pwd.getpwnam(username)
        except KeyError:
            return
        ssh_dir = Path(p.pw_dir) / ".ssh"
        ak = ssh_dir / "authorized_keys"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        os.chown(ssh_dir, p.pw_uid, p.pw_gid)

        # Idempotent: only rewrite if key changed
        existing = ak.read_text() if ak.exists() else ""
        if pubkey.strip() in existing:
            return
        ak.write_text(pubkey.rstrip() + "\n")
        ak.chmod(0o600)
        os.chown(ak, p.pw_uid, p.pw_gid)
        self.log_action(username, "write_authorized_keys")

    def ensure_nas_dir(self, username: str, row: UserRow) -> None:
        nas_dir = Path(NAS_ROOT) / username
        if self.dry_run:
            if not nas_dir.exists():
                self.log_action(username, "mkdir_nas", str(nas_dir))
            return
        try:
            p = pwd.getpwnam(username)
        except KeyError:
            return
        if not Path(NAS_ROOT).exists():
            LOG.warning("%s: %s does not exist; skipping NAS dir creation", username, NAS_ROOT)
            return
        nas_dir.mkdir(mode=0o700, exist_ok=True)
        os.chown(nas_dir, p.pw_uid, p.pw_gid)
        self.log_action(username, "ensure_nas_dir", str(nas_dir))

    def ensure_slurm_user(self, username: str, tier: str) -> None:
        # Add user to the matching account, set DefaultAccount, set DefaultQOS
        account = tier
        qos = f"tesla-{tier}"
        if sacctmgr_user_exists(username):
            run(["sacctmgr", "-i", "modify", "user", username, "set",
                 f"DefaultAccount={account}", f"DefaultQOS={qos}"],
                dry_run=self.dry_run, check=False)
            self.log_action(username, "sacctmgr_modify_user", f"account={account} qos={qos}")
        else:
            run(["sacctmgr", "-i", "add", "user", username,
                 f"Account={account}", f"DefaultAccount={account}", f"DefaultQOS={qos}"],
                dry_run=self.dry_run, check=False)
            self.log_action(username, "sacctmgr_add_user", f"account={account} qos={qos}")

    def archive_home(self, username: str) -> None:
        archive_dir = Path(ARCHIVE_ROOT)
        if self.dry_run:
            self.log_action(username, "archive_home", "(dry run)")
            return
        archive_dir.mkdir(parents=True, exist_ok=True)
        today = dt.date.today().isoformat()
        out = archive_dir / f"home_{username}_{today}.tar.gz"
        home = Path(HOME_ROOT) / username
        if not home.exists():
            return
        if out.exists():
            LOG.info("%s: archive %s already exists, skipping", username, out)
            return
        LOG.info("%s: archiving %s → %s", username, home, out)
        with tarfile.open(out, "w:gz") as tf:
            tf.add(home, arcname=username)
        out.chmod(0o600)
        self.log_action(username, "archive_home", str(out))


# ─── Main ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile Tesla users.csv against system state.")
    ap.add_argument("--csv", default=DEFAULT_CSV, help=f"CSV path (default: {DEFAULT_CSV})")
    ap.add_argument("--check", action="store_true", help="Dry run — show what would change, don't apply")
    ap.add_argument("--user", help="Operate on a single username (must be in CSV)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if os.geteuid() != 0 and not args.check:
        LOG.error("Must run as root (or with --check for dry run).")
        return 1

    with CSVFile(args.csv) as csvfile:
        rows = csvfile.read()
        if not rows:
            LOG.error("No rows in %s. Aborting.", args.csv)
            return 1

        # Validate everything before applying anything
        errs = validate_rows(rows)
        if errs:
            LOG.error("CSV validation failed with %d error(s):", len(errs))
            for e in errs:
                LOG.error("  %s", e)
            return 2

        LOG.info("CSV validation: %d row(s) OK", len(rows))

        # Surface keyless active users so admin knows who still needs an ssh key.
        keyless_active = [r.username for r in rows if r.status == "active" and not r.ssh_pubkey]
        if keyless_active:
            LOG.warning("%d active user(s) have NO ssh_pubkey in the CSV:", len(keyless_active))
            for u in keyless_active:
                LOG.warning("  %s", u)
            LOG.warning("sync will NOT touch their ~/.ssh/authorized_keys. Enroll keys manually.")

        target_rows = rows
        if args.user:
            target_rows = [r for r in rows if r.username == args.user]
            if not target_rows:
                LOG.error("User %r not found in CSV.", args.user)
                return 3

        reconciler = Reconciler(dry_run=args.check)

        for row in target_rows:
            try:
                if row.status == "active":
                    new_row = reconciler.apply_active(row)
                elif row.status == "inactive":
                    new_row = reconciler.apply_inactive(row)
                else:
                    LOG.warning("%s: unknown status %r, skipping", row.username, row.status)
                    continue
                # Replace in master list
                for i, r in enumerate(rows):
                    if r.username == new_row.username:
                        rows[i] = new_row
            except subprocess.CalledProcessError as e:
                LOG.error("%s: command failed: %s\n  stdout: %s\n  stderr: %s",
                          row.username, e, e.stdout, e.stderr)

        # Write back UID/GID/created_date (only if not dry-run, and only if we touched anything)
        if not args.check and reconciler.actions:
            csvfile.write(rows)
            LOG.info("CSV updated: %s", args.csv)

        LOG.info("Done. %d action(s) %s.",
                 len(reconciler.actions),
                 "would be applied" if args.check else "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
