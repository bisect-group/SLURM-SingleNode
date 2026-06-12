# SSN Current Implementation Status

Audit date: 2026-06-12

Source decision contract: `docs/decisions.md`

Repo basis: current working tree under `/home/adhil/SLURM-SingleNode`.

Live-test basis: Quadro P620 test server `bisect-node0` using profile
`gpu-bisect-quadro-p620`; install, CPU smoke, GPU smoke, over-limit rejection,
scratch job temp, scratch cleanup reporting, accounting backup, managed fixture
users, suspended-user blocking, queued-job apply refusal, risk token creation,
forced apply with a reviewed token, token single-use rejection, check-mode apply
over a queued fixture job, drained apply success, drain timeout safe resume,
drained reinstall idempotence, user-sync no-op idempotence, targeted fixture
drift repair, quota capability reporting with unsupported fixture quota apply
skip, Slurm `--export=NONE` cache/default environment injection, scratch
health marker gating, fixture-only tokenized scratch cleanup deletion, and final
CPU/GPU/scratch smoke tests were observed during live testing. A later login
and GPU-isolation tranche also live-tested fixture-scoped systemd login slices,
cgroup v2 direct GPU denial, Slurm GPU access by the same user, root GPU status
snapshots, and Slurm job mapping on the Quadro host. The inactive lifecycle
foundation was then live-tested with fixture user `ssn-test-inactive`: inactive
dry-run manifest/report generation, local-only reviewed plan token, running job
cancellation, local 7z archive creation, `/data` lockout, account removal,
UID/GID tombstone state, token reuse rejection, and explicit UID/GID
reactivation all passed. A managed `sbatch` PATH wrapper was added after live
testing showed this Slurm build did not expose `--no-requeue` reliably to
`job_submit.lua`.

Status buckets:

- **Implemented correctly**: matches the locked decision closely enough for v1.
- **Implemented differently**: working behavior exists, but it differs from the
  decision or is intentionally softened.
- **Implemented extra**: useful behavior exists beyond the original decision.
- **Yet to be implemented**: absent or only a partial foundation exists.

## Executive Summary

Core SSN is now real rather than just a plan. The generalized layout exists,
profiles resolve, Ansible can apply a CPU/GPU single-node Slurm install, and the
Quadro P620 host has passed live end-to-end install and Slurm smoke testing.

The strongest implemented areas are the resolver/profile model, core Slurm
configuration, accounting/QoS tiers, preemption wiring, single-command install,
GPU GRES rendering, per-job scratch basics, accounting database backups,
active/suspended user sync for managed fixture users, fixture-scoped login
cgroup limits, hard direct-GPU denial for fixture login sessions, and root GPU
status snapshots. User sync is now current-state-aware enough for the managed
Quadro fixtures to produce a clean no-op dry-run after repair, and the
inactive lifecycle has a fixture-scoped end-to-end implementation with reviewed
local-only archive tokens and UID/GID reactivation checks.

The largest remaining gaps are production quota enforcement, production-wide
login/GPU isolation beyond fixtures, full multi-GPU health gates and CPU-only
recovery, production-grade inactive archive backup hooks/service QoS,
CUDA/module management, and production-grade user docs.

## Generalization Model

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Use hardware/site profiles as the main generalization mechanism. | Profiles drive identity, hardware, policies, storage, and operations. | Implemented correctly | `profiles/*.yml`, `ssn/config.py` | Continue adding reviewed site profiles. |
| Cover DGX/V100, generic NVIDIA GPU, and CPU-only servers. | `dgx-v100`, `generic-gpu`, `generic-nvidia-4gpu`, `cpu-dev-local`, and live test profiles exist. | Implemented correctly | `profiles/dgx-v100.yml`, `profiles/generic-gpu.yml`, `profiles/cpu-dev-local.yml` | Review DGX render parity against old Tesla deployment before production. |
| CPU-only machine is the development/test target. | `cpu-dev-local` exists and resolves; live test work shifted to `bisect-node0` profiles. | Implemented correctly | `profiles/cpu-dev-local.yml`, tests | Local CPU live apply still optional if desired. |
| First generic NVIDIA server is separate from CPU dev host. | Generic profile remains render-gated; Quadro profile was added for live testing. | Implemented correctly | `profiles/generic-nvidia-4gpu.yml`, `profiles/gpu-bisect-quadro-p620.yml` | Target production NVIDIA CPU caps still need hardware review. |
| Reorganize moderately, preserving useful old Ansible ideas. | New tree exists while old Tesla tree remains. | Implemented correctly | `ansible/`, `ssn/`, `tesla-scheduler-v2/` | Eventually retire old tree after parity is proven. |
| Preserve Tesla behavior as `dgx-v100` plus optional aliases. | DGX profile exists and Tesla aliases are conditionally installed. | Implemented correctly | `profiles/dgx-v100.yml`, `ansible/roles/ssn_admin_tools/tasks/main.yml` | Validate on actual DGX before claiming parity. |

## Repository And Config Layout

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Use `profiles/` and domain policy files under `policies/`. | Implemented. | Implemented correctly | `profiles/`, `policies/` | None for current structure. |
| Profiles bind named policy files and local overrides. | Resolver loads selected policies and applies profile policy overrides. | Implemented correctly | `ssn/config.py` | Add stricter validation for override shapes. |
| Hardware discovery generates draft data, not direct apply. | `ssn-discover` reports system and GPU details; it does not apply them. | Implemented correctly | `ssn/cli.py` | Add draft profile generation output when needed. |
| Deep merge inheritance. | Implemented for profile inheritance and overrides. | Implemented correctly | `deep_merge` in `ssn/config.py` | Add schema annotations for list extension if ever needed. |
| Human-readable units in YAML. | Memory and durations are normalized in resolver. | Implemented correctly | `ssn/units.py`, `ssn/config.py`, tests | Broaden unit tests for all authored units. |
| Light-dependency Python resolver. | Implemented with stdlib plus PyYAML. | Implemented correctly | `ssn/config.py`, `ssn/yamlutil.py` | None. |
| Strict validation before touching the system. | Resolver validates authored profiles and policy files for `schema_version: 1`, unknown fields, required fields after profile inheritance, primitive/container types, and legal `REVIEW_REQUIRED` placement. Install/apply also validate host/profile match, capabilities, and Slurm feature shape before applying. | Implemented correctly | `ssn/config.py`, `ssn/schema.py`, `ssn/install.py`, `ssn/ops.py`, tests, live Quadro apply/install reports | Keep schemas current as new domains mature; add migrations only when schema versions change. |
| Unknown fields fail validation. | Authored profile/policy/user/state unknown fields now fail validation; `x_*` keys are allowed as explicit local-extension escape hatches. | Implemented correctly | `ssn/schema.py`, `ssn/users.py`, `tests/test_schema.py`, `tests/test_users.py` | Keep schemas current as policy files grow. |
| Dry run produces readable and JSON plan artifacts. | Installer dry-run renders/readably reports; live install/apply write protected JSON reports and summaries. `ssn-sync-users` writes protected inactive lifecycle plan reports with operation hashes when inactive actions are present. | Implemented differently | `ssn/install.py`, `ssn/cli.py`, `ssn/users.py`, live Quadro inactive plan | Make all remaining dry-runs write protected machine-readable plan artifacts consistently. |
| Plan artifacts live under `/var/lib/slurm-single-node/plans`, mode `0750/0640`, retained 90 days. | Install and apply reports plus rendered artifacts are written under protected per-run plan dirs; retention is report-only. Reports now include capability snapshots and drain phases when used. | Implemented partially | `ssn/install.py`, `ssn/cli.py`, live Quadro `install-20260611200316`, `apply-20260611195922`, `apply-20260611200037` | Implement actual retention pruning for old plan artifacts when deletion policy is approved. |
| Redaction classes for secrets, keys, emails, manifests. | Central redaction helpers exist; install reports redact sensitive key names; SSH user plans show labels/fingerprints; DB secret Ansible tasks use `no_log`. Inactive manifests are protected root/admin-readable plan artifacts, but full terminal path minimization for private manifests is still basic. | Implemented partially | `ssn/safety.py`, `ssn/users.py`, tests, live user and inactive dry-runs | Extend redaction/path minimization to future production prune/archive manifests and all plan artifact writers. |
| Risky operations require reviewed plan id/hash tokens. | Implemented for queued-jobs risk on install/apply, fixture-only scratch cleanup deletion, and fixture inactive local-only archive apply. Tokens are config/input/operation-hash-bound where available, expiring, stored hashed, and single-use. Live forced apply, cleanup deletion, inactive archive apply, token reuse rejection, and operation-hash mismatch rejection passed. Other risky workflows do not use tokens yet. | Implemented partially | `ssn/ops.py`, `ssn/install.py`, `ssn/cli.py`, `ssn/storage.py`, `ssn/users.py`, `bin/ssn-plan-token`, tests, live Quadro token tests | Reuse this token system for production inactive backup/removal, retention deletion, and broader cleanup applies. |
| Persist resolved audit file at `/etc/slurm-single-node/config.yml`. | Implemented by base role. | Implemented correctly | `ansible/roles/ssn_base/tasks/main.yml` | None. |

## Target Scope

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Ubuntu 24.04 and 26.04 primary, capability-gated. | Ansible asserts Ubuntu major version >= 24 and cgroup v2; shared capability probes record command, package, runtime, mount, free-space, Slurm, accounting, Lua, and NVIDIA details. Apply/install fail on missing required commands, cgroup v2, storage mount/write checks, apply-time accounting access, and whole-GPU basics. | Implemented partially | `ansible/site.yml`, `ssn/install.py`, `ssn/ops.py`, live install/apply reports | Add deeper probes for NVML/CUDA ordering, MIG/MPS/shared GPU modes, and package/runtime incompatibilities. |
| Use Ubuntu apt packages. | Installer and Ansible use apt packages. | Implemented correctly | `ssn/install.py`, `ansible/roles/ssn_base/tasks/main.yml` | Add unattended-upgrade protection for Slurm packages. |
| Target cgroup v2 only. | Installer and Ansible fail if cgroup fs is not `cgroup2fs`; Slurm cgroup config uses v2. | Implemented correctly | `ssn/install.py`, `ansible/site.yml`, `cgroup.conf.j2` | None. |
| NVIDIA whole-GPU and CPU-only only; MIG/MPS/shared fail closed unless supported. | Profiles encode fail-closed modes and feature gates verify expected whole-GPU count/device files. MIG/MPS/shared mode detection is not implemented. | Implemented partially | `profiles/generic-gpu.yml`, `ssn/ops.py` | Add validation of MIG/MPS/shared modes during GPU verify. |
| Validate driver, do not install it. | Installer/Ansible require `nvidia-smi` for GPU profiles; no driver install role. | Implemented correctly | `ssn/install.py`, `ansible/site.yml` | Add clearer driver/toolkit diagnostic output. |
| GPU mapping verification as boot/apply health gate. | Basic GPU count, `/dev/nvidiaN`, rendered GRES entry count, installed `gres.conf`, and Slurm node GRES checks exist. Root GPU status snapshots and single-GPU Slurm job mapping are now live-tested. Full NVML/CUDA ordering, topology, multi-GPU mapping, and health-state gate remain incomplete. | Implemented partially | `ansible/verify.yml`, `ssn/ops.py`, `ssn/cli.py`, `ssn/login.py`, live Quadro reports | Implement full GPU verification and health-state handling. |
| CPU-only recovery overlay on GPU verification failure. | Not implemented. | Yet to be implemented | No overlay/recovery code present | Add drain/hold/overlay workflow. |
| Apptainer optional/off by default. | Policy marks off; roots are created. No install/config workflow. | Implemented partially | `policies/modules.yml`, `ssn_modules` role | Add optional Apptainer management if profile enables it. |

## Commands And Compatibility

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Default command prefix is `ssn-*`. | Implemented for repo and installed wrappers. | Implemented correctly | `bin/ssn-*`, `ansible/roles/ssn_admin_tools/tasks/main.yml` | Make prefix fully configurable for all wrappers if non-ssn prefix is needed. |
| Core helper commands exist. | All listed commands exist, plus installer, scratch cleanup, scratch health, login isolation/status, and GPU collector commands. `ssn-archive-status` reports fixture archive states when present. | Implemented correctly | `bin/`, `ssn/cli.py` | Expand archive status once service archive jobs and backup hooks exist. |
| DGX may install `tesla-*` aliases. | Conditional alias install exists for selected tools. | Implemented correctly | `ssn_admin_tools` role | Expand aliases only after DGX parity review. |
| Deployed user source is `/etc/slurm-single-node/users.yml`. | CLI default points there. | Implemented correctly | `ssn/cli.py` | Ensure installer creates example or empty file when desired. |

## Accounting

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Keep MariaDB and `slurmdbd` accounting. | Implemented and live tested. | Implemented correctly | `ansible/roles/ssn_slurmdbd`, live Quadro install | None for core service. |
| QoS is tier authority with one default account. | Implemented via `sacctmgr` account/QoS setup. | Implemented correctly | `ansible/roles/ssn_slurm_config/tasks/main.yml` | Add admin/service accounts for archive workflows. |
| Strict `cons_tres`, consumable CPU/mem/GRES, cgroup enforcement. | Slurm config renders `select/cons_tres`, cgroup plugins, memory defaults, and GPU GRES when needed. Apply/install now validate resolved and installed Slurm feature settings. | Implemented correctly | `slurm.conf.j2`, `cgroup.conf.j2`, `ssn/ops.py`, live Quadro installed feature validation | Verify all supported Slurm versions accept exact settings. |
| GPU TRES/accounting where available. | GPU profiles include `gres/gpu`; CPU profiles remove GPU TRES. | Implemented correctly | `ssn/config.py`, `slurm.conf.j2` | Add version/capability check for GPU TRES support. |
| Explicit memory defaults pinned by profile. | Profiles carry `def_mem_per_cpu` and `max_mem_per_cpu`; render fails on unresolved values. | Implemented correctly | `profiles/*.yml`, `ssn/config.py` | Add tests for memory REVIEW_REQUIRED edge cases. |
| Generate local slurmdbd DB password once. | Implemented with protected file. | Implemented correctly | `ssn_slurmdbd` role | Add secret backup handling. |
| Daily MariaDB dumps with 30-day retention. | Implemented as `ssn-slurmdb-backup.timer` plus `/usr/local/sbin/ssn-backup-slurmdb`; live manual run created a compressed dump. | Implemented correctly | `ansible/roles/ssn_slurmdbd/tasks/main.yml`, live Quadro backup file | Add external backup integration later if needed. |
| Munge key generated if absent, protected, backed up, not auto-rotated. | Generated if absent and permissioned. Existing key is preserved. Backup is via installer preinstall copy, not dedicated secret backup. | Implemented partially | `ssn_base` role, `ssn/install.py` | Add protected service-secret backup policy. |

## User Policy Model

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| YAML user source, keyed by username. | Implemented. | Implemented correctly | `ssn/users.py`, `profiles/users.example.yml` | None. |
| Bootstrap discovery of human users and authorized keys. | `ssn-discover --users` scans UID range and keys. | Implemented correctly | `ssn/users.py`, `ssn/cli.py` | Improve exclude/adoption review controls. |
| Preserve SSH key options raw plus parsed. | Parser stores `options_raw`, parsed `options`, comment, fingerprint; renderer preserves raw options. | Implemented correctly | `ssn/users.py`, tests | Add more option syntax tests. |
| `ssh_keys` omitted/null unmanaged; `{}` managed empty. | Implemented in planning and apply. | Implemented correctly | `ssn/users.py` | None. |
| Options raw and parsed mismatch fails. | Implemented validation. | Implemented correctly | `ssn/users.py`, tests | None. |
| Managed users removed from YAML fail validation. | Planning emits a risky validation error and apply pre-scans validation errors before mutating. | Implemented correctly | `plan_user_sync`, `apply_user_actions`, tests | None for v1. |
| UID/GID auto with explicit override support. | User creation supports explicit UID/GID; otherwise system allocates; explicit ID conflicts are validated. | Implemented correctly | `ssn/users.py`, tests | Broaden adoption-plan UX. |
| Inactive reactivation must reuse original UID/GID. | Planning validates this case, and live Quadro reactivation without explicit IDs was rejected after tombstoning. Reactivation with original UID/GID recreated `ssn-test-inactive` and restored `/data` ownership. | Implemented correctly | `ssn/users.py`, tests, live Quadro inactive/reactivation test | Broaden restore workflow beyond fixture testing. |
| Permanent UID/GID tombstones. | Fixture inactive apply records original UID/GID and reaches `archive_state: tombstoned` after account removal. Reactivation consumes the original IDs. Permanent tombstone reservation after production account removal is only fixture-proven. | Implemented partially | `ssn/users.py`, live Quadro `ssn-test-inactive` state | Add admin migration/clear workflow and broader conflict tests. |
| Back up `users.yml` and `users-state.yml` before writes. | CLI backs up both existing files before apply and reports 90-day retention candidates without deleting them. | Implemented partially | `ssn/cli.py`, `ssn/users.py`, live Quadro sync | Add approved retention pruning if desired. |
| Top-level groups metadata only; user groups authoritative. | Validation rejects `groups.*.members`; apply reconciles SSN-managed supplementary groups and removes stale managed project/tier membership. | Implemented correctly | `ssn/users.py`, tests, live Quadro fixture groups | None for v1 managed users. |
| Admin-exempt users in profile/site config. | Profiles define admins; Ansible creates admin group memberships. | Implemented partially | `profiles/*.yml`, `ssn_base` role | Wire admin exemptions into login confinement once implemented. |
| User sync idempotence for managed fixtures. | Active/suspended fixture users now compare current Unix lock/expiry state, groups, authorized keys, data/scratch directories, Slurm associations, and state file values before planning. Live dry-run is a clean no-op after apply; a deliberate fixture key drift planned only `sync_authorized_keys` and repaired cleanly. | Implemented correctly | `ssn/users.py`, `tests/test_users.py`, live Quadro sync and drift repair | Broaden the same current-state comparison UX for non-fixture adoption and future inactive lifecycle states. |
| Staged state-machine reconciliation and resumable repair. | Basic state file update, backups, and validation pre-scan exist; no true staged/resumable state machine. | Yet to be implemented | `ssn/users.py` | Implement resumable user/group/Slurm/archive reconciliation. |
| Tier templates and per-user overrides. | Tier templates exist. Override validation detects overlapping active fields, but overrides are not applied to associations/limits. | Implemented partially | `policies/tiers.yml`, `ssn/users.py` | Implement override resolution, expiry reconcile, and enforcement. |
| Suspended lifecycle blocks login/Slurm and kills jobs. | Apply locks the Unix account, removes the default Slurm association, and runs `scancel`; live test killed a pending fixture job and blocked new submissions. PAM/login denial is basic account lock only. | Implemented differently | `ssn/users.py`, live Quadro suspended fixture | Add complete PAM/login denial validation and decide whether association removal is the final Slurm-disable mechanism. |
| Inactive lifecycle archives, prunes, removes after backup. | Fixture-scoped local-only inactive lifecycle is implemented for `ssn-test-inactive`: dry-plan manifest, reviewed token, job cancellation, account lock, Slurm association disable, data lock, allowlisted prune, local 7z archive, account removal, tombstone, and reactivation validation were live-tested. Real backup/replication success is not implemented. | Implemented partially | `ssn/users.py`, `ssn/cli.py`, live Quadro inactive lifecycle test | Add production backup hooks, service archive jobs, and non-fixture rollout controls. |

## Slurm Partitions And Job Requests

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Single default partition per profile. | Implemented. | Implemented correctly | `profiles/*.yml`, `slurm.conf.j2` | None. |
| Generic partition name `compute`. | Implemented for generic and test profiles. | Implemented correctly | `profiles/generic-gpu.yml`, `profiles/gpu-bisect-quadro-p620.yml` | DGX intentionally uses `gpu`. |
| CPU jobs default; GPU jobs explicitly request GRES. | Implemented and documented in examples. | Implemented correctly | `slurm.conf.j2`, `user-kit/README.md` | Expand docs per profile. |
| CPU and GPU jobs share node under cgroups and limits. | Implemented in Slurm config and live-tested for GPU allocation. | Implemented correctly | live Quadro smoke | Add concurrent mixed workload tests. |
| `job_submit.lua` is fast gate for explicit request fields. | Lua gate checks explicit QoS, CPU, GPU, scratch health, and attempts to reject no-requeue without shellouts. On Slurm 25.11, `--no-requeue` was not exposed reliably to Lua at submit time, so a managed `/usr/local/bin/sbatch` wrapper was added for the ordinary user PATH. | Implemented differently | `job_submit.lua.j2`, `sbatch-wrapper.j2`, live Quadro no-requeue test | Investigate Slurm `cli_filter` or another server-side path for absolute `/usr/bin/sbatch` no-requeue enforcement. |
| Reject over-tier CPU/GPU/RAM/walltime, unsafe GPU syntax, no-requeue. | CPU and GPU over-limit rejections passed via Slurm/QoS. Ordinary `sbatch --no-requeue` through PATH now rejects via managed wrapper; absolute `/usr/bin/sbatch --no-requeue` still bypasses on this Slurm build. RAM/walltime/unsafe syntax coverage is incomplete. | Implemented partially | `job_submit.lua.j2`, `sbatch-wrapper.j2`, live Quadro fixture jobs | Add RAM/walltime/unsafe syntax checks and a server-side no-requeue enforcement path if available. |

## Fairshare And Billing

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Slurm multifactor fairshare with 7-day decay. | Rendered in `slurm.conf`. | Implemented correctly | `slurm.conf.j2` | Validate resulting priority behavior with jobs. |
| Billing weights CPU=1, GPU=64, RAM not billed. | Partition `TRESBillingWeights` renders CPU and GPU; CPU-only omits GPU. | Implemented correctly | `slurm.conf.j2` | Confirm account/user shares behavior in Slurm DB. |
| Equal user shares under default account. | Default account exists; user sync creates associations and caps allowed QoS by tier rank. Explicit per-user fairshare shares are not actively managed. | Implemented partially | `ssn_slurm_config` tasks, `ssn/users.py`, live associations | Add explicit share management when user sync matures. |
| Hard compute quotas are future work. | Not implemented, as intended. | Implemented correctly | No quota compute code | None. |

## Preemption

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| QOS-based preemption with `REQUEUE`, `JobRequeue=1`, grace 300s. | Implemented in Slurm config and QoS setup. | Implemented correctly | `slurm.conf.j2`, `ssn_slurm_config` tasks | Live preemption test still needed. |
| QOS `Preempt=` relationships by tier rank. | Implemented from policy relationships. | Implemented correctly | `ssn/config.py`, `ssn_slurm_config` tasks | Add test asserting rendered relationships. |
| Reject normal-user `--no-requeue`. | Implemented for ordinary user PATH through a managed `sbatch` wrapper and retained in Lua where Slurm exposes the field. Live testing showed absolute `/usr/bin/sbatch --no-requeue` still bypasses the wrapper. | Implemented differently | `sbatch-wrapper.j2`, `job_submit.lua.j2`, live Quadro no-requeue regression test | Find a Slurm-side enforcement option or `cli_filter` workflow for absolute binary bypass. |
| Interactive `srun` allowed but canceled on preemption. | Docs mention interactive examples; no explicit cancellation policy code beyond Slurm preemption mode. | Yet to be implemented | `user-kit/examples/20-interactive-srun.sh` | Validate actual Slurm behavior and document exactly. |
| Teach checkpointing. | User docs are basic; checkpointing guidance is not production-grade. | Yet to be implemented | `user-kit/README.md` | Expand docs/examples. |

## Login Policy

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Constrained login default, not strict Slurm-only SSH. | Implemented as policy docs, MOTD/banner, shell defaults, and process limit. | Implemented differently | `ssn_user_policy` role | Add real cgroup login slice enforcement. |
| Per non-admin user login slice: 2 CPUs, 4 GB RAM, 128 tasks, low I/O weight. | Implemented and live-tested for active managed `ssn-test-*` fixture users with per-user `user-<uid>.slice` drop-ins: `CPUQuota=200%`, `MemoryMax=4GB`, `TasksMax=128`, and `IOWeight=50`. Slurm CPU/GPU jobs by the same user stayed under `slurmstepd` cgroups, not the login slice. Production-wide non-admin rollout is not enabled. | Implemented partially | `ssn/login.py`, `ssn-login-isolation`, live Quadro SSH/session and Slurm job cgroup tests | Generalize beyond fixtures after more safety testing. |
| Admin users exempt. | Fixture-scoped login isolation excludes configured admins; root/admin Slurm operation remains unaffected. Production-wide cap exemption still needs rollout testing. | Implemented partially | `ssn/login.py`, profiles, live Quadro tests | Re-test when applying to all managed/non-admin users. |
| Hard non-Slurm GPU denial through cgroup v2 devices. | Implemented and live-tested for active managed fixture login sessions in cgroup mode. SSH as `ssn-test-standard` landed in `user-1003.slice`; absolute `/usr/bin/nvidia-smi` failed with NVML error, while `sbatch --gres=gpu:1 nvidia-smi` by the same user succeeded under Slurm cgroups. Production-wide denial is not enabled. | Implemented partially | `ssn/login.py`, per-user slice drop-ins, live Quadro direct-denial and Slurm GPU tests | Generalize scope and add multi-user/multi-GPU tests. |
| Friendly PATH wrappers for direct GPU tools. | `nvidia-smi` wrapper is installed on GPU profiles. It allows root/admins and Slurm jobs through to `/usr/bin/nvidia-smi`; ordinary login use gets a friendly Slurm-only message. Other GPU tools are not wrapped yet. | Implemented partially | `nvidia-smi-wrapper.j2`, live Quadro wrapper test | Add wrappers for additional GPU tools as needed. |
| Profile-prefixed GPU status wrapper with 10s root snapshot and Slurm job mapping. | `ssn-gpu-status` now reads a root/service snapshot refreshed by `ssn-gpu-status.timer`; the collector includes utilization, memory, temperature, identity, and Slurm job/user mapping. Live single-GPU Quadro mapping worked for a running fixture GPU job. Multi-GPU exact device mapping remains basic. | Implemented partially | `ssn/login.py`, `ssn-gpu-collector`, admin tools role, live Quadro snapshot test | Harden multi-GPU mapping and stale/failure reporting. |
| No process-policing daemon in v1. | No policing daemon exists. | Implemented correctly | Repo inspection | None. |

## Storage Policy

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Optional `/home`, `/data`, `/scratch` per profile. | Implemented policies for no-scratch and three-area layouts. | Implemented correctly | `policies/storage.yml`, profiles | None. |
| `/home` persistent path. | Installer allows `/home` to be a directory on `/` for the test host. | Implemented differently | `ssn/install.py` | Document this as an allowed dev/test compromise. |
| RAID0 `/data` persistent but not durable, external backup required. | Policy records it; enforcement/acknowledgment is not active. | Yet to be implemented | `policies/storage.yml` | Add validation requiring site acknowledgment for nondurable data. |
| Admins provision filesystems; automation verifies mounts. | Installer/verify checks `/data` and `/scratch` mounts for scratch profiles; quota capability reporting now records command and active user-quota availability for `/data` and `/scratch`. | Implemented correctly | `ssn/install.py`, `ssn/cli.py`, `ssn/storage.py`, storage role | Add deeper filesystem type/free-space thresholds if needed. |
| Quota-managed home/data/scratch and quota capability validation. | Quota capability detection exists, and `ssn-sync-users --apply-fixture-quotas` can apply limits only for `ssn-test-*` users when user quotas are already active. Live Quadro had no active user quotas, so fixture quota apply skipped without remounting or editing `/etc/fstab`. Production quota enforcement is not implemented. | Implemented partially | `ssn/storage.py`, `ssn/cli.py`, `tests/test_storage.py`, live Quadro quota report/apply skip | Implement production quota enablement/enforcement after explicit mount/quota policy signoff. |
| `/data` and `/scratch` capacity isolation. | Policy-only. | Yet to be implemented | `policies/storage.yml` | Validate separate filesystems/LVs/project quotas. |
| Create `/data/$USER`, `/scratch/$USER/cache`, `/scratch/$USER/tmp`. | User sync apply creates data and scratch user directories. | Implemented correctly | `ssn/users.py` | Add quota assignment and ownership repair checks. |
| Per-job scratch via root Prolog/Epilog and TaskProlog exports temp vars. | Implemented and live-tested. Prolog now marks scratch unhealthy on failure where practical. | Implemented correctly | prolog/epilog/task-prolog templates, live job output | Keep adding failure-mode coverage. |
| Scratch-required profiles block jobs until scratch healthy. | `ssn-scratch-health` writes a health report and `/run/slurm-single-node/scratch-unhealthy` marker; `job_submit.lua` rejects new scratch-dependent jobs while the marker exists. Live chmod drift of `/scratch/ssn-test-standard/tmp` produced an unhealthy marker, blocked submission, then restored cleanly. | Implemented correctly | `ssn/storage.py`, `ssn/cli.py`, `job_submit.lua.j2`, live Quadro scratch health test | Add apply/install integration for automatic health checks before service changes if desired. |
| Scratch cleanup deletes eligible aged files, excluding job scratch. | Production cleanup remains report-only. Fixture-only deletion is implemented for reviewed tokenized reports containing top-level `/scratch/ssn-test-*` candidates; live deletion removed only `/scratch/ssn-test-cleanup-live`, and token reuse/mismatch failed. | Implemented differently | `ssn/storage.py`, `ssn-scratch-cleanup`, live Quadro cleanup token test | Add production deletion mode only after broader cleanup policy signoff. |

## Cache Policy

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Broad-dev cache requires scratch. | Resolver validates scratch requirement. | Implemented correctly | `ssn/config.py`, `policies/cache.yml` | None. |
| Core scratch/persistent/home cache env map. | Policy contains locked env map. | Implemented correctly | `policies/cache.yml` | None. |
| Inject cache defaults into login shells and Slurm jobs. | `/etc/profile.d` injects shell defaults, and TaskProlog now emits Slurm job defaults independently of login-shell inheritance. Live `sbatch --export=NONE` received scratch, persistent, and home-config defaults. | Implemented correctly | `ssn-profile.sh.j2`, `ssn-job-env-task-prolog.j2`, live Quadro env job | Add more framework-specific env checks if needed. |
| User-provided values override managed defaults. | Shell and TaskProlog templates only export managed defaults when variables are empty. | Implemented correctly | `ssn-profile.sh.j2`, `ssn-job-env-task-prolog.j2` | Add a live explicit-env override test later. |
| TaskProlog overrides job temp vars to per-job scratch. | Implemented. | Implemented correctly | `ssn-job-env-task-prolog.j2` | None. |
| No default Matplotlib env override. | No `MPLCONFIGDIR` in cache policy. | Implemented correctly | `policies/cache.yml` | None. |

## Inactive Users And Archives

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Kill jobs, lock data, prune, archive, backup hook, remove account after success. | Fixture local-only path is implemented: a running `ssn-test-inactive` job was canceled, the Unix account was locked/removed, Slurm association disabled, `/data/ssn-test-inactive` chowned root, home pruned/archived, and state tombstoned. Real backup hook success is not implemented. | Implemented partially | `ssn/users.py`, live Quadro job 46 and archive test | Add production backup hook gate and non-fixture controls before real users. |
| Archive job under service/admin Slurm identity and protected QoS. | Fixture archive currently runs directly as root/admin during `ssn-sync-users --apply`; it does not submit a protected Slurm archive job. | Implemented differently | `ssn/users.py`, live Quadro inactive apply | Add service/admin QoS and monitored Slurm archive job submission. |
| Archive root required and Slurm unavailable blocks transition. | Archive root is required by the fixture apply path and `/data/_archive` was used live. Slurm job cancellation/association commands run during apply, but a full "Slurm unavailable blocks transition" preflight is not complete. | Implemented partially | `ssn/users.py`, live Quadro archive path | Add explicit Slurm health gate to inactive transition planner. |
| Prune allowlist, symlink safety, report-only build trees. | Implemented for the fixture manifest/apply path: fixed allowlisted paths, marker-detected venv/conda envs, symlink remove-link-only handling, and report-only build trees. Live manifest found delete and report-only candidates. | Implemented partially | `ssn/users.py`, `tests/test_users.py`, live Quadro inactive plan | Broaden tests and keep production apply disabled until policy signoff. |
| Dry plan writes plan id/hash and real apply requires token. | Implemented for inactive fixture lifecycle. Dry-run writes a protected plan with `operation_hash`; apply requires `inactive_local_only_archive` token; token reuse failed live. | Implemented correctly | `ssn/cli.py`, `ssn/users.py`, `ssn-plan-token`, live Quadro inactive plan/token test | Reuse for production backup/removal risks. |
| Backup hooks after local archive. | Not implemented. The Quadro test used the reviewed local-only override token path. | Yet to be implemented | No hook runner present | Add hook directory, environment contract, status handling. |
| UID/GID tombstones and reactivation semantics. | Fixture account removal records original UID/GID and tombstone state. Reactivation without explicit original IDs failed; reactivation with original UID/GID recreated the fixture and restored `/data` ownership. | Implemented partially | `ssn/users.py`, tests, live Quadro reactivation | Add permanent production tombstone reservation and admin migration controls. |

## Modules And Shared Software

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Use Lmod. | Installed when enabled; roots created. | Implemented partially | `ssn_modules` role | Create actual modulefiles and loader behavior. |
| Shared roots under `/tools`. | Implemented. | Implemented correctly | `ssn_modules` role | None. |
| Miniconda default shared base. | Policy records Miniconda root; role does not install/manage it. | Yet to be implemented | `policies/modules.yml`, `ssn_modules` role | Add validate-only or managed Miniconda workflow. |
| CUDA validate-only by default, versioned modules when detected. | Policy-only. No CUDA module detection/rendering. | Yet to be implemented | `policies/modules.yml` | Implement CUDA detection, modulefiles, smoke checks. |
| Admin-run updates, smoke checks, rollback targets. | Policy-only. | Yet to be implemented | `policies/modules.yml` | Build update workflow. |
| Optional Apptainer and `/tools/containers`. | Root exists; Apptainer install/config absent. | Implemented partially | `ssn_modules` role | Add optional profile-controlled Apptainer role. |

## Project Groups

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Basic project/lab groups in `users.yml`. | Implemented validation and authoritative membership reconciliation for SSN-managed groups. | Implemented correctly | `ssn/users.py`, tests, live Quadro fixtures | None for v1 managed users. |
| No shared group storage v1. | No shared group storage exists. | Implemented correctly | Repo inspection | None. |
| Module visibility global v1. | No per-group module visibility exists. | Implemented correctly | `ssn_modules` role | None. |

## Canonical YAML Sketches

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| `users.yml` shape with groups and keyed users. | Implemented with stricter v1 validation for top-level, group, user, SSH-key, and override keys. | Implemented correctly | `profiles/users.example.yml`, `ssn/users.py`, tests | Extend schema as new lifecycle fields are implemented. |
| `users-state.yml` state shape with archive states. | State validation accepts archive fields and the fixture inactive lifecycle records archive path, operation hash, local-only flag, original UID/GID, `removal_ready`, and `tombstoned`. Full production archive substates and backup failure handling are incomplete. | Implemented partially | `ssn/users.py`, tests, live Quadro `ssn-test-inactive` state | Add backup failure/retry substates and production tombstone migration controls. |
| Profile binding shape with services, admins, operations. | Profiles match the broad shape. | Implemented correctly | `profiles/*.yml` | Add strict schema and migrations. |
| `policies/slurm-core.yml` shape. | Policy exists and drives render. | Implemented correctly | `policies/slurm-core.yml`, templates | Add validation for unsupported fields. |
| `policies/tiers.yml` shape. | Policy exists and drives QoS/rendered tiers. | Implemented correctly | `policies/tiers.yml`, `ssn/config.py` | Add tests for all tier variants. |
| `policies/storage.yml` shape. | Policy exists; directory creation, per-job scratch, scratch health marker gating, quota capability reporting, and fixture-only cleanup apply are active. Archive, production quota enforcement, and production cleanup deletion remain incomplete. | Implemented partially | `policies/storage.yml`, `ssn/storage.py`, storage role | Implement archive workflows and production quota/cleanup enforcement after policy signoff. |
| `policies/cache.yml` shape. | Policy exists and is injected into login shells and Slurm jobs as defaults. | Implemented correctly | `policies/cache.yml`, `ssn-profile.sh.j2`, `ssn-job-env-task-prolog.j2`, live `--export=NONE` job | Add profile-specific cache extensions only when needed. |
| `policies/modules.yml` shape. | Policy exists; roots only. | Implemented partially | `policies/modules.yml`, modules role | Implement module/CUDA behavior. |
| `policies/login.yml` shape. | Policy exists; conservative limits/banner are active, and fixture-scoped systemd login-slice enforcement plus GPU cgroup denial are implemented through explicit admin commands. Production-wide policy application remains incomplete. | Implemented partially | `policies/login.yml`, `ssn_user_policy` role, `ssn/login.py`, live Quadro tests | Generalize and harden login/GPU isolation beyond fixtures. |

## User-Facing Docs And Examples

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| New `user-kit/` docs and examples. | Basic docs and CPU/GPU examples exist. The README now explains constrained login, direct GPU denial, `ssn-gpu-status`, and Slurm GPU workflow. | Implemented partially | `user-kit/README.md`, `user-kit/examples/` | Generate/profile-check examples and expand guidance. |
| Remove missing `gpu-shell`/`gpu-jupyter` helper model. | New docs use `sbatch`/`srun`; old `SLURM-user-kit` still exists for reference. | Implemented correctly | `user-kit/` | Decide when to archive/remove old user kit. |
| Interactive workflows examples only. | `20-interactive-srun.sh` exists; no helper command added. | Implemented correctly | `user-kit/examples/20-interactive-srun.sh` | Add caveats around preemption/cancellation. |

## Testing And Rollout

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Local apply inventory by default. | Implemented with localhost inventory. | Implemented correctly | `ansible/inventories/local.ini`, installer | None. |
| CPU-only validation before GPU rollout. | CPU profiles resolve; live GPU test was also performed on disposable Quadro host. | Implemented differently | tests, live Quadro notes | CPU live smoke on `cpu-dev-local` remains optional. |
| Render-review gate for GPU/DGX production. | Generic GPU contains `REVIEW_REQUIRED`; render fails unless allowed. | Implemented correctly | `ssn/config.py`, tests | Add review artifact workflow. |
| Live apply refuses changes while jobs are running unless force/drain. | `ssn-install` and `ssn-apply --run` refuse when `squeue` has queued jobs unless `--force --plan-token` is supplied, or `--drain` successfully drains the node and waits for active jobs to clear. `--check` remains allowed without a token. | Implemented correctly | `ssn/install.py`, `ssn/cli.py`, `ssn/ops.py`, live Quadro queued-job refusal, force token, drain success, drain timeout, and drained install tests | Add a richer drain/hold workflow only if future multi-node or recovery needs demand it. |
| Resumable apply/sync workflows. | Partial user state update exists; install reports phases. Not truly resumable. | Yet to be implemented | `ssn/install.py`, `ssn/users.py` | Add staged reconciliation and repair plans. |
| GPU verification tests include login denial and Slurm GPU access. | Fixture-scoped login denial and Slurm GPU access by the same user were live-tested on Quadro. This is not yet a full production GPU health gate and does not cover multi-GPU mapping or CPU-only recovery. | Implemented partially | live Quadro SSH direct-denial and Slurm GPU tests | Add full GPU verification suite. |
| Scratch-unhealthy, archive, hook, tombstone tests. | Scratch happy path, per-job cleanup, and unhealthy marker/job rejection were live-tested. Fixture inactive dry-plan, local archive, tombstone, token reuse, job cancellation, and reactivation were live-tested. Backup hook tests remain absent. | Implemented partially | `ssn/storage.py`, `ssn/users.py`, live Quadro scratch and inactive lifecycle tests | Add backup-hook success/failure tests with production inactive lifecycle. |
| Static tests. | Unit tests, compileall, shell syntax, render checks, `git diff --check`, and remote static tests pass. Tests now include schema validation, capability gates, drain wait timeout behavior, user-sync idempotence, quota report safety, scratch health, cleanup operation hashes, fixture cleanup safety, inactive plan reports, prune manifests, and fixture-limited inactive apply. | Implemented correctly | `tests/`, live session notes | Add CI entrypoint when repo is ready. |

## Implemented Extra

| Extra Item | Current Code State | Evidence | Keep / Follow-up |
|---|---|---|---|
| `gpu-bisect-quadro-p620` live profile. | Added for disposable Quadro test server. | `profiles/gpu-bisect-quadro-p620.yml` | Keep as useful hardware fixture. |
| `cpu-bisect-node0` profile. | CPU-only profile for same test host. | `profiles/cpu-bisect-node0.yml` | Keep for CPU-only recovery testing. |
| `starter-single-gpu-small` and `starter-cpu-small` policies. | Added for smaller machines. | `policies/tiers.yml` | Keep as practical starter tiers. |
| Single-command installer smoke-user association setup. | Installer can create/update smoke user Slurm association for tests. | `ssn/install.py` | Keep; make clear it is test/smoke behavior. |
| Automated install smoke rejection tests. | Installer tests over CPU and over GPU. `--no-requeue` is covered by the managed `sbatch` wrapper live regression test rather than installer smoke. | `ssn/install.py`, `sbatch-wrapper.j2`, live Quadro notes | Expand to RAM/walltime/preemption and server-side no-requeue enforcement if available. |
| `ssn-scratch-cleanup` report-only command. | Added with service/timer integration. | `ssn/cli.py`, `bin/ssn-scratch-cleanup` | Convert to reviewed deletion mode later. |
| `ssn-scratch-health` command. | Added for scratch health reports and unhealthy marker management; live tests verified healthy, unhealthy, submission block, and recovery paths. | `ssn/storage.py`, `bin/ssn-scratch-health`, live Quadro scratch health test | Keep; extend to install/apply preflight if desired. |
| Fixture-only scratch cleanup deletion. | Reviewed tokenized reports can delete only top-level `/scratch/ssn-test-*` candidates; live test deleted a fixture path and rejected token reuse/mismatch. | `ssn/storage.py`, `ssn-scratch-cleanup`, live Quadro cleanup test | Keep scoped to fixtures until production deletion is explicitly approved. |
| Fixture quota apply skip path. | `ssn-sync-users --apply-fixture-quotas` reports skipped fixture quotas when user quotas are not already active, without remounting or editing `/etc/fstab`. | `ssn/storage.py`, live Quadro quota report | Keep; production quota enforcement remains future work. |
| Managed fixture users on Quadro. | `ssn-test-standard`, `ssn-test-priority`, `ssn-test-suspended`, and reactivated `ssn-test-inactive` remain on the test host for repeat sync/idempotence/lifecycle tests. | live Quadro users.yml/state | Keep as disposable test fixtures; do not treat as production users. |
| Report-only retention helpers. | Plan and user-backup retention candidates are reported without deleting files. | `ssn/safety.py`, `ssn/install.py`, `ssn/cli.py` | Convert to approved deletion only after policy signoff. |
| `ssn-plan-token` risk-token helper. | Added as an admin command for creating reviewed, short-lived tokens from install/apply reports. | `bin/ssn-plan-token`, `ssn/ops.py`, `ssn/cli.py`, live Quadro token test | Extend beyond queued-job risk as future risky workflows are implemented. |
| Explicit drain workflow. | `ssn-install` and `ssn-apply --run` support `--drain`, `--drain-timeout`, and `--drain-reason`; live tests covered success, timeout safe resume, and drained reinstall idempotence. | `ssn/install.py`, `ssn/cli.py`, `ssn/ops.py`, live Quadro reports | Keep; consider pending-job hold semantics only if future workflows need it. |
| Fixture-scoped login isolation commands. | Added `ssn-login-isolation` and `ssn-login-status` for cgroup/ACL/disabled modes and per-user slice reporting. Live cgroup mode succeeded; ACL fallback code exists but was not needed live. | `ssn/login.py`, `bin/ssn-login-isolation`, `bin/ssn-login-status`, live Quadro tests | Keep fixture-scoped until wider rollout is approved. |
| Root GPU status collector. | Added `ssn-gpu-collector` plus `ssn-gpu-status.service/timer`; live snapshot and single-GPU job mapping passed. | `ssn/login.py`, `bin/ssn-gpu-collector`, admin tools role, live Quadro tests | Harden multi-GPU mapping. |
| Fixture inactive lifecycle. | Added token-gated inactive dry-plan/apply for `ssn-test-inactive`, including prune manifest, local archive, tombstone, and reactivation validation. | `ssn/users.py`, `ssn/cli.py`, live Quadro inactive test | Keep fixture-scoped until backup hooks and production controls are implemented. |
| Managed `sbatch` no-requeue wrapper. | Added `/usr/local/bin/sbatch` wrapper to reject ordinary user `--no-requeue` submissions after Slurm 25.11 did not expose the flag reliably to `job_submit.lua`; direct `/usr/bin/sbatch` still bypasses. | `sbatch-wrapper.j2`, live Quadro no-requeue test | Replace or supplement with Slurm-side enforcement if available. |

## Next Implementation Queue

### Priority 1: Safety And Correctness Gates

- Extend the reviewed-token pattern to remaining risky operations, especially
  production inactive backup/removal and production retention deletion.
- Deepen capability gates for Slurm submit-plugin support, MariaDB access
  modes, Lua plugin compatibility, NVML/CUDA ordering, MIG/MPS/shared GPU
  detection, and package/runtime compatibility.
- Investigate a Slurm-side or `cli_filter` no-requeue enforcement path because
  `job_submit.lua` did not catch absolute `/usr/bin/sbatch --no-requeue` on the
  Quadro Slurm 25.11 stack.
- Add schema migration handling if/when `schema_version` moves beyond v1.
- Add approved pruning for old plan/user-backup artifacts if retention should
  delete rather than report.

### Priority 2: User And Storage Foundations

- Broaden user sync adoption UX and conflict validation beyond the current
  managed-fixture foundation.
- Implement production quota enablement/enforcement for `/home`, `/data`, and
  `/scratch` after mount/quota policy signoff.
- Decide whether `/data` nondurable-storage acknowledgment should become an
  install/apply gate.
- Turn scratch cleanup from fixture-only reviewed deletion into a production
  reviewed deletion mode, if approved.
- Add install/apply preflight integration for scratch health checks if desired.

### Priority 3: Login And GPU Isolation

- Generalize fixture-scoped login slices to all managed non-admin users, then
  optionally to all local non-admin users after safety review.
- Harden cgroup GPU denial across multiple concurrent users and multi-GPU
  hosts; keep the ACL fallback available but disabled unless needed.
- Add wrappers for additional direct GPU tools beyond `nvidia-smi`.
- Improve root/service GPU status mapping for multi-GPU jobs and exact device
  allocation.

### Priority 4: Inactive Lifecycle

- Add service/admin archive QoS and archive job submission.
- Add backup/replication hook runner and failure handling.
- Broaden fixture-only inactive apply into a production-reviewed workflow after
  backup hooks and non-fixture controls are ready.
- Add backup-failed/retry substates, permanent tombstone migration controls,
  and broader restore/rollback documentation.

### Priority 5: GPU Production Readiness And Docs

- Implement full GPU health gate: GRES, device files, NVML/CUDA ordering,
  topology if pinned, status mapping, login denial, and Slurm job access.
- Implement CPU-only recovery overlay for GPU failures.
- Add CUDA/toolkit/module validation and smoke-check workflow.
- Expand `user-kit/` into profile-accurate production docs and examples.
- Validate DGX/V100 render and behavior parity before replacing Tesla tooling.
