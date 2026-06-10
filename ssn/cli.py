from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import config_hash, render_profile, repo_root, resolve_profile, summary_text
from .users import (
    action_dicts,
    apply_user_actions,
    discover_users,
    load_state,
    load_users,
    plan_user_sync,
    validate_users,
    write_users,
)
from .yamlutil import dump_yaml


DEFAULT_USERS = "/etc/slurm-single-node/users.yml"
DEFAULT_STATE = "/var/lib/slurm-single-node/users-state.yml"


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    invoked = Path(prog or sys.argv[0]).name
    direct = {
        "ssn-render": render_cmd,
        "ssn-discover": discover_cmd,
        "ssn-verify": verify_cmd,
        "ssn-apply": apply_cmd,
        "ssn-sync-users": sync_users_cmd,
        "ssn-gpu-status": gpu_status_cmd,
        "ssn-archive-status": archive_status_cmd,
    }
    if invoked in direct:
        return direct[invoked](argv)

    parser = argparse.ArgumentParser(prog=invoked)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("render")
    sub.add_parser("discover")
    sub.add_parser("verify")
    sub.add_parser("apply")
    sub.add_parser("sync-users")
    sub.add_parser("gpu-status")
    sub.add_parser("archive-status")
    ns, rest = parser.parse_known_args(argv)
    return {
        "render": render_cmd,
        "discover": discover_cmd,
        "verify": verify_cmd,
        "apply": apply_cmd,
        "sync-users": sync_users_cmd,
        "gpu-status": gpu_status_cmd,
        "archive-status": archive_status_cmd,
    }[ns.command](rest)


def render_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-render")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--allow-review-required", action="store_true")
    parser.add_argument("--json", action="store_true", help="print resolved JSON to stdout")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    output = args.output_dir or root / "build" / "rendered" / args.profile
    try:
        resolved = render_profile(
            args.profile,
            output,
            root,
            allow_review_required=args.allow_review_required,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(resolved, indent=2, sort_keys=True))
    else:
        print(summary_text(resolved), end="")
        print(f"Rendered artifacts: {output}")
    return 0


def discover_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-discover")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--users", action="store_true", help="emit discovered users.yml draft")
    parser.add_argument("--format", choices=["json", "yaml"], default="yaml")
    args = parser.parse_args(argv)

    if args.users:
        data = discover_users()
    else:
        data = discover_system()
    if args.format == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(dump_yaml(data), end="")
    return 0


def verify_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-verify")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--allow-review-required", action="store_true")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    try:
        resolved = resolve_profile(args.profile, root, allow_review_required=args.allow_review_required)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    checks = verify_local(resolved)
    failed = [check for check in checks if check["status"] == "FAIL"]
    for check in checks:
        print(f"{check['status']:4s} {check['name']}: {check['detail']}")
    return 1 if failed else 0


def apply_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-apply")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--check", action="store_true", help="run ansible in check mode")
    parser.add_argument("--run", action="store_true", help="actually invoke ansible-playbook")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    output = Path(args.output_dir or root / "build" / "rendered" / args.profile)
    try:
        resolved = render_profile(args.profile, output, root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    ansible_vars = output / "ansible-vars.json"
    cmd = [
        "ansible-playbook",
        "-i",
        str(root / "ansible" / "inventories" / "local.ini"),
        str(root / "ansible" / "site.yml"),
        "-e",
        f"@{ansible_vars}",
    ]
    if args.check:
        cmd.append("--check")
    print(summary_text(resolved), end="")
    print(f"Config hash: {config_hash(resolved)}")
    print("Ansible command:")
    print("  " + " ".join(cmd))
    if not args.run:
        print("Dry planning only. Re-run with --run to invoke ansible-playbook.")
        return 0
    if shutil.which("ansible-playbook") is None:
        print("ERROR: ansible-playbook is not installed.", file=sys.stderr)
        return 2
    return subprocess.call(cmd)


def sync_users_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-sync-users")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--users", default=DEFAULT_USERS)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--user", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    try:
        resolved = resolve_profile(args.profile, root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    users_doc = load_users(args.users)
    state_doc = load_state(args.state)
    errors = validate_users(users_doc, resolved)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    actions = plan_user_sync(users_doc, state_doc, resolved, single_user=args.user)
    if args.json:
        print(json.dumps({"actions": action_dicts(actions)}, indent=2, sort_keys=True))
    else:
        if not actions:
            print("No user changes planned.")
        for action in actions:
            flag = "RISKY" if action.risky else "PLAN"
            print(f"{flag:5s} {action.username:20s} {action.action:28s} {action.detail}")
    if args.apply:
        apply_user_actions(actions, users_doc, resolved)
    return 0


def gpu_status_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-gpu-status")
    parser.add_argument("--snapshot", default="/run/slurm-single-node/gpu-status.json")
    args = parser.parse_args(argv)
    path = Path(args.snapshot)
    if path.exists():
        print(path.read_text(), end="")
        return 0
    if shutil.which("nvidia-smi") is None:
        print("No NVIDIA GPU status is available on this CPU-only host.")
        return 0
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader",
    ]
    return subprocess.call(cmd)


def archive_status_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-archive-status")
    parser.add_argument("--state", default=DEFAULT_STATE)
    args = parser.parse_args(argv)
    state = load_state(args.state)
    users = state.get("users") or {}
    found = False
    for username, entry in sorted(users.items()):
        archive_state = entry.get("archive_state")
        if archive_state:
            found = True
            print(f"{username:20s} {archive_state}")
    if not found:
        print("No inactive archive workflows recorded.")
    return 0


def discover_system() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "host": {
            "node_name": platform.node(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "resources": {
            "cpus_total": os.cpu_count(),
            "memory_total_mb": _mem_total_mb(),
            "cgroup_fs": _command_stdout(["stat", "-fc", "%T", "/sys/fs/cgroup"]),
        },
        "commands": {
            "ansible_playbook": shutil.which("ansible-playbook"),
            "slurmd": shutil.which("slurmd"),
            "sacctmgr": shutil.which("sacctmgr"),
            "nvidia_smi": shutil.which("nvidia-smi"),
        },
        "mounts": {
            "home": _findmnt("/home"),
            "data": _findmnt("/data"),
            "scratch": _findmnt("/scratch"),
        },
    }


def verify_local(resolved: dict[str, Any]) -> list[dict[str, str]]:
    checks = []
    checks.append(_check("profile_resolves", True, resolved["profile"]))
    checks.append(_check("cgroup_v2", _command_stdout(["stat", "-fc", "%T", "/sys/fs/cgroup"]) == "cgroup2fs", "/sys/fs/cgroup"))
    checks.append(_check("slurmd_command", shutil.which("slurmd") is not None, shutil.which("slurmd") or "missing"))
    checks.append(_check("sacctmgr_command", shutil.which("sacctmgr") is not None, shutil.which("sacctmgr") or "missing"))
    checks.append(_check("ansible_playbook", shutil.which("ansible-playbook") is not None, shutil.which("ansible-playbook") or "missing"))
    for label, mount in (resolved["derived"].get("paths") or {}).items():
        if mount:
            checks.append(_check(f"mount_{label}", Path(mount).exists(), str(mount)))
    if resolved["derived"]["has_gpus"]:
        checks.append(_check("nvidia_smi", shutil.which("nvidia-smi") is not None, shutil.which("nvidia-smi") or "missing"))
    return checks


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _command_stdout(cmd: list[str]) -> str | None:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def _findmnt(path: str) -> str | None:
    out = _command_stdout(["findmnt", "-no", "TARGET,SOURCE,FSTYPE,OPTIONS", path])
    return out


def _mem_total_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(int(line.split()[1]) / 1024)
    except OSError:
        return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
