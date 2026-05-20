#!/usr/bin/env python3
"""
bootstrap_users_csv.py — scan existing Unix users on this system and emit a
users.csv compatible with sync_users.py.

Run this ONCE when adopting the cluster automation. Output goes to
users.csv.discovered by default; admin reviews, fills in real names/emails,
adjusts tiers, then `mv users.csv.discovered users.csv`.

The CSV itself goes to --out (a file path, or '-' for stdout).
The human-readable summary always goes to stderr — so you can safely do:
    sudo bootstrap_users_csv.py --out - > /etc/tesla-cluster/users.csv.discovered

Defaults for every existing user:
  full_name      = GECOS field if present, else "FULL_NAME_PLACEHOLDER"
  email          = "EMAIL_PLACEHOLDER"
  ssh_pubkey     = first key from ~/.ssh/authorized_keys (warn if multiple, empty if none)
  tier           = gpu1
  status         = active
  expiry_date    = (empty)
  uid, gid       = from system
  created_date   = today

Usage:
    sudo bootstrap_users_csv.py                              # writes ./users.csv.discovered
    sudo bootstrap_users_csv.py --out /etc/tesla-cluster/users.csv.discovered
    sudo bootstrap_users_csv.py --out - > somefile.csv       # CSV to stdout
"""
import argparse
import csv
import datetime as dt
import pwd
import re
import sys
from pathlib import Path

# Common system-y usernames to exclude even if their UID falls in the human range
DEFAULT_EXCLUDES = {
    "root", "nobody", "nfsnobody", "sshd", "systemd-network",
    "systemd-resolve", "systemd-timesync", "messagebus", "syslog",
    "mysql", "redis", "postgres", "mongodb", "munge", "slurm",
    "ubuntu", "kube",
}

SSH_KEY_RE = re.compile(r"^(ssh-(rsa|dss|ed25519)|ecdsa-sha2-\S+)\s+\S+(\s+\S+)?$")

CSV_COLUMNS = [
    "username", "full_name", "email", "ssh_pubkey",
    "tier", "status", "expiry_date",
    "uid", "gid", "created_date", "notes",
]


def first_ssh_key(home: str, username: str) -> tuple[str, int]:
    """Return (first_key, total_key_count). Empty string if none."""
    ak = Path(home) / ".ssh" / "authorized_keys"
    if not ak.exists():
        return "", 0
    try:
        lines = ak.read_text().splitlines()
    except (PermissionError, OSError):
        return "", 0
    keys = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#") and SSH_KEY_RE.match(ln.strip())]
    if not keys:
        return "", 0
    return keys[0], len(keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid-min", type=int, default=1000)
    ap.add_argument("--uid-max", type=int, default=60000)
    ap.add_argument("--out", default="users.csv.discovered")
    ap.add_argument("--exclude", default="", help="comma-separated usernames to skip")
    ap.add_argument("--default-tier", default="gpu1")
    args = ap.parse_args()

    excludes = DEFAULT_EXCLUDES | set(filter(None, args.exclude.split(",")))
    today = dt.date.today().isoformat()
    multi_key_users: list[tuple[str, int]] = []
    no_key_users: list[str] = []

    rows = []
    for p in pwd.getpwall():
        if p.pw_uid < args.uid_min or p.pw_uid > args.uid_max:
            continue
        if p.pw_name in excludes:
            continue
        ssh_key, n_keys = first_ssh_key(p.pw_dir, p.pw_name)
        if n_keys > 1:
            multi_key_users.append((p.pw_name, n_keys))
        if n_keys == 0:
            no_key_users.append(p.pw_name)

        gecos = (p.pw_gecos or "").split(",")[0].strip() or "FULL_NAME_PLACEHOLDER"

        rows.append({
            "username": p.pw_name,
            "full_name": gecos,
            "email": "EMAIL_PLACEHOLDER",
            "ssh_pubkey": ssh_key,
            "tier": args.default_tier,
            "status": "active",
            "expiry_date": "",
            "uid": str(p.pw_uid),
            "gid": str(p.pw_gid),
            "created_date": today,
            "notes": "discovered by bootstrap_users_csv.py",
        })

    rows.sort(key=lambda r: int(r["uid"]))

    # CSV destination: file path, or '-' for stdout
    if args.out == "-":
        writer = csv.DictWriter(sys.stdout, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)
        out_label = "<stdout>"
    else:
        out = Path(args.out).resolve()
        with out.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            writer.writerows(rows)
        out_label = str(out)

    # All human-readable output goes to stderr so `>` redirect captures only CSV.
    err = sys.stderr
    print(f"\nWrote {len(rows)} user(s) to {out_label}", file=err)
    if multi_key_users:
        print("\nWARNING - these users have MULTIPLE ssh keys; only the first was kept:", file=err)
        for u, n in multi_key_users:
            print(f"  {u:20s}  {n} keys", file=err)
        print("  (review their ~/.ssh/authorized_keys manually and merge into the CSV)", file=err)
    if no_key_users:
        print("\nNOTE - these users have NO authorized_keys; ssh_pubkey is empty:", file=err)
        for u in no_key_users:
            print(f"  {u}", file=err)
        print("  (existing users: OK — sync_users.py will not manage their authorized_keys.", file=err)
        print("   if you want sync to push a key, fill it in manually before running sync_users.py)", file=err)

    print("\nNext steps:", file=err)
    print(f"  1. Review {out_label}", file=err)
    print(f"  2. Fill in real full_name and email values (or leave the placeholders)", file=err)
    print("  3. Set tier per user (gpu1 default; bump to gpu2/gpu4/deadline as needed)", file=err)
    print("  4. For graduated/inactive users: set status=inactive (will archive /home)", file=err)
    if args.out != "-":
        print(f"  5. sudo mv {out_label} /etc/tesla-cluster/users.csv", file=err)
    print("  6. sudo ansible-playbook -i inventories/hosts.ini sync_users.yml --check", file=err)
    print("  7. sudo ansible-playbook -i inventories/hosts.ini sync_users.yml", file=err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
