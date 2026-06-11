from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import config_hash, render_profile, repo_root, resolve_profile, summary_text
from .ops import create_plan_token, queued_jobs, validate_feature_gates, validate_plan_token, write_protected_json
from .users import (
    action_dicts,
    apply_user_actions,
    backup_file,
    backup_retention_report,
    discover_users,
    load_state,
    load_users,
    plan_user_sync,
    validate_state,
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
        "ssn-scratch-cleanup": scratch_cleanup_cmd,
        "ssn-plan-token": plan_token_cmd,
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
    sub.add_parser("scratch-cleanup")
    sub.add_parser("plan-token")
    ns, rest = parser.parse_known_args(argv)
    return {
        "render": render_cmd,
        "discover": discover_cmd,
        "verify": verify_cmd,
        "apply": apply_cmd,
        "sync-users": sync_users_cmd,
        "gpu-status": gpu_status_cmd,
        "archive-status": archive_status_cmd,
        "scratch-cleanup": scratch_cleanup_cmd,
        "plan-token": plan_token_cmd,
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
    parser.add_argument("--force", action="store_true", help="allow service-changing apply while Slurm jobs are queued")
    parser.add_argument("--plan-token", default=None, help="reviewed token required with --force over risky operations")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    apply_id = f"apply-{stamp}"
    if args.output_dir:
        output = Path(args.output_dir).resolve()
        plan_dir = output.parent
    elif args.run and os.geteuid() == 0:
        plan_dir = Path("/var/lib/slurm-single-node/plans") / apply_id
        output = plan_dir / "rendered"
    elif args.run:
        plan_dir = Path("/tmp") / apply_id
        output = plan_dir / "rendered"
    else:
        plan_dir = root / "build" / "plans" / apply_id
        output = root / "build" / "rendered" / args.profile
    report_file = plan_dir / "apply-report.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "apply",
        "apply_id": apply_id,
        "profile": args.profile,
        "repo": str(root),
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "running",
        "phases": [],
    }
    try:
        resolved = render_profile(args.profile, output, root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    report["config_hash"] = config_hash(resolved)
    report["rendered_dir"] = str(output)
    admin_group = resolved.get("derived", {}).get("admin_group", "slurm_admins")
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
    try:
        if shutil.which("ansible-playbook") is None:
            raise RuntimeError("ansible-playbook is not installed")
        if not args.check:
            feature_errors = validate_feature_gates(resolved, mode="apply")
            if feature_errors:
                raise RuntimeError("feature gate failed: " + "; ".join(feature_errors))
            report["phases"].append({"name": "feature_gates", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            jobs = queued_jobs()
            if jobs:
                report["blocked_jobs"] = jobs
                report["risk"] = "queued_jobs"
                if not args.force:
                    raise RuntimeError(
                        "refusing service-changing apply while Slurm has queued jobs; "
                        "create a reviewed plan token, then re-run with --force --plan-token"
                    )
                if not args.plan_token:
                    raise RuntimeError("queued jobs require --force plus --plan-token")
                validate_plan_token(args.plan_token, report, risk="queued_jobs")
                report["phases"].append({"name": "queued_job_token", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            else:
                report["phases"].append({"name": "queued_job_gate", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
        write_protected_json(report_file, report, group=admin_group)
        rc = subprocess.call(cmd)
        if rc != 0:
            raise RuntimeError(f"ansible-playbook failed with rc={rc}")
        report["status"] = "ok"
        report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_protected_json(report_file, report, group=admin_group)
        print(f"Apply report: {report_file}")
        return 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_protected_json(report_file, report, group=admin_group)
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Apply report: {report_file}", file=sys.stderr)
        return 2


def plan_token_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-plan-token")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--plan", required=True)
    create.add_argument("--risk", required=True)
    create.add_argument("--reason", required=True)
    create.add_argument("--expires-hours", type=int, default=24)
    create.add_argument("--store-root", default="/var/lib/slurm-single-node/plan-tokens")
    args = parser.parse_args(argv)

    if args.command == "create":
        try:
            token, record = create_plan_token(
                args.plan,
                risk=args.risk,
                reason=args.reason,
                store_root=args.store_root,
                expires_hours=args.expires_hours,
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(token)
        print(f"token_id={record['token_id']}")
        print(f"risk={record['risk']}")
        print(f"expires_at={record['expires_at']}")
        return 0
    return 2


def sync_users_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-sync-users")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--users", default=DEFAULT_USERS)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--user", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--backup-root", default="/var/backups/slurm-single-node/users")
    parser.add_argument("--retention-days", type=int, default=90)
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    try:
        resolved = resolve_profile(args.profile, root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    users_doc = load_users(args.users)
    state_doc = load_state(args.state)
    errors = [*validate_state(state_doc), *validate_users(users_doc, resolved, state_doc=state_doc)]
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    actions = plan_user_sync(users_doc, state_doc, resolved, single_user=args.user)
    if args.json and not args.apply:
        print(json.dumps({"actions": action_dicts(actions)}, indent=2, sort_keys=True))
    elif not args.json:
        if not actions:
            print("No user changes planned.")
        for action in actions:
            flag = "RISKY" if action.risky else "PLAN"
            print(f"{flag:5s} {action.username:20s} {action.action:28s} {action.detail}")
    if args.apply:
        users_backup = backup_file(args.users, args.backup_root)
        state_backup = backup_file(args.state, args.backup_root)
        if not args.json:
            if users_backup:
                print(f"Backed up users.yml: {users_backup}")
            if state_backup:
                print(f"Backed up users-state.yml: {state_backup}")
        state_doc = apply_user_actions(actions, users_doc, resolved, state_doc=state_doc)
        state_path = Path(args.state)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(dump_yaml(state_doc))
        retention = backup_retention_report(args.backup_root, retention_days=args.retention_days)
        if args.json:
            print(
                json.dumps(
                    {"actions": action_dicts(actions), "backup_retention": retention},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(
                "Backup retention report-only: "
                f"{retention['candidate_count']} item(s) older than {args.retention_days} days under {args.backup_root}"
            )
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


def scratch_cleanup_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-scratch-cleanup")
    parser.add_argument("--root", default="/scratch")
    parser.add_argument("--jobs-root", default="/scratch/jobs")
    parser.add_argument("--age-days", type=int, default=30)
    parser.add_argument("--report", default="/var/log/slurm/scratch-cleanup.json")
    parser.add_argument("--apply", action="store_true", help="delete eligible paths")
    parser.add_argument("--yes-delete", action="store_true", help="required with --apply")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    jobs_root = Path(args.jobs_root).resolve()
    if str(root) != "/scratch":
        print(f"ERROR: refusing scratch cleanup outside /scratch: {root}", file=sys.stderr)
        return 2
    if str(jobs_root) != "/scratch/jobs":
        print(f"ERROR: refusing jobs root outside /scratch/jobs: {jobs_root}", file=sys.stderr)
        return 2
    if args.apply and not args.yes_delete:
        print("ERROR: --apply requires --yes-delete", file=sys.stderr)
        return 2
    if not root.exists():
        print("Scratch root is absent; nothing to report.")
        return 0

    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - args.age_days * 86400
    candidates = []
    for child in sorted(root.iterdir()):
        if child == jobs_root:
            continue
        if child.is_symlink():
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        candidates.append({
            "path": str(child),
            "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
            "type": "directory" if child.is_dir() else "file",
        })

    report = {
        "schema_version": 1,
        "mode": "apply" if args.apply else "report_only",
        "root": str(root),
        "jobs_root_excluded": str(jobs_root),
        "age_days": args.age_days,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Scratch cleanup report written: {report_path}")
    print(f"Eligible top-level scratch paths: {len(candidates)}")
    if args.apply:
        print("Deletion mode is intentionally not implemented in v1; report only.")
        return 2
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
        "gpus": _discover_nvidia_gpus(),
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
            if label in {"data", "scratch"}:
                mounted = _findmnt(str(mount))
                checks.append(_check(f"mount_{label}", mounted is not None, mounted or str(mount)))
            else:
                checks.append(_check(f"path_{label}", Path(mount).exists(), str(mount)))
    if resolved["derived"]["has_gpus"]:
        gpus = _discover_nvidia_gpus()
        expected = int(resolved["hardware"]["gpus"])
        checks.append(_check("nvidia_smi", len(gpus) == expected, f"expected={expected} discovered={len(gpus)}"))
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


def _discover_nvidia_gpus() -> list[dict[str, str]]:
    if shutil.which("nvidia-smi") is None:
        return []
    out = _command_stdout([
        "nvidia-smi",
        "--query-gpu=index,name,uuid,pci.bus_id,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        gpus.append({
            "index": parts[0],
            "name": parts[1],
            "uuid": parts[2],
            "pci_bus_id": parts[3],
            "memory_total_mb": parts[4],
        })
    return gpus


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
