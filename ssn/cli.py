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
from .gpu import (
    GPU_RECOVERY_RISK,
    GPU_RECOVERY_STATE,
    gpu_recovery_plan,
    gpu_verification_errors,
    gpu_verification_report,
)
from .login import (
    DEFAULT_FIXTURE_PREFIX as DEFAULT_LOGIN_FIXTURE_PREFIX,
    DEFAULT_TARGET_SCOPE as DEFAULT_LOGIN_TARGET_SCOPE,
    collect_gpu_status_snapshot,
    apply_login_isolation,
    login_isolation_report,
    login_isolation_status,
    login_isolation_status_for_report,
)
from .modules import modules_status_report, modules_verify_errors, modules_verify_report
from .ops import (
    collect_capabilities,
    create_plan_token,
    drain_node,
    queued_jobs,
    resume_node,
    secure_path,
    validate_feature_gates,
    validate_installed_slurm_features,
    validate_plan_token,
    validate_resolved_slurm_features,
    wait_for_no_active_jobs,
    write_protected_json,
)
from .storage import (
    DEFAULT_FIXTURE_PREFIX,
    STORAGE_QUOTA_ENABLE_RISK,
    apply_fixture_quotas,
    apply_fixture_scratch_cleanup,
    enable_storage_quotas,
    parse_fixture_quota_overrides,
    quota_capability_report,
    scratch_cleanup_report,
    scratch_health_report,
    storage_quota_plan,
    storage_quota_status,
    write_scratch_health_state,
)
from .safety import RETENTION_DELETE_RISK, apply_test_retention_cleanup, retention_cleanup_report
from .units import duration_to_seconds
from .users import (
    INACTIVE_ARCHIVE_RISK,
    INACTIVE_LOCAL_ONLY_RISK,
    action_dicts,
    apply_user_actions,
    backup_file,
    backup_retention_report,
    discover_users,
    inactive_actions,
    inactive_plan_report,
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
DEFAULT_TOKEN_STORE = "/var/lib/slurm-single-node/plan-tokens"


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
        "ssn-gpu-collector": gpu_collector_cmd,
        "ssn-gpu-recovery": gpu_recovery_cmd,
        "ssn-login-isolation": login_isolation_cmd,
        "ssn-login-status": login_status_cmd,
        "ssn-modules": modules_cmd,
        "ssn-archive-status": archive_status_cmd,
        "ssn-scratch-cleanup": scratch_cleanup_cmd,
        "ssn-retention-cleanup": retention_cleanup_cmd,
        "ssn-storage-quotas": storage_quotas_cmd,
        "ssn-scratch-health": scratch_health_cmd,
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
    sub.add_parser("gpu-collector")
    sub.add_parser("gpu-recovery")
    sub.add_parser("login-isolation")
    sub.add_parser("login-status")
    sub.add_parser("modules")
    sub.add_parser("archive-status")
    sub.add_parser("scratch-cleanup")
    sub.add_parser("retention-cleanup")
    sub.add_parser("storage-quotas")
    sub.add_parser("scratch-health")
    sub.add_parser("plan-token")
    ns, rest = parser.parse_known_args(argv)
    return {
        "render": render_cmd,
        "discover": discover_cmd,
        "verify": verify_cmd,
        "apply": apply_cmd,
        "sync-users": sync_users_cmd,
        "gpu-status": gpu_status_cmd,
        "gpu-collector": gpu_collector_cmd,
        "gpu-recovery": gpu_recovery_cmd,
        "login-isolation": login_isolation_cmd,
        "login-status": login_status_cmd,
        "modules": modules_cmd,
        "archive-status": archive_status_cmd,
        "scratch-cleanup": scratch_cleanup_cmd,
        "retention-cleanup": retention_cleanup_cmd,
        "storage-quotas": storage_quotas_cmd,
        "scratch-health": scratch_health_cmd,
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
    parser.add_argument("--drain", action="store_true", help="drain the node and wait for active jobs before service-changing apply")
    parser.add_argument("--drain-timeout", default="10m", help="maximum wait for running/completing jobs when --drain is used")
    parser.add_argument("--drain-reason", default="SSN apply", help="Slurm node drain reason when --drain is used")
    args = parser.parse_args(argv)
    if args.force and args.drain:
        print("ERROR: --force and --drain are mutually exclusive apply safety modes", file=sys.stderr)
        return 2

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
    report["capabilities"] = collect_capabilities(resolved, mode="apply")
    report["login_isolation"] = login_isolation_status_for_report(resolved)
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
        rendered_errors = validate_resolved_slurm_features(resolved)
        if rendered_errors:
            raise RuntimeError("rendered Slurm feature validation failed: " + "; ".join(rendered_errors))
        report["phases"].append({"name": "rendered_feature_validation", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
        drain_info: dict[str, Any] | None = None
        apply_started = False
        if not args.check:
            feature_errors = validate_feature_gates(resolved, mode="apply", capabilities=report["capabilities"])
            if feature_errors:
                raise RuntimeError("feature gate failed: " + "; ".join(feature_errors))
            report["phases"].append({"name": "feature_gates", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            jobs = queued_jobs()
            if args.drain:
                timeout = duration_to_seconds(args.drain_timeout)
                reason = f"{args.drain_reason} ({apply_id})"
                drain_info = drain_node(resolved["identity"]["node_name"], reason)
                report["drain"] = drain_info
                report["phases"].append({"name": "node_drain", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat(), "detail": drain_info})
                active = wait_for_no_active_jobs(timeout)
                if active:
                    report["blocked_jobs"] = active
                    if drain_info.get("initiated_by_ssn"):
                        resume_node(resolved["identity"]["node_name"])
                        drain_info["initiated_by_ssn"] = False
                        report["phases"].append({"name": "node_resume_after_drain_timeout", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
                    raise RuntimeError(f"drain timed out with active jobs still present: {active}")
                report["phases"].append({"name": "drain_wait", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            elif jobs:
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
        apply_started = True
        rc = subprocess.call(cmd)
        if rc != 0:
            raise RuntimeError(f"ansible-playbook failed with rc={rc}")
        if not args.check:
            installed_errors = validate_installed_slurm_features(resolved)
            if installed_errors:
                raise RuntimeError("installed Slurm feature validation failed: " + "; ".join(installed_errors))
            report["phases"].append({"name": "installed_feature_validation", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            if resolved["derived"]["has_gpus"]:
                gpu_report = gpu_verification_report(resolved)
                report["gpu_verification"] = gpu_report
                gpu_errors = gpu_verification_errors(gpu_report)
                if gpu_errors:
                    raise RuntimeError("GPU verification failed: " + "; ".join(gpu_errors))
                report["phases"].append({"name": "gpu_verification", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            modules_policy = resolved.get("resolved_policies", {}).get("modules") or {}
            if modules_policy.get("lmod"):
                modules_report = modules_verify_report(resolved)
                report["modules_verification"] = modules_report
                module_errors = modules_verify_errors(modules_report)
                if module_errors:
                    raise RuntimeError("module verification failed: " + "; ".join(module_errors))
                report["phases"].append({"name": "modules_verification", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            if drain_info and drain_info.get("initiated_by_ssn"):
                resume_node(resolved["identity"]["node_name"])
                report["phases"].append({"name": "node_resume", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
        report["status"] = "ok"
        report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_protected_json(report_file, report, group=admin_group)
        print(f"Apply report: {report_file}")
        return 0
    except Exception as exc:
        if "drain_info" in locals() and drain_info and drain_info.get("initiated_by_ssn") and not locals().get("apply_started", False):
            try:
                resume_node(resolved["identity"]["node_name"])
                report["phases"].append({"name": "node_resume_after_preapply_failure", "status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()})
            except Exception as resume_exc:
                report["manual_recovery"] = f"scontrol update NodeName={resolved['identity']['node_name']} State=RESUME"
                report["resume_error"] = str(resume_exc)
        elif "drain_info" in locals() and drain_info and drain_info.get("initiated_by_ssn"):
            report["manual_recovery"] = f"scontrol update NodeName={resolved['identity']['node_name']} State=RESUME"
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
    parser.add_argument("--dry-run", action="store_true", help="plan only; this is the default without --apply")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plan-output", default=None, help="write inactive lifecycle plan report to this path")
    parser.add_argument("--plan-token", default=None, help="reviewed token required for inactive lifecycle apply")
    parser.add_argument("--token-store", default=DEFAULT_TOKEN_STORE)
    parser.add_argument("--backup-root", default="/var/backups/slurm-single-node/users")
    parser.add_argument("--retention-days", type=int, default=90)
    parser.add_argument("--quota-report", action="store_true", help="report quota capability for fixture users")
    parser.add_argument(
        "--apply-fixture-quotas",
        action="store_true",
        help="apply quotas only for users matching --quota-fixture-prefix when quotas are already active",
    )
    parser.add_argument("--quota-fixture-prefix", default=DEFAULT_FIXTURE_PREFIX)
    parser.add_argument(
        "--fixture-quota",
        action="append",
        default=[],
        metavar="LABEL=SIZE",
        help="fixture-only quota override, for example home=64MB data=64MB scratch=128MB",
    )
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
    quota_requested = args.quota_report or args.apply_fixture_quotas
    if quota_requested and not args.apply and not args.dry_run:
        return _sync_users_quota_request(args, users_doc, resolved)
    actions = plan_user_sync(users_doc, state_doc, resolved, single_user=args.user)
    inactive = inactive_actions(actions)
    inactive_report = None
    inactive_report_path = None
    if inactive:
        inactive_report = inactive_plan_report(actions, users_doc, state_doc, resolved)
        inactive_report_path = Path(args.plan_output) if args.plan_output else _default_sync_plan_path("inactive-plan.json")
        write_protected_json(inactive_report_path, inactive_report, group=resolved["derived"]["admin_group"])
    if args.json and not args.apply and not quota_requested:
        payload = {"actions": action_dicts(actions)}
        if inactive_report:
            payload["inactive_plan"] = inactive_report
            payload["inactive_plan_path"] = str(inactive_report_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif not args.json:
        if not actions:
            print("No user changes planned.")
        for action in actions:
            flag = "RISKY" if action.risky else "PLAN"
            print(f"{flag:5s} {action.username:20s} {action.action:28s} {action.detail}")
        if inactive_report_path:
            print(f"Inactive lifecycle plan: {inactive_report_path}")
            print(f"Inactive lifecycle risks: {', '.join(inactive_report.get('risks') or [inactive_report.get('risk')])}")
            backup = inactive_report.get("backup") or {}
            print(
                "Inactive backup hooks: "
                f"required={backup.get('required')} "
                f"dir={backup.get('directory')} "
                f"executable={len(backup.get('executable_hooks') or [])}"
            )
    if args.apply:
        inactive_local_only_override = False
        if inactive:
            if not args.plan_token:
                print("ERROR: inactive lifecycle apply requires --plan-token", file=sys.stderr)
                print(f"Inactive lifecycle plan: {inactive_report_path}", file=sys.stderr)
                return 2
            token_risk = None
            try:
                validate_plan_token(args.plan_token, inactive_report or {}, risk=INACTIVE_ARCHIVE_RISK, store_root=args.token_store, mark_used=False)
                token_risk = INACTIVE_ARCHIVE_RISK
            except Exception as exc:
                try:
                    validate_plan_token(args.plan_token, inactive_report or {}, risk=INACTIVE_LOCAL_ONLY_RISK, store_root=args.token_store, mark_used=False)
                    token_risk = INACTIVE_LOCAL_ONLY_RISK
                    inactive_local_only_override = True
                except Exception:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    return 2
            backup = (inactive_report or {}).get("backup") or {}
            if (
                token_risk == INACTIVE_ARCHIVE_RISK
                and backup.get("required")
                and not backup.get("executable_hooks")
            ):
                print(
                    "ERROR: inactive lifecycle requires an executable backup hook "
                    f"under {backup.get('directory')} or a reviewed local-only override token",
                    file=sys.stderr,
                )
                return 2
            try:
                validate_plan_token(
                    args.plan_token,
                    inactive_report or {},
                    risk=token_risk or INACTIVE_ARCHIVE_RISK,
                    store_root=args.token_store,
                )
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        users_backup = backup_file(args.users, args.backup_root)
        state_backup = backup_file(args.state, args.backup_root)
        if not args.json:
            if users_backup:
                print(f"Backed up users.yml: {users_backup}")
            if state_backup:
                print(f"Backed up users-state.yml: {state_backup}")
        state_doc = apply_user_actions(
            actions,
            users_doc,
            resolved,
            state_doc=state_doc,
            allow_inactive_fixture=bool(inactive),
            inactive_local_only_override=inactive_local_only_override,
        )
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
    if args.quota_report or args.apply_fixture_quotas:
        return _sync_users_quota_request(args, users_doc, resolved)
    return 0


def _sync_users_quota_request(args: argparse.Namespace, users_doc: dict[str, Any], resolved: dict[str, Any]) -> int:
    try:
        quota_overrides = parse_fixture_quota_overrides(args.fixture_quota)
        quota = (
            apply_fixture_quotas(
                users_doc,
                resolved,
                fixture_prefix=args.quota_fixture_prefix,
                quota_overrides=quota_overrides,
            )
            if args.apply_fixture_quotas
            else quota_capability_report(
                users_doc,
                resolved,
                fixture_prefix=args.quota_fixture_prefix,
                quota_overrides=quota_overrides,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"quota": quota}, indent=2, sort_keys=True))
    else:
        print(
            f"Quota {quota['mode']}: fixtures={len(quota.get('fixture_users', []))} "
            f"mounts={','.join(sorted(quota.get('mounts', {}))) or 'none'}"
        )
        for action in quota.get("actions", []):
            print(
                f"QUOTA {action.get('user', '-'):20s} "
                f"{action.get('mount', '-'):8s} {action.get('status')} "
                f"{action.get('reason') or action.get('path') or ''}"
            )
    return 0


def storage_quotas_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-storage-quotas")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status"):
        command = sub.add_parser(name)
        command.add_argument("--profile", default="cpu-dev-local")
        command.add_argument("--repo", default=None)
        command.add_argument("--mount", action="append", choices=["home", "data", "scratch"], default=[])
        command.add_argument("--fstab", default="/etc/fstab")
        command.add_argument("--report", default=None)
        command.add_argument("--json", action="store_true")
    enable = sub.add_parser("enable")
    enable.add_argument("--plan", required=True)
    enable.add_argument("--plan-token", required=True)
    enable.add_argument("--token-store", default=DEFAULT_TOKEN_STORE)
    enable.add_argument("--fstab", default="/etc/fstab")
    enable.add_argument("--backup-root", default="/var/backups/slurm-single-node/fstab")
    enable.add_argument("--report", default=None)
    enable.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command in {"plan", "status"}:
        try:
            resolved = resolve_profile(args.profile, repo_root(args.repo))
            labels = args.mount or None
            report = (
                storage_quota_plan(resolved, labels=labels, fstab_path=args.fstab)
                if args.command == "plan"
                else storage_quota_status(resolved, labels=labels, fstab_path=args.fstab)
            )
            report_path = Path(args.report) if args.report else _default_plan_path(
                "storage-quotas",
                "storage-quota-plan.json" if args.command == "plan" else "storage-quota-status.json"
            )
            if args.command == "plan" or args.report:
                _write_json_report(report_path, report, group=resolved["derived"]["admin_group"])
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"Storage quota {args.command}: profile={report['profile']} mounts={','.join(report['mount_labels']) or 'none'}")
            if args.command == "plan" or args.report:
                print(f"Report: {report_path}")
            if args.command == "plan":
                print(f"Risk: {STORAGE_QUOTA_ENABLE_RISK}")
            for label, mount in report.get("mounts", {}).items():
                active = "active" if mount.get("active_user_quota") and mount.get("active_group_quota") else "inactive"
                print(
                    f"{label:8s} path={mount.get('path')} mount={mount.get('mountpoint')} "
                    f"fstype={mount.get('fstype')} quota={active} can_enable={mount.get('can_enable')}"
                )
        return 0

    try:
        report_path = Path(args.plan)
        plan = json.loads(report_path.read_text())
        validate_plan_token(
            args.plan_token,
            plan,
            risk=STORAGE_QUOTA_ENABLE_RISK,
            store_root=args.token_store,
        )
        applied = enable_storage_quotas(plan, fstab_path=args.fstab, backup_root=args.backup_root)
        output_path = Path(args.report) if args.report else report_path.with_name("storage-quota-enable.json")
        _write_json_report(output_path, applied)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(applied, indent=2, sort_keys=True))
    else:
        print(f"Storage quota enable status: {applied.get('status')}")
        print(f"Report: {output_path}")
        print(f"fstab backup: {applied.get('fstab_backup')}")
        if applied.get("status") != "enabled":
            for command in applied.get("recovery_commands") or []:
                print(f"RECOVERY {command}")
            return 1
    return 0


def _default_sync_plan_path(filename: str) -> Path:
    return _default_plan_path("user-sync", filename)


def _default_plan_path(prefix: str, filename: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    return Path("/var/lib/slurm-single-node/plans") / f"{prefix}-{stamp}" / filename


def _write_json_report(path: Path, report: dict[str, Any], *, group: str = "slurm_admins") -> None:
    if os.geteuid() == 0:
        write_protected_json(path, report, group=group)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def gpu_status_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-gpu-status")
    parser.add_argument("--snapshot", default="/run/slurm-single-node/gpu-status.json")
    args = parser.parse_args(argv)
    path = Path(args.snapshot)
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            generated = dt.datetime.fromisoformat(payload.get("generated_at", ""))
            age = (dt.datetime.now(dt.timezone.utc) - generated).total_seconds()
            if age > 30:
                print(f"WARN: GPU status snapshot is stale ({age:.0f}s old).", file=sys.stderr)
        except Exception:
            print("WARN: GPU status snapshot exists but could not be parsed.", file=sys.stderr)
        print(path.read_text(), end="")
        return 0
    if shutil.which("nvidia-smi") is None:
        print("No NVIDIA GPU status is available on this CPU-only host.")
        return 0
    print("WARN: no root/service GPU snapshot found; falling back to live nvidia-smi.", file=sys.stderr)
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader",
    ]
    return subprocess.call(cmd)


def gpu_collector_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-gpu-collector")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--snapshot", default="/run/slurm-single-node/gpu-status.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        resolved = resolve_profile(args.profile, repo_root(args.repo))
        snapshot = collect_gpu_status_snapshot(resolved, snapshot_path=args.snapshot)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        print(f"GPU status snapshot written: {args.snapshot}")
        print(f"GPU count: {len(snapshot.get('gpus') or [])}")
        print(f"Slurm GPU jobs: {len(snapshot.get('slurm_jobs') or [])}")
    return 0


def gpu_recovery_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-gpu-recovery")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "enter", "exit", "status"):
        child = sub.add_parser(name)
        child.add_argument("--profile", default="gpu-bisect-quadro-p620")
        child.add_argument("--recovery-profile", default="cpu-bisect-node0")
        child.add_argument("--repo", default=None)
        child.add_argument("--fixture-prefix", default="ssn-test-")
        child.add_argument("--state", default=str(GPU_RECOVERY_STATE))
        child.add_argument("--json", action="store_true")
        if name in {"plan", "enter"}:
            child.add_argument("--plan", default=None, help="recovery plan report path")
        if name == "enter":
            child.add_argument("--plan-token", required=True)
            child.add_argument("--token-store", default=DEFAULT_TOKEN_STORE)
            child.add_argument("--drain-timeout", default="10m")
            child.add_argument("--drain-reason", default="SSN GPU CPU-only recovery")
        if name == "exit":
            child.add_argument("--drain-timeout", default="10m")
            child.add_argument("--drain-reason", default="SSN GPU recovery exit")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    try:
        resolved = resolve_profile(args.profile, root)
        recovery_resolved = resolve_profile(args.recovery_profile, root)
        if not resolved["derived"]["has_gpus"]:
            raise ValueError(f"profile {args.profile} is not a GPU profile")
        if recovery_resolved["derived"]["has_gpus"]:
            raise ValueError(f"recovery profile {args.recovery_profile} must be CPU-only")
        admin_group = resolved["derived"]["admin_group"]
        if args.command == "plan":
            report = gpu_recovery_plan(resolved, recovery_resolved, fixture_prefix=args.fixture_prefix)
            report_path = Path(args.plan) if args.plan else _default_gpu_recovery_plan_path()
            write_protected_json(report_path, report, group=admin_group)
            _print_gpu_recovery_report(report, report_path=report_path, as_json=args.json)
            return 0
        if args.command == "status":
            state_path = Path(args.state)
            state = json.loads(state_path.read_text()) if state_path.exists() else {"status": "normal"}
            verification = gpu_verification_report(resolved)
            payload = {"state": state, "gpu_verification": verification}
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"GPU recovery state: {state.get('status', 'normal')}")
                if state.get("active_profile"):
                    print(f"Active profile: {state.get('active_profile')}")
                for check in verification.get("checks", []):
                    print(f"{check['status']:4s} {check['name']}: {check['detail']}")
            return 0
        if args.command == "enter":
            return _gpu_recovery_enter(args, root, resolved, recovery_resolved, admin_group)
        if args.command == "exit":
            return _gpu_recovery_exit(args, root, resolved, admin_group)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


def _gpu_recovery_enter(
    args: argparse.Namespace,
    root: Path,
    resolved: dict[str, Any],
    recovery_resolved: dict[str, Any],
    admin_group: str,
) -> int:
    plan_path = Path(args.plan) if args.plan else _default_gpu_recovery_plan_path()
    if not plan_path.exists():
        raise ValueError(f"recovery plan not found: {plan_path}")
    plan = json.loads(plan_path.read_text())
    validate_plan_token(args.plan_token, plan, risk=GPU_RECOVERY_RISK, store_root=args.token_store, mark_used=False)
    current = gpu_recovery_plan(resolved, recovery_resolved, fixture_prefix=args.fixture_prefix)
    if current.get("operation_hash") != plan.get("operation_hash"):
        raise ValueError("current GPU recovery operation no longer matches reviewed plan; rerun plan and create a new token")
    if current.get("nonfixture_gpu_jobs"):
        raise ValueError("non-fixture GPU jobs are present; refusing fixture-only recovery")
    validate_plan_token(args.plan_token, plan, risk=GPU_RECOVERY_RISK, store_root=args.token_store)

    state_path = Path(args.state)
    recovery_id = f"gpu-recovery-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}"
    plan_dir = plan_path.parent
    drain_info: dict[str, Any] | None = None
    apply_started = False
    state = {
        "schema_version": 1,
        "status": "entering_cpu_only",
        "recovery_id": recovery_id,
        "profile": resolved["profile"],
        "recovery_profile": recovery_resolved["profile"],
        "plan": str(plan_path),
        "operation_hash": plan.get("operation_hash"),
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "fixture_prefix": args.fixture_prefix,
        "job_actions": [],
    }
    try:
        drain_info = drain_node(resolved["identity"]["node_name"], f"{args.drain_reason} ({recovery_id})")
        state["drain"] = drain_info
        _apply_recovery_job_actions(current.get("actions") or [], state)
        active = wait_for_no_active_jobs(duration_to_seconds(args.drain_timeout))
        if active:
            raise RuntimeError(f"drain timed out with active jobs still present: {active}")
        apply_started = True
        _apply_profile_via_ansible(recovery_resolved["profile"], root, plan_dir / "cpu-only-rendered")
        state["status"] = "cpu_only"
        state["active_profile"] = recovery_resolved["profile"]
        state["entered_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if drain_info.get("initiated_by_ssn"):
            resume_node(resolved["identity"]["node_name"])
            drain_info["initiated_by_ssn"] = False
        _write_gpu_recovery_state(state_path, state, group=admin_group)
    except Exception:
        state["status"] = "failed_enter"
        state["failed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if drain_info and drain_info.get("initiated_by_ssn") and not apply_started:
            try:
                resume_node(resolved["identity"]["node_name"])
                drain_info["initiated_by_ssn"] = False
            except Exception as resume_exc:
                state["resume_error"] = str(resume_exc)
        elif drain_info and drain_info.get("initiated_by_ssn"):
            state["manual_recovery"] = f"scontrol update NodeName={resolved['identity']['node_name']} State=RESUME"
        _write_gpu_recovery_state(state_path, state, group=admin_group)
        raise
    print(f"GPU recovery entered CPU-only mode using profile {recovery_resolved['profile']}")
    print(f"Recovery state: {state_path}")
    return 0


def _gpu_recovery_exit(args: argparse.Namespace, root: Path, resolved: dict[str, Any], admin_group: str) -> int:
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    recovery_id = state.get("recovery_id") or f"gpu-recovery-exit-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}"
    plan_dir = Path("/var/lib/slurm-single-node/plans") / str(recovery_id)
    drain_info: dict[str, Any] | None = None
    apply_started = False
    try:
        drain_info = drain_node(resolved["identity"]["node_name"], f"{args.drain_reason} ({recovery_id})")
        active = wait_for_no_active_jobs(duration_to_seconds(args.drain_timeout))
        if active:
            raise RuntimeError(f"drain timed out with active jobs still present: {active}")
        apply_started = True
        _apply_profile_via_ansible(resolved["profile"], root, plan_dir / "gpu-rendered")
        verification = gpu_verification_report(resolved)
        errors = gpu_verification_errors(verification)
        if errors:
            raise RuntimeError("GPU verification failed after recovery exit: " + "; ".join(errors))
        _release_held_jobs(state)
        state.update(
            {
                "schema_version": 1,
                "status": "normal",
                "profile": resolved["profile"],
                "active_profile": resolved["profile"],
                "exited_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "gpu_verification": verification,
            }
        )
        if drain_info.get("initiated_by_ssn"):
            resume_node(resolved["identity"]["node_name"])
            drain_info["initiated_by_ssn"] = False
        _write_gpu_recovery_state(state_path, state, group=admin_group)
    except Exception:
        state["status"] = "failed_exit"
        state["failed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if drain_info and drain_info.get("initiated_by_ssn") and not apply_started:
            try:
                resume_node(resolved["identity"]["node_name"])
                drain_info["initiated_by_ssn"] = False
            except Exception as resume_exc:
                state["resume_error"] = str(resume_exc)
        elif drain_info and drain_info.get("initiated_by_ssn"):
            state["manual_recovery"] = f"scontrol update NodeName={resolved['identity']['node_name']} State=RESUME"
        _write_gpu_recovery_state(state_path, state, group=admin_group)
        raise
    print(f"GPU recovery exited; restored profile {resolved['profile']}")
    print(f"Recovery state: {state_path}")
    return 0


def _apply_recovery_job_actions(actions: list[dict[str, Any]], state: dict[str, Any]) -> None:
    for action in actions:
        job_id = str(action.get("job_id"))
        if action.get("action") == "hold":
            proc = subprocess.run(["scontrol", "hold", job_id], text=True, capture_output=True)
        else:
            proc = subprocess.run(["scancel", job_id], text=True, capture_output=True)
        result = dict(action)
        result["rc"] = proc.returncode
        result["stdout"] = proc.stdout.strip()
        result["stderr"] = proc.stderr.strip()
        if proc.returncode != 0:
            raise RuntimeError(f"failed to {action.get('action')} GPU fixture job {job_id}: {proc.stderr.strip()}")
        state.setdefault("job_actions", []).append(result)


def _write_gpu_recovery_state(path: Path, state: dict[str, Any], *, group: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    secure_path(path, group=group)


def _release_held_jobs(state: dict[str, Any]) -> None:
    for action in state.get("job_actions") or []:
        if action.get("action") != "hold" or action.get("rc") != 0:
            continue
        job_id = str(action.get("job_id"))
        subprocess.run(["scontrol", "release", job_id], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _apply_profile_via_ansible(profile: str, root: Path, output_dir: Path) -> None:
    render_profile(profile, output_dir, root)
    ansible_vars = output_dir / "ansible-vars.json"
    cmd = [
        "ansible-playbook",
        "-i",
        str(root / "ansible" / "inventories" / "local.ini"),
        str(root / "ansible" / "site.yml"),
        "-e",
        f"@{ansible_vars}",
    ]
    rc = subprocess.call(cmd)
    if rc != 0:
        raise RuntimeError(f"ansible-playbook failed with rc={rc}")


def _default_gpu_recovery_plan_path() -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    if os.geteuid() == 0:
        return Path("/var/lib/slurm-single-node/plans") / f"gpu-recovery-{stamp}" / "gpu-recovery-plan.json"
    return repo_root(None) / "build" / "plans" / f"gpu-recovery-{stamp}" / "gpu-recovery-plan.json"


def _print_gpu_recovery_report(report: dict[str, Any], *, report_path: Path, as_json: bool) -> None:
    if as_json:
        payload = dict(report)
        payload["path"] = str(report_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"GPU recovery plan: {report_path}")
    print(f"Risk: {GPU_RECOVERY_RISK}")
    print(f"Operation hash: {report.get('operation_hash')}")
    print(f"Fixture GPU jobs: {len(report.get('fixture_gpu_jobs') or [])}")
    print(f"Non-fixture GPU jobs: {len(report.get('nonfixture_gpu_jobs') or [])}")
    for action in report.get("actions") or []:
        print(f"PLAN {action['action']:6s} job={action['job_id']} user={action['user']} state={action['state']}")


def login_isolation_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-login-isolation")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--users", default=DEFAULT_USERS)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--fixture-prefix", default=DEFAULT_LOGIN_FIXTURE_PREFIX)
    parser.add_argument(
        "--target-scope",
        choices=["fixture_only", "managed_allowlist", "all_managed_non_admin"],
        default="managed_allowlist",
    )
    parser.add_argument("--allow-user", action="append", default=[])
    parser.add_argument("--allow-prefix", action="append", default=None)
    parser.add_argument("--mode", choices=["cgroup", "acl", "limits", "disabled"], default="cgroup")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", default="/etc/slurm-single-node/login-isolation.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        resolved = resolve_profile(args.profile, repo_root(args.repo))
        users_doc = load_users(args.users)
        state_doc = load_state(args.state)
        allow_prefixes = args.allow_prefix if args.allow_prefix is not None else ([] if args.allow_user else [DEFAULT_LOGIN_FIXTURE_PREFIX])
        errors = [*validate_state(state_doc), *validate_users(users_doc, resolved, state_doc=state_doc)]
        if errors:
            raise ValueError("; ".join(errors))
        report = (
            apply_login_isolation(
                users_doc,
                state_doc,
                resolved,
                fixture_prefix=args.fixture_prefix,
                target_scope=args.target_scope,
                allow_users=args.allow_user,
                allow_prefixes=allow_prefixes,
                mode=args.mode,
                report_path=args.report,
            )
            if args.apply
            else login_isolation_report(
                users_doc,
                state_doc,
                resolved,
                fixture_prefix=args.fixture_prefix,
                target_scope=args.target_scope,
                allow_users=args.allow_user,
                allow_prefixes=allow_prefixes,
                mode=args.mode,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        action = "Applied" if args.apply else "Planned"
        print(
            f"{action} login isolation mode={args.mode} "
            f"target_scope={args.target_scope} targets={len(report.get('targets') or [])}"
        )
        if args.apply:
            print(f"Report: {args.report}")
        for target in report.get("targets", []):
            print(f"TARGET {target.get('user')} uid={target.get('uid', '-')} {target.get('status')}")
    return 0


def login_status_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-login-status")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--users", default=DEFAULT_USERS)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--fixture-prefix", default=DEFAULT_LOGIN_FIXTURE_PREFIX)
    parser.add_argument(
        "--target-scope",
        choices=["fixture_only", "managed_allowlist", "all_managed_non_admin"],
        default=DEFAULT_LOGIN_TARGET_SCOPE,
    )
    parser.add_argument("--allow-user", action="append", default=[])
    parser.add_argument("--allow-prefix", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        resolved = resolve_profile(args.profile, repo_root(args.repo))
        users_doc = load_users(args.users)
        state_doc = load_state(args.state)
        errors = [*validate_state(state_doc), *validate_users(users_doc, resolved, state_doc=state_doc)]
        if errors:
            raise ValueError("; ".join(errors))
        report = login_isolation_status(
            users_doc,
            state_doc,
            resolved,
            fixture_prefix=args.fixture_prefix,
            target_scope=args.target_scope,
            allow_users=args.allow_user,
            allow_prefixes=args.allow_prefix,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Login isolation mode: {report.get('mode')} target_scope={report.get('target_scope')}")
        snapshot = report.get("snapshot") or {}
        print(
            "GPU snapshot: "
            f"exists={snapshot.get('exists')} fresh={snapshot.get('fresh')} "
            f"gpus={snapshot.get('gpu_count', '-')}"
        )
        for status in report.get("status", []):
            props = status.get("properties") or {}
            print(
                f"USER {status['user']} {status['unit']} "
                f"dropin={status.get('dropin_exists')} "
                f"CPUQuota={props.get('CPUQuotaPerSecUSec', '-')} "
                f"MemoryMax={props.get('MemoryMax', '-')} "
                f"TasksMax={props.get('TasksMax', '-')} "
                f"DevicePolicy={props.get('DevicePolicy', '-')}"
            )
    return 0


def modules_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-modules")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "verify"):
        child = sub.add_parser(name)
        child.add_argument("--profile", default="cpu-dev-local")
        child.add_argument("--repo", default=None)
        child.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        resolved = resolve_profile(args.profile, repo_root(args.repo))
        report = modules_status_report(resolved) if args.command == "status" else modules_verify_report(resolved)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "status":
        cuda = report.get("cuda") or {}
        miniconda = report.get("miniconda") or {}
        lmod = report.get("lmod") or {}
        print(f"Modules status: profile={report.get('profile')}")
        print(f"Lmod init: {lmod.get('init_bash') or 'missing'}")
        print(f"CUDA: {cuda.get('status')} ({cuda.get('reason')})")
        for toolkit in cuda.get("toolkits") or []:
            print(
                f"CUDA_TOOLKIT root={toolkit.get('root')} "
                f"version={toolkit.get('version') or '-'} "
                f"nvcc={toolkit.get('has_nvcc')}"
            )
        print(f"Miniconda: {miniconda.get('status')} ({miniconda.get('reason')})")
        for modulefile in report.get("modulefiles") or []:
            print(f"MODULE {modulefile.get('name')} -> {modulefile.get('path')}")
    else:
        print(f"Modules verify: profile={report.get('profile')} healthy={report.get('healthy')}")
        for check in report.get("checks") or []:
            print(f"{check['status']:4s} {check['name']}: {check['detail']}")
    if args.command == "verify":
        return 1 if modules_verify_errors(report) else 0
    return 0


def archive_status_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-archive-status")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    state = load_state(args.state)
    users = state.get("users") or {}
    rows = []
    for username, entry in sorted(users.items()):
        archive_state = entry.get("archive_state")
        if archive_state:
            rows.append(
                {
                    "username": username,
                    "archive_state": archive_state,
                    "archive_path": entry.get("archive_path"),
                    "local_only": entry.get("archive_local_only"),
                    "backup_required": entry.get("archive_backup_required"),
                    "backup_status": entry.get("archive_backup_status"),
                    "backup_hook": entry.get("archive_backup_hook"),
                    "backup_rc": entry.get("archive_backup_rc"),
                    "last_error": entry.get("archive_last_error"),
                    "next_action": _archive_next_action(archive_state),
                }
            )
    if args.json:
        print(json.dumps({"archives": rows}, indent=2, sort_keys=True))
        return 0
    found = False
    for row in rows:
        found = True
        print(
            f"{row['username']:20s} {row['archive_state']:20s} "
            f"local_only={row['local_only']} backup={row.get('backup_status')} next={row['next_action']}"
        )
        if row.get("archive_path"):
            print(f"{'':20s} archive={row['archive_path']}")
        if row.get("backup_hook"):
            print(f"{'':20s} hook={row['backup_hook']} rc={row.get('backup_rc')}")
        if row.get("last_error"):
            print(f"{'':20s} error={row['last_error']}")
    if not found:
        print("No inactive archive workflows recorded.")
    return 0


def _archive_next_action(archive_state: str) -> str:
    if archive_state == "archive_pending":
        return "run_archive"
    if archive_state == "archive_running":
        return "inspect_or_resume"
    if archive_state in {"archived_local_only", "backup_complete"}:
        return "mark_removal_ready"
    if archive_state == "removal_ready":
        return "remove_account"
    if archive_state == "tombstoned":
        return "complete"
    if archive_state == "backup_failed":
        return "retry_backup_or_local_override"
    return "unknown"


def scratch_health_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-scratch-health")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--users", default=DEFAULT_USERS)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--report", default="/run/slurm-single-node/scratch-health.json")
    parser.add_argument("--marker", default="/run/slurm-single-node/scratch-unhealthy")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    try:
        resolved = resolve_profile(args.profile, root)
        users_doc = load_users(args.users)
        state_doc = load_state(args.state)
        errors = [*validate_state(state_doc), *validate_users(users_doc, resolved, state_doc=state_doc)]
        if errors:
            raise ValueError("; ".join(errors))
        report = scratch_health_report(users_doc, resolved)
        write_scratch_health_state(report, report_path=args.report, marker_path=args.marker)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = "healthy" if report.get("healthy") else "unhealthy"
        print(f"Scratch health: {status}")
        print(f"Report: {args.report}")
        print(f"Marker: {args.marker}")
        for check in report.get("checks", []):
            print(f"{check['status']:4s} {check['name']}: {check['detail']}")
    return 0 if report.get("healthy") else 1


def scratch_cleanup_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-scratch-cleanup")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--root", default="/scratch")
    parser.add_argument("--jobs-root", default="/scratch/jobs")
    parser.add_argument("--age-days", type=int, default=30)
    parser.add_argument("--report", default="/var/log/slurm/scratch-cleanup.json")
    parser.add_argument("--apply", action="store_true", help="delete eligible paths")
    parser.add_argument("--yes-delete", action="store_true", help="required with --apply")
    parser.add_argument("--plan-token", default=None, help="reviewed token required with --apply")
    parser.add_argument("--token-store", default="/var/lib/slurm-single-node/plan-tokens")
    parser.add_argument("--fixture-prefix", default=DEFAULT_FIXTURE_PREFIX)
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
    if args.apply and not args.plan_token:
        print("ERROR: --apply requires --plan-token", file=sys.stderr)
        return 2
    if not root.exists():
        print("Scratch root is absent; nothing to report.")
        return 0
    report_path = Path(args.report)
    if args.apply:
        try:
            report = json.loads(report_path.read_text())
            validate_plan_token(
                args.plan_token,
                report,
                risk="fixture_scratch_cleanup",
                store_root=args.token_store,
            )
            applied = apply_fixture_scratch_cleanup(report, fixture_prefix=args.fixture_prefix)
            report_path.write_text(json.dumps(applied, indent=2, sort_keys=True) + "\n")
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"Scratch cleanup applied from reviewed report: {report_path}")
        for item in applied.get("deletion_results", []):
            print(f"CLEANUP {item['status']:8s} {item['path']} {item.get('reason', '')}")
        return 0

    resolved = None
    if args.profile:
        try:
            resolved = resolve_profile(args.profile, repo_root(args.repo))
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    report = scratch_cleanup_report(
        root=root,
        jobs_root=jobs_root,
        age_days=args.age_days,
        profile=args.profile,
        resolved=resolved,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Scratch cleanup report written: {report_path}")
    print(f"Eligible top-level scratch paths: {report['candidate_count']}")
    return 0


def retention_cleanup_cmd(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ssn-retention-cleanup")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--root", required=True)
    parser.add_argument("--older-than-days", type=int, default=90)
    parser.add_argument("--report", required=True)
    parser.add_argument("--apply", action="store_true", help="delete eligible SSN test artifacts")
    parser.add_argument("--yes-delete", action="store_true", help="required with --apply")
    parser.add_argument("--plan-token", default=None, help="reviewed token required with --apply")
    parser.add_argument("--token-store", default=DEFAULT_TOKEN_STORE)
    parser.add_argument("--fixture-prefix", default=DEFAULT_FIXTURE_PREFIX)
    args = parser.parse_args(argv)

    if args.apply and not args.yes_delete:
        print("ERROR: --apply requires --yes-delete", file=sys.stderr)
        return 2
    if args.apply and not args.plan_token:
        print("ERROR: --apply requires --plan-token", file=sys.stderr)
        return 2

    report_path = Path(args.report)
    if args.apply:
        try:
            report = json.loads(report_path.read_text())
            validate_plan_token(
                args.plan_token,
                report,
                risk=RETENTION_DELETE_RISK,
                store_root=args.token_store,
            )
            applied = apply_test_retention_cleanup(report, fixture_prefix=args.fixture_prefix)
            report_path.write_text(json.dumps(applied, indent=2, sort_keys=True) + "\n")
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"Retention cleanup applied from reviewed report: {report_path}")
        for item in applied.get("deletion_results", []):
            print(f"RETENTION {item['status']:8s} {item['path']} {item.get('reason', '')}")
        return 0

    profile = None
    config_hash_value = None
    if args.profile:
        try:
            resolved = resolve_profile(args.profile, repo_root(args.repo))
            profile = resolved["profile"]
            config_hash_value = config_hash(resolved)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    report = retention_cleanup_report(
        args.root,
        older_than_days=args.older_than_days,
        profile=profile,
        config_hash_value=config_hash_value,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Retention cleanup report written: {report_path}")
    print(f"Risk: {RETENTION_DELETE_RISK}")
    print(f"Eligible old items: {report['candidate_count']}")
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
        report = gpu_verification_report(resolved)
        for check in report.get("checks", []):
            checks.append({"name": f"gpu_{check['name']}", "status": check["status"], "detail": check["detail"]})
    modules_policy = resolved.get("resolved_policies", {}).get("modules") or {}
    if modules_policy.get("lmod"):
        report = modules_verify_report(resolved)
        for check in report.get("checks", []):
            checks.append({"name": f"modules_{check['name']}", "status": check["status"], "detail": check["detail"]})
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
