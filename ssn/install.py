from __future__ import annotations

import argparse
import datetime as dt
import grp
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import config_hash, render_profile, repo_root, resolve_profile, summary_text
from .ops import (
    collect_capabilities,
    drain_node,
    queued_jobs,
    resume_node,
    validate_feature_gates,
    validate_installed_slurm_features,
    validate_plan_token,
    validate_resolved_slurm_features,
    wait_for_no_active_jobs,
)
from .safety import redact_for_plan, retention_candidates
from .units import duration_to_seconds


BOOTSTRAP_PACKAGES = [
    "ansible-core",
    "python3-apt",
    "python3-yaml",
]

RUNTIME_PACKAGES = [
    "slurm-wlm",
    "slurmd",
    "slurmctld",
    "slurmdbd",
    "slurm-wlm-doc",
    "munge",
    "libmunge2",
    "mariadb-server",
    "mariadb-client",
    "hwloc",
    "numactl",
    "cgroup-tools",
    "quota",
    "python3",
    "acl",
    "lua5.3",
    "liblua5.3-dev",
    "lmod",
]

GPU_PACKAGES = [
    "libpam-slurm-adopt",
]

BACKUP_PATHS = [
    "/etc/slurm",
    "/etc/slurm-single-node",
    "/etc/systemd/system/slurmctld.service.d",
    "/etc/systemd/system/slurmd.service.d",
    "/etc/systemd/system/slurmdbd.service.d",
    "/var/lib/slurm-single-node",
    "/etc/munge/munge.key",
]

SERVICE_NAMES = ["munge", "mariadb", "slurmdbd", "slurmctld", "slurmd"]


class Installer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = repo_root(args.repo)
        self.stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        self.install_id = f"install-{self.stamp}"
        self.log_file = self._log_path()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.admin_group = "slurm_admins"
        self.drain_info: dict[str, Any] | None = None
        self.service_change_started = False
        self.report: dict[str, Any] = {
            "schema_version": 1,
            "command": "install",
            "install_id": self.install_id,
            "profile": args.profile,
            "repo": str(self.root),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "phases": [],
            "status": "running",
            "log_file": str(self.log_file),
        }
        self.report_file: Path | None = None

    def run(self) -> int:
        try:
            self._reexec_with_sudo_if_needed()
            if self.args.force and self.args.drain:
                raise RuntimeError("--force and --drain are mutually exclusive install safety modes")
            self._banner()
            self.report_file = self._plan_dir() / "install-report.json"
            resolved = self._phase_preflight()
            self._record_plan_retention_report()
            packages = self._select_packages(resolved)
            missing_packages = [pkg for pkg in packages if not self._package_installed(pkg)]
            self._record_phase("preflight", "ok", {"missing_packages": missing_packages})

            if missing_packages:
                self._prompt(
                    "Install missing OS packages with apt: "
                    + ", ".join(missing_packages)
                )
                if not self.args.dry_run:
                    self.service_change_started = True
                    self._run(["apt-get", "update"])
                    self._run(["apt-get", "install", "-y", *missing_packages])
            else:
                self._log("All required OS packages already appear installed.")
            self._validate_feature_gates(resolved)

            output_dir = self._render_dir()
            self._log(f"Rendering profile artifacts into {output_dir}")
            resolved = render_profile(self.args.profile, output_dir, self.root)
            rendered_errors = validate_resolved_slurm_features(resolved)
            if rendered_errors:
                raise RuntimeError("rendered Slurm feature validation failed: " + "; ".join(rendered_errors))
            self._record_phase("rendered_feature_validation", "ok", {})
            self._secure_plan_tree(resolved)
            self._log(summary_text(resolved).rstrip())
            self._log(f"Config hash: {config_hash(resolved)}")
            self.report["config_hash"] = config_hash(resolved)
            self.report["rendered_dir"] = str(output_dir)
            self._record_phase("render", "ok", {"output_dir": str(output_dir)})
            self._write_report()

            self._backup_existing_managed_files()
            self._prompt_for_missing_directories(resolved)

            ansible_cmd = [
                "ansible-playbook",
                "-i",
                str(self.root / "ansible" / "inventories" / "local.ini"),
                str(self.root / "ansible" / "site.yml"),
                "-e",
                f"@{output_dir / 'ansible-vars.json'}",
            ]
            self._run_ansible_syntax_check(output_dir)
            if self.args.check:
                ansible_cmd.append("--check")
            self._log("Ansible command: " + " ".join(ansible_cmd))
            if not self.args.dry_run:
                self.service_change_started = True
                self._run(ansible_cmd)
                installed_errors = validate_installed_slurm_features(resolved)
                if installed_errors:
                    raise RuntimeError("installed Slurm feature validation failed: " + "; ".join(installed_errors))
                self._record_phase("installed_feature_validation", "ok", {})
                self._record_phase("apply", "ok", {})

            if self.args.dry_run:
                self._log("Dry run completed; skipping Ansible apply and smoke tests.")
            elif self.args.check:
                self._log("Check mode completed; skipping smoke tests.")
            elif not self.args.skip_smoke:
                self._phase_verify_and_smoke(resolved, output_dir)
                self._record_phase("verify", "ok", {})

            self.report["status"] = "ok"
            self.report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            self._resume_drained_node_after_success(resolved)
            self._write_report()
            self._write_report_summary()
            self._log(f"Install log: {self.log_file}")
            if self.report_file:
                self._log(f"Install report: {self.report_file}")
            self._log(
                "Retry command: sudo ./bin/ssn-install "
                f"--profile {self.args.profile}"
            )
            return 0
        except Exception as exc:
            self._handle_failed_drain_recovery()
            self.report["status"] = "failed"
            self.report["error"] = str(exc)
            self.report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            self._write_report()
            self._write_report_summary()
            raise

    def _phase_preflight(self) -> dict[str, Any]:
        self._log("== preflight ==")
        resolved = resolve_profile(self.args.profile, self.root)
        self._secure_plan_tree(resolved)
        self.report["config_hash"] = config_hash(resolved)
        self._log(summary_text(resolved).rstrip())
        self._log(f"Repo: {self.root}")
        self._log(f"User: euid={os.geteuid()} sudo_user={os.environ.get('SUDO_USER') or ''}")
        capabilities = collect_capabilities(resolved, mode="install")
        self.report["capabilities"] = capabilities
        self._log(f"OS: {capabilities['os']['pretty_name']}")
        self._log(f"Kernel: {capabilities['os']['kernel']}")
        self._log(f"cgroup fs: {capabilities['cgroup_fs']}")
        if self._command_stdout(["stat", "-fc", "%T", "/sys/fs/cgroup"]) != "cgroup2fs":
            raise RuntimeError("cgroup v2 is required")
        for path, detail in capabilities["mounts"].items():
            mount = detail.get("findmnt") or "not mounted"
            free = detail.get("free_mb")
            free_text = "" if free is None else f", free={free}MB"
            self._log(f"mount {path}: {mount}{free_text}")
        for command, command_path in capabilities["commands"].items():
            self._log(f"command {command}: {command_path or 'missing'}")
        for package, version in capabilities["packages"].items():
            self._log(f"package {package}: {version or 'not installed'}")
        for runtime, version in capabilities["runtime_versions"].items():
            self._log(f"{runtime}: {version or 'unavailable'}")
        for service in SERVICE_NAMES:
            self._log(
                f"service {service}: active={self._systemctl('is-active', service)} "
                f"enabled={self._systemctl('is-enabled', service)}"
            )
        self._log("Planned managed writes: /etc/slurm, /etc/slurm-single-node, "
                  "/var/lib/slurm-single-node, /var/backups/slurm-single-node, "
                  "/var/spool/slurm, /run/slurm, /var/log/slurm, /tools, configured data/archive roots")
        if resolved["derived"]["has_gpus"]:
            self._log("GPU profile selected; NVIDIA driver/toolkit is validate-only in v1.")
        else:
            self._log("CPU-only profile selected; no GPU GRES/TRES will be rendered.")
        self._validate_profile_matches_host(resolved)
        self._validate_required_mounts(resolved)
        self._validate_no_jobs_before_apply(resolved)
        return resolved

    def _validate_no_jobs_before_apply(self, resolved: dict[str, Any]) -> None:
        if self.args.dry_run or self.args.check:
            return
        if shutil.which("squeue") is None:
            self._log("squeue is unavailable; running-job apply gate skipped on this not-yet-installed host.")
            return
        jobs = queued_jobs()
        if self.args.drain:
            timeout = duration_to_seconds(self.args.drain_timeout)
            reason = f"{self.args.drain_reason} ({self.install_id})"
            self.drain_info = drain_node(resolved["identity"]["node_name"], reason)
            self.report["drain"] = self.drain_info
            self._record_phase("node_drain", "ok", self.drain_info)
            active = wait_for_no_active_jobs(timeout)
            if active:
                self.report["blocked_jobs"] = active
                if self.drain_info.get("initiated_by_ssn"):
                    resume_node(resolved["identity"]["node_name"])
                    self.drain_info["initiated_by_ssn"] = False
                    self._record_phase("node_resume_after_drain_timeout", "ok", {})
                raise RuntimeError(f"drain timed out with active jobs still present: {active}")
            self._record_phase("drain_wait", "ok", {})
            self._log("Drain workflow: node is drained and no active Slurm jobs remain.")
            return
        if not jobs:
            self._log("Running-job apply gate: no queued Slurm jobs found.")
            return
        self.report["blocked_jobs"] = jobs
        self.report["risk"] = "queued_jobs"
        if self.args.force:
            if not self.args.plan_token:
                raise RuntimeError("queued jobs require --force plus --plan-token")
            validate_plan_token(self.args.plan_token, self.report, risk="queued_jobs")
            self._log("FORCE: queued-job apply gate authorized by reviewed plan token.")
            return
        preview = "; ".join(jobs[:5])
        raise RuntimeError(
            "refusing service-changing apply while Slurm has running/pending jobs: "
            f"{preview}. Create a reviewed plan token, then re-run with --force --plan-token."
        )

    def _validate_feature_gates(self, resolved: dict[str, Any]) -> None:
        if self.args.dry_run:
            return
        capabilities = collect_capabilities(resolved, mode="install")
        self.report["capabilities"] = capabilities
        errors = validate_feature_gates(resolved, mode="install", capabilities=capabilities)
        if errors:
            raise RuntimeError("feature gate failed: " + "; ".join(errors))
        self._record_phase("feature_gates", "ok", {"mode": "install"})
        self._log("Feature gates passed for install.")

    def _resume_drained_node_after_success(self, resolved: dict[str, Any]) -> None:
        if self.drain_info and self.drain_info.get("initiated_by_ssn") and not self.args.dry_run and not self.args.check:
            resume_node(resolved["identity"]["node_name"])
            self.drain_info["initiated_by_ssn"] = False
            self._record_phase("node_resume", "ok", {})
            self._log("Drain workflow: node resumed after successful install.")

    def _handle_failed_drain_recovery(self) -> None:
        if not self.drain_info or not self.drain_info.get("initiated_by_ssn"):
            return
        node_name = str(self.drain_info.get("node"))
        if not self.service_change_started:
            try:
                resume_node(node_name)
                self.drain_info["initiated_by_ssn"] = False
                self._record_phase("node_resume_after_preapply_failure", "ok", {})
                return
            except Exception as exc:
                self.report["resume_error"] = str(exc)
        self.report["manual_recovery"] = f"scontrol update NodeName={node_name} State=RESUME"

    def _validate_profile_matches_host(self, resolved: dict[str, Any]) -> None:
        host = self._command_stdout(["hostname"]) or ""
        node_name = resolved["identity"]["node_name"]
        if host != node_name:
            raise RuntimeError(
                f"profile node_name={node_name!r} does not match host={host!r}; "
                "select or create a profile for this host before applying"
            )
        actual_cpus = os.cpu_count() or 0
        profile_cpus = int(resolved["hardware"]["cpus_total"])
        alloc_cpus = int(resolved["derived"]["cpus_allocatable"])
        if profile_cpus > actual_cpus or alloc_cpus > actual_cpus:
            raise RuntimeError(
                f"profile CPU values exceed host CPUs: profile total={profile_cpus}, "
                f"allocatable={alloc_cpus}, host={actual_cpus}"
            )
        actual_mem = self._mem_total_mb()
        profile_mem = int(resolved["derived"]["memory_total_mb"])
        alloc_mem = int(resolved["derived"]["memory_allocatable_mb"])
        if actual_mem and (profile_mem > actual_mem + 512 or alloc_mem > actual_mem):
            raise RuntimeError(
                f"profile memory values exceed host memory: profile total={profile_mem}MB, "
                f"allocatable={alloc_mem}MB, host={actual_mem}MB"
            )
        if resolved["derived"]["has_gpus"]:
            expected = int(resolved["hardware"]["gpus"])
            gpu_lines = self._command_stdout([
                "nvidia-smi",
                "--query-gpu=index,name,uuid",
                "--format=csv,noheader",
            ])
            if gpu_lines is None:
                raise RuntimeError("GPU profile selected, but nvidia-smi is unavailable or failing")
            actual = len([line for line in gpu_lines.splitlines() if line.strip()])
            if actual != expected:
                raise RuntimeError(f"profile GPU count={expected} does not match nvidia-smi count={actual}")
            for index in range(expected):
                device = Path(f"/dev/nvidia{index}")
                if not device.exists():
                    raise RuntimeError(f"profile expects GPU device {device}, but it is missing")

    def _validate_required_mounts(self, resolved: dict[str, Any]) -> None:
        storage = resolved["resolved_policies"]["storage"]
        paths = resolved["derived"].get("paths") or {}
        require_mounts = bool(storage.get("quotas", {}).get("fail_if_unavailable"))
        require_mounts = require_mounts or bool(storage.get("job_scratch", {}).get("required_for_jobs"))
        if not require_mounts:
            return
        missing = []
        for key in ("data", "scratch"):
            path = paths.get(key)
            if path and not self._findmnt(path):
                missing.append(f"{key}={path}")
        if missing:
            raise RuntimeError("profile requires mounted storage paths, but these are not mounted: " + ", ".join(missing))

    def _record_plan_retention_report(self) -> None:
        root = Path("/var/lib/slurm-single-node/plans")
        candidates = retention_candidates(root, older_than_days=90)
        detail = {
            "root": str(root),
            "retention_days": 90,
            "mode": "report_only",
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
        self.report["plan_retention"] = detail
        self._record_phase("plan_retention", "ok", detail)
        self._log(
            f"Plan retention report-only: {len(candidates)} item(s) older than 90 days under {root}"
        )

    def _select_packages(self, resolved: dict[str, Any]) -> list[str]:
        packages = [*BOOTSTRAP_PACKAGES, *RUNTIME_PACKAGES]
        archive_pkg = self._first_available_package(["7zip", "p7zip-full"])
        if archive_pkg:
            packages.append(archive_pkg)
        else:
            self._log("WARN archive package 7zip/p7zip-full is unavailable; core Slurm install will continue.")
        if resolved["derived"]["has_gpus"]:
            packages.extend(pkg for pkg in GPU_PACKAGES if self._package_has_candidate(pkg))
        return sorted(dict.fromkeys(packages))

    def _backup_existing_managed_files(self) -> None:
        existing = [Path(path) for path in BACKUP_PATHS if self._path_exists(path)]
        if not existing:
            self._log("No existing managed files found for preinstall backup.")
            return
        backup_root = Path("/var/backups/slurm-single-node/preinstall") / self.stamp
        self._prompt(
            "Back up existing managed files before replacement to "
            f"{backup_root}: " + ", ".join(str(path) for path in existing)
        )
        if self.args.dry_run:
            return
        backup_root.mkdir(parents=True, exist_ok=True)
        for path in existing:
            dest = backup_root / path.relative_to("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir():
                shutil.copytree(path, dest, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(path, dest, follow_symlinks=False)
            self._log(f"Backed up {path} -> {dest}")

    def _prompt_for_missing_directories(self, resolved: dict[str, Any]) -> None:
        candidates = [
            "/etc/slurm",
            "/etc/slurm-single-node",
            "/var/lib/slurm-single-node",
            "/var/lib/slurm-single-node/plans",
            "/var/backups/slurm-single-node/users",
            "/var/spool/slurm",
            "/var/log/slurm",
            "/run/slurm",
            "/tools/apps",
            "/tools/modules",
            "/tools/containers",
        ]
        paths = resolved["derived"].get("paths") or {}
        for key in ("data", "archive", "scratch"):
            value = paths.get(key)
            if value:
                candidates.append(str(value))
        missing = [path for path in dict.fromkeys(candidates) if not Path(path).exists()]
        if missing:
            self._prompt("Create missing install directories: " + ", ".join(missing))
        else:
            self._log("All expected install directories already exist or are disabled.")

    def _phase_verify_and_smoke(self, resolved: dict[str, Any], output_dir: Path) -> None:
        self._log("== verify ==")
        verify_cmd = [
            "ansible-playbook",
            "-i",
            str(self.root / "ansible" / "inventories" / "local.ini"),
            str(self.root / "ansible" / "verify.yml"),
            "-e",
            f"@{output_dir / 'ansible-vars.json'}",
        ]
        self._run(verify_cmd)
        self._run(["sinfo", "-o", "%N %P %t %G"])
        if resolved["derived"]["has_gpus"]:
            self._run(["scontrol", "show", "node", resolved["identity"]["node_name"]], check=False)
            self._run(["nvidia-smi", "--query-gpu=index,name,uuid", "--format=csv,noheader"])
        self._run(["sacctmgr", "-nP", "show", "qos", "format=name,priority,maxtresperjob,maxjobsperuser,maxsubmitjobsperuser"])
        self._run_smoke_jobs(resolved)

    def _run_smoke_jobs(self, resolved: dict[str, Any]) -> None:
        smoke_user = self.args.smoke_user or os.environ.get("SUDO_USER")
        if not smoke_user or smoke_user == "root":
            self._log("No non-root smoke user found; skipping sbatch smoke tests.")
            return
        if not self._user_exists(smoke_user):
            self._log(f"Smoke user {smoke_user!r} does not exist; skipping sbatch smoke tests.")
            return
        tier = resolved["derived"]["rendered_tiers"][0]
        account = resolved["derived"]["slurm_account"]
        self._prompt(
            f"Create/update Slurm accounting association for smoke user {smoke_user} "
            f"with account {account} and QOS {tier['qos']}"
        )
        self._run(["sacctmgr", "-i", "add", "account", account], check=False)
        self._run(
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
        self._run(
            [
                "sacctmgr",
                "-i",
                "add",
                "user",
                smoke_user,
                f"Account={account}",
                f"DefaultAccount={account}",
                f"DefaultQOS={tier['qos']}",
            ],
            check=False,
        )
        self._run(
            [
                "sacctmgr",
                "-i",
                "modify",
                "user",
                smoke_user,
                "set",
                f"DefaultAccount={account}",
                f"DefaultQOS={tier['qos']}",
            ],
            check=False,
        )
        smoke_dir = Path("/tmp") / f"ssn-smoke-{self.stamp}"
        smoke_dir.mkdir(mode=0o755, exist_ok=True)
        entry = self._pwd_entry(smoke_user)
        if entry:
            os.chown(smoke_dir, entry.pw_uid, entry.pw_gid)
        submit = [
            "sbatch",
            "--parsable",
            f"--qos={tier['qos']}",
            "--cpus-per-task=1",
            "--mem=128M",
            "--time=00:02:00",
            f"--output={smoke_dir}/ok-%j.out",
            "--wrap=hostname; sleep 1",
        ]
        job = self._run_as_user(smoke_user, submit).strip().splitlines()[-1]
        job_id = job.split(";")[0]
        self._log(f"Smoke job submitted as {smoke_user}: {job_id}")
        self._wait_for_job_done(job_id)
        self._expect_submit_failure(
            smoke_user,
            [
                "sbatch",
                "--parsable",
                f"--qos={tier['qos']}",
                f"--cpus-per-task={int(tier['max_cpus_per_job']) + 1}",
                "--mem=128M",
                "--time=00:02:00",
                "--wrap=true",
            ],
            "over-tier CPU request",
        )
        if resolved["derived"]["has_gpus"]:
            self._run_gpu_smoke(smoke_user, tier, smoke_dir)
        else:
            self._expect_submit_failure(
                smoke_user,
                [
                    "sbatch",
                    "--parsable",
                    f"--qos={tier['qos']}",
                    "--gres=gpu:1",
                    "--time=00:02:00",
                    "--wrap=true",
                ],
                "GPU request on CPU-only profile",
            )
        self._expect_submit_failure(
            smoke_user,
            [
                "sbatch",
                "--parsable",
                f"--qos={tier['qos']}",
                "--no-requeue",
                "--time=00:02:00",
                "--wrap=true",
            ],
            "--no-requeue request",
        )

    def _run_gpu_smoke(self, smoke_user: str, tier: dict[str, Any], smoke_dir: Path) -> None:
        submit = [
            "sbatch",
            "--parsable",
            f"--qos={tier['qos']}",
            "--gres=gpu:1",
            "--cpus-per-task=1",
            "--mem=128M",
            "--time=00:02:00",
            f"--output={smoke_dir}/gpu-%j.out",
            "--wrap=nvidia-smi --query-gpu=index,name --format=csv,noheader",
        ]
        job = self._run_as_user(smoke_user, submit).strip().splitlines()[-1]
        job_id = job.split(";")[0]
        self._log(f"GPU smoke job submitted as {smoke_user}: {job_id}")
        self._wait_for_job_done(job_id)
        self._expect_submit_failure(
            smoke_user,
            [
                "sbatch",
                "--parsable",
                f"--qos={tier['qos']}",
                "--gres=gpu:2",
                "--time=00:02:00",
                "--wrap=true",
            ],
            "over-GPU request",
        )

    def _wait_for_job_done(self, job_id: str) -> None:
        deadline = time.time() + 120
        while time.time() < deadline:
            out = self._command_stdout(["squeue", "-h", "-j", job_id]) or ""
            if not out.strip():
                self._run(["sacct", "-j", job_id, "--format=JobID,State,ExitCode", "-n", "-P"], check=False)
                return
            time.sleep(2)
        raise RuntimeError(f"smoke job {job_id} did not finish within 120 seconds")

    def _expect_submit_failure(self, user: str, cmd: list[str], label: str) -> None:
        proc = subprocess.run(
            ["runuser", "-u", user, "--", *cmd],
            text=True,
            capture_output=True,
            cwd=str(self.root),
        )
        output = (proc.stdout + proc.stderr).strip()
        self._write_log(f"$ runuser -u {user} -- {' '.join(cmd)}\n{output}\n")
        if proc.returncode == 0:
            raise RuntimeError(f"expected submit failure for {label}, but command succeeded")
        self._log(f"Expected rejection passed: {label}")

    def _run_as_user(self, user: str, cmd: list[str]) -> str:
        proc = subprocess.run(
            ["runuser", "-u", user, "--", *cmd],
            text=True,
            capture_output=True,
            cwd=str(self.root),
        )
        output = proc.stdout + proc.stderr
        self._write_log(f"$ runuser -u {user} -- {' '.join(cmd)}\n{output}\n")
        if proc.returncode != 0:
            raise RuntimeError(f"command failed as {user}: {' '.join(cmd)}")
        print(output, end="")
        return output

    def _run(self, cmd: list[str], *, check: bool = True) -> str:
        self._log("$ " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self.root),
        )
        output_parts: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            output_parts.append(line)
            self._write_log(line)
        rc = proc.wait()
        if check and rc != 0:
            raise RuntimeError(f"command failed with rc={rc}: {' '.join(cmd)}")
        return "".join(output_parts)

    def _run_ansible_syntax_check(self, output_dir: Path) -> None:
        if shutil.which("ansible-playbook") is None:
            self._log("ansible-playbook is not installed yet; skipping syntax check before bootstrap apply.")
            return
        cmd = [
            "ansible-playbook",
            "-i",
            str(self.root / "ansible" / "inventories" / "local.ini"),
            str(self.root / "ansible" / "site.yml"),
            "-e",
            f"@{output_dir / 'ansible-vars.json'}",
            "--syntax-check",
        ]
        self._run(cmd)
        self._record_phase("ansible_syntax", "ok", {})

    def _prompt(self, message: str) -> None:
        if self.args.yes:
            self._log(f"AUTO-YES: {message}")
            return
        if self.args.dry_run:
            self._log(f"DRY-RUN prompt: {message}")
            return
        print()
        print(message)
        answer = input("Continue? [y/N] ").strip().lower()
        self._write_log(f"PROMPT: {message}\nANSWER: {answer}\n")
        if answer not in {"y", "yes"}:
            raise RuntimeError("stopped by user")

    def _reexec_with_sudo_if_needed(self) -> None:
        if self.args.dry_run or os.geteuid() == 0:
            return
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError("this installer must run as root and sudo was not found")
        pythonpath = str(self.root)
        if os.environ.get("PYTHONPATH"):
            pythonpath = f"{pythonpath}:{os.environ['PYTHONPATH']}"
        os.execvp(
            sudo,
            [sudo, "env", f"PYTHONPATH={pythonpath}", sys.executable, "-m", "ssn.install", *sys.argv[1:]],
        )

    def _render_dir(self) -> Path:
        if self.args.output_dir:
            return Path(self.args.output_dir).resolve()
        if self.args.dry_run:
            return Path("/tmp") / f"ssn-install-render-{self.args.profile}-{self.stamp}"
        return self._plan_dir() / "rendered"

    def _plan_dir(self) -> Path:
        if self.args.output_dir:
            return Path(self.args.output_dir).resolve().parent
        if self.args.dry_run or os.geteuid() != 0:
            return Path("/tmp") / self.install_id
        return Path("/var/lib/slurm-single-node/plans") / self.install_id

    def _log_path(self) -> Path:
        if os.geteuid() == 0:
            return Path("/var/log/slurm-single-node") / f"install-{self.stamp}.log"
        return Path("/tmp") / f"ssn-install-{self.stamp}.log"

    def _banner(self) -> None:
        self._log("Slurm Single-Node installer")
        self._log(f"Profile: {self.args.profile}")
        self._log(f"Dry run: {self.args.dry_run}")

    def _log(self, message: str) -> None:
        print(message)
        self._write_log(message + "\n")

    def _write_log(self, text: str) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a") as handle:
            handle.write(text)

    def _record_phase(self, name: str, status: str, detail: dict[str, Any]) -> None:
        self.report["phases"].append({
            "name": name,
            "status": status,
            "detail": detail,
            "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        })

    def _write_report(self) -> None:
        if self.report_file is None:
            return
        self.report_file.parent.mkdir(parents=True, exist_ok=True)
        self.report_file.write_text(json.dumps(redact_for_plan(self.report), indent=2, sort_keys=True) + "\n")
        self._secure_file(self.report_file)

    def _write_report_summary(self) -> None:
        if self.report_file is None:
            return
        path = self.report_file.with_name("install-summary.txt")
        lines = [
            f"Install ID: {self.report.get('install_id')}",
            f"Profile: {self.report.get('profile')}",
            f"Status: {self.report.get('status')}",
            f"Started: {self.report.get('started_at')}",
            f"Finished: {self.report.get('finished_at', '')}",
            f"Log: {self.report.get('log_file')}",
            "Phases:",
        ]
        for phase in self.report.get("phases", []):
            lines.append(f"  - {phase.get('name')}: {phase.get('status')} at {phase.get('time')}")
        if self.report.get("error"):
            lines.append(f"Error: {self.report['error']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        self._secure_file(path)

    def _secure_plan_tree(self, resolved: dict[str, Any]) -> None:
        plan_dir = self._plan_dir()
        try:
            plan_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        group = resolved.get("derived", {}).get("admin_group", "slurm_admins")
        self.admin_group = group
        gid = self._group_gid(group)
        for path in [plan_dir, *plan_dir.rglob("*")]:
            try:
                if os.geteuid() == 0 and gid is not None:
                    os.chown(path, 0, gid, follow_symlinks=False)
                if path.is_dir():
                    path.chmod(0o750)
                else:
                    path.chmod(0o640)
            except OSError:
                continue

    def _secure_file(self, path: Path) -> None:
        try:
            path.chmod(0o640)
        except OSError:
            return
        try:
            gid = self._group_gid(self.admin_group)
            if os.geteuid() == 0 and gid is not None:
                os.chown(path, 0, gid, follow_symlinks=False)
        except OSError:
            pass

    def _group_gid(self, group_name: str) -> int | None:
        try:
            return grp.getgrnam(group_name).gr_gid
        except KeyError:
            return None

    def _package_installed(self, package: str) -> bool:
        return subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout.strip() == "install ok installed"

    def _package_version(self, package: str) -> str | None:
        out = self._command_stdout(["dpkg-query", "-W", "-f=${Version}", package])
        return out or None

    def _path_exists(self, path: str | Path) -> bool:
        try:
            return Path(path).exists()
        except OSError:
            return os.geteuid() != 0

    def _package_has_candidate(self, package: str) -> bool:
        out = self._command_stdout(["apt-cache", "policy", package]) or ""
        return "Candidate: (none)" not in out and "Candidate:" in out

    def _first_available_package(self, names: list[str]) -> str | None:
        for name in names:
            if self._package_has_candidate(name):
                return name
        return None

    def _systemctl(self, action: str, service: str) -> str:
        out = self._command_stdout(["systemctl", action, service])
        return out or "unknown"

    def _findmnt(self, path: str) -> str | None:
        return self._command_stdout(["findmnt", "-no", "TARGET,SOURCE,FSTYPE,OPTIONS", path])

    def _os_pretty_name(self) -> str:
        path = Path("/etc/os-release")
        if not path.exists():
            return "unknown"
        for line in path.read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.partition("=")[2].strip().strip('"')
        return "unknown"

    def _mem_total_mb(self) -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(int(line.split()[1]) / 1024)
        except OSError:
            return None
        return None

    def _command_stdout(self, cmd: list[str]) -> str | None:
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        except Exception:
            return None

    def _free_mb(self, path: str) -> int | None:
        try:
            if not Path(path).exists():
                return None
            usage = shutil.disk_usage(path)
        except OSError:
            return None
        return int(usage.free / 1024 / 1024)

    def _user_exists(self, username: str) -> bool:
        return self._pwd_entry(username) is not None

    def _pwd_entry(self, username: str) -> Any:
        import pwd

        try:
            return pwd.getpwnam(username)
        except KeyError:
            return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ssn-install")
    parser.add_argument("--profile", default="cpu-dev-local")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--yes", "-y", action="store_true", help="accept installer prompts")
    parser.add_argument("--dry-run", action="store_true", help="show planned work without changing system state")
    parser.add_argument("--check", action="store_true", help="run Ansible in check mode after bootstrap/render")
    parser.add_argument("--skip-smoke", action="store_true", help="skip post-apply sbatch smoke tests")
    parser.add_argument("--smoke-user", default=None, help="existing local user to use for sbatch smoke tests")
    parser.add_argument("--force", action="store_true", help="allow service-changing apply while Slurm jobs are queued")
    parser.add_argument("--plan-token", default=None, help="reviewed token required with --force over risky operations")
    parser.add_argument("--drain", action="store_true", help="drain the node and wait for active jobs before service-changing install")
    parser.add_argument("--drain-timeout", default="10m", help="maximum wait for running/completing jobs when --drain is used")
    parser.add_argument("--drain-reason", default="SSN install", help="Slurm node drain reason when --drain is used")
    args = parser.parse_args(argv)
    try:
        return Installer(args).run()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
