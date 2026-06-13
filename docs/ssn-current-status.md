# SSN Current Implementation Status

Audit date: 2026-06-13

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
`job_submit.lua`. The next safety round added Slurm `cli_filter/lua`,
structured GPU verification, and fixture-scoped CPU-only GPU recovery; live
testing verified ordinary and absolute `/usr/bin/sbatch --no-requeue`
rejection, script `#SBATCH --no-requeue` rejection, healthy GPU verification,
fixture GPU job hold/cancel during recovery, temporary CPU-only Slurm service,
GPU job rejection during recovery, GPU restoration, and final CPU/GPU/scratch
regression tests. The production safety bridge then added inactive archive
backup hooks, archive service account/QoS foundations, managed-user allowlist
login/GPU isolation, tokenized retention cleanup for SSN test artifacts only,
and a `cli_filter.lua` fix for this Slurm build's `no-requeue` option value.
Live testing verified no-hook inactive apply refusal without consuming the
token, failing-hook `backup_failed` state, successful-hook `backup_complete`
and tombstone state for `ssn-test-inactive-prod`, allowlist login/GPU isolation
for `ssn-test-standard` and `ssn-test-priority`, SSH direct GPU denial with
Slurm GPU access by the same user, tokenized retention deletion of only
`ssn-test-*` artifacts, and final CPU/GPU/scratch regression smoke. The storage
quota round then added token-gated quota enablement, enabled user/group quotas
on the Quadro `/`, `/data`, and `/scratch` ext4 mounts, applied tiny fixture
quotas to `ssn-test-quota`, verified over-quota writes fail on home/data/scratch,
and re-ran drained install plus CPU/GPU/scratch/no-requeue/GPU-recovery
regression smoke. That round also found and fixed a fixture UID/GID tombstone
reuse bug during live testing.

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
cgroup limits, hard direct-GPU denial for fixture login sessions, managed-user
allowlist rollout controls, root GPU status snapshots, Slurm client-side
no-requeue filtering, structured GPU verification, fixture-scoped CPU-only
recovery, fixture-tested inactive backup hooks, and token-gated filesystem
quota enablement on the disposable Quadro host. User sync is now
current-state-aware enough for the managed Quadro fixtures to produce a clean
no-op dry-run after repair, and the inactive lifecycle has a fixture-scoped
end-to-end implementation with reviewed local-only archive tokens,
backup-hook-gated archive removal, and UID/GID reactivation checks.

The largest remaining gaps are real-user quota rollout beyond fixture testing,
production-wide login/GPU isolation beyond fixtures, multi-GPU topology/NVML/CUDA
ordering health gates, production inactive archive jobs through Slurm service
QoS, real external backup hook integration beyond dummy fixture hooks,
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
| Plan artifacts live under `/var/lib/slurm-single-node/plans`, mode `0750/0640`, retained 90 days. | Install and apply reports plus rendered artifacts are written under protected per-run plan dirs; production retention remains report-only. A new tokenized retention cleanup command can delete only explicit SSN test artifacts; live Quadro deleted `/tmp/ssn-test-*` candidates and skipped a production-looking directory. | Implemented partially | `ssn/install.py`, `ssn/cli.py`, `ssn/safety.py`, live Quadro retention test | Implement production retention deletion only after explicit policy signoff. |
| Redaction classes for secrets, keys, emails, manifests. | Central redaction helpers exist; install reports redact sensitive key names; SSH user plans show labels/fingerprints; DB secret Ansible tasks use `no_log`. Inactive manifests are protected root/admin-readable plan artifacts, but full terminal path minimization for private manifests is still basic. | Implemented partially | `ssn/safety.py`, `ssn/users.py`, tests, live user and inactive dry-runs | Extend redaction/path minimization to future production prune/archive manifests and all plan artifact writers. |
| Risky operations require reviewed plan id/hash tokens. | Implemented for queued-jobs risk on install/apply, fixture-only scratch cleanup deletion, fixture inactive local-only archive apply, backup-hooked fixture inactive archive apply, fixture CPU-only GPU recovery, and test-artifact retention deletion. Tokens are config/input/operation-hash-bound where available, expiring, stored hashed, and single-use. Live tests covered no-hook pre-consumption refusal, hook-backed inactive apply, token reuse rejection, wrong-plan token rejection, and operation-hash mismatch rejection. | Implemented partially | `ssn/ops.py`, `ssn/install.py`, `ssn/cli.py`, `ssn/storage.py`, `ssn/users.py`, `ssn/gpu.py`, `ssn/safety.py`, `bin/ssn-plan-token`, live Quadro token tests | Extend the same token pattern to non-fixture production retention deletion and non-fixture inactive removals when approved. |
| Persist resolved audit file at `/etc/slurm-single-node/config.yml`. | Implemented by base role. | Implemented correctly | `ansible/roles/ssn_base/tasks/main.yml` | None. |

## Target Scope

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Ubuntu 24.04 and 26.04 primary, capability-gated. | Ansible asserts Ubuntu major version >= 24 and cgroup v2; shared capability probes record command, package, runtime, mount, free-space, Slurm, accounting, Lua, and NVIDIA details. Apply/install fail on missing required commands, cgroup v2, storage mount/write checks, apply-time accounting access, and whole-GPU basics. | Implemented partially | `ansible/site.yml`, `ssn/install.py`, `ssn/ops.py`, live install/apply reports | Add deeper probes for NVML/CUDA ordering, MIG/MPS/shared GPU modes, and package/runtime incompatibilities. |
| Use Ubuntu apt packages. | Installer and Ansible use apt packages. | Implemented correctly | `ssn/install.py`, `ansible/roles/ssn_base/tasks/main.yml` | Add unattended-upgrade protection for Slurm packages. |
| Target cgroup v2 only. | Installer and Ansible fail if cgroup fs is not `cgroup2fs`; Slurm cgroup config uses v2. | Implemented correctly | `ssn/install.py`, `ansible/site.yml`, `cgroup.conf.j2` | None. |
| NVIDIA whole-GPU and CPU-only only; MIG/MPS/shared fail closed unless supported. | Profiles encode fail-closed modes; feature gates and GPU verification check expected whole-GPU count/device files and fail closed if MIG or MPS appears enabled on a fail-closed profile. Shared-GPU detection is still basic. | Implemented partially | `profiles/generic-gpu.yml`, `ssn/ops.py`, `ssn/gpu.py`, live Quadro `ssn-verify` | Deepen shared-GPU/MPS/MIG detection across more NVIDIA generations. |
| Validate driver, do not install it. | Installer/Ansible require `nvidia-smi` for GPU profiles; no driver install role. | Implemented correctly | `ssn/install.py`, `ansible/site.yml` | Add clearer driver/toolkit diagnostic output. |
| GPU mapping verification as boot/apply health gate. | Structured GPU verification now checks `nvidia-smi`, `/dev/nvidiaN`, rendered/installed `gres.conf`, Slurm node GRES, GPU status snapshot freshness, Slurm GPU job mapping when active GPU jobs exist, and fail-closed MIG/MPS/shared indicators. Install/apply and `ssn-verify` consume the report. Single-GPU Quadro passed live; full NVML/CUDA ordering, topology, and multi-GPU mapping remain incomplete. | Implemented partially | `ssn/gpu.py`, `ssn/ops.py`, `ssn/cli.py`, `ssn/install.py`, live Quadro `ssn-verify` and install report | Add multi-GPU topology/order verification and broader hardware coverage. |
| CPU-only recovery overlay on GPU verification failure. | Fixture-scoped recovery exists via `ssn-gpu-recovery plan/enter/exit/status`. Live Quadro recovery used a reviewed token, held a pending fixture GPU job, canceled a running fixture GPU job, applied `cpu-bisect-node0`, kept CPU jobs running, rejected GPU jobs while CPU-only, restored `gpu-bisect-quadro-p620`, released held fixture work, and returned to healthy GPU service. Automatic recovery on verification failure and production/non-fixture recovery are not enabled. | Implemented partially | `ssn/gpu.py`, `ssn/cli.py`, `bin/ssn-gpu-recovery`, live Quadro recovery test | Wire recovery as an optional operator workflow for real GPU failures, then add non-fixture policy controls. |
| Apptainer optional/off by default. | Policy marks off; roots are created. No install/config workflow. | Implemented partially | `policies/modules.yml`, `ssn_modules` role | Add optional Apptainer management if profile enables it. |

## Commands And Compatibility

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Default command prefix is `ssn-*`. | Implemented for repo and installed wrappers. | Implemented correctly | `bin/ssn-*`, `ansible/roles/ssn_admin_tools/tasks/main.yml` | Make prefix fully configurable for all wrappers if non-ssn prefix is needed. |
| Core helper commands exist. | All listed commands exist, plus installer, scratch cleanup, retention cleanup, storage quotas, scratch health, login isolation/status, GPU collector, and GPU recovery commands. `ssn-archive-status` reports archive state, local-only status, backup status, hook path/rc, errors, and next action. | Implemented correctly | `bin/`, `ssn/cli.py`, live Quadro archive/retention/quota tests | Expand archive status once Slurm-submitted service archive jobs exist. |
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
| UID/GID auto with explicit override support. | User creation supports explicit UID/GID; otherwise SSN now allocates explicit free IDs while excluding current system IDs and tombstoned IDs. Explicit ID conflicts and explicit tombstone reuse are validated. | Implemented correctly | `ssn/users.py`, tests, live Quadro `ssn-test-quota` repair | Broaden adoption-plan UX. |
| Inactive reactivation must reuse original UID/GID. | Planning validates this case, and live Quadro reactivation without explicit IDs was rejected after tombstoning. Reactivation with original UID/GID recreated `ssn-test-inactive` and restored `/data` ownership. | Implemented correctly | `ssn/users.py`, tests, live Quadro inactive/reactivation test | Broaden restore workflow beyond fixture testing. |
| Permanent UID/GID tombstones. | Fixture inactive apply records original UID/GID and reaches `archive_state: tombstoned` after account removal. Reactivation consumes the original IDs. The quota round found that auto-created users could reuse tombstoned IDs; SSN now rejects explicit tombstone reuse, flags existing users occupying tombstoned IDs, and auto-allocates around tombstones. Production admin migration/clear controls remain incomplete. | Implemented partially | `ssn/users.py`, tests, live Quadro `ssn-test-inactive-prod` and `ssn-test-quota` repair | Add admin migration/clear workflow and broader restore/conflict tests. |
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
| `job_submit.lua` is fast gate for explicit request fields. | Lua gate checks explicit QoS, CPU, GPU, scratch health, and retains a no-requeue check where Slurm exposes the field. Slurm `cli_filter/lua` now handles client-side no-requeue rejection for `sbatch`/`srun`/`salloc`, with the PATH wrapper kept as a friendly fallback. Slurm documents `cli_filter` as bypassable with alternate client config, so it is not treated as security-hard. | Implemented differently | `job_submit.lua.j2`, `cli_filter.lua.j2`, `sbatch-wrapper.j2`, Slurm cli_filter docs, live Quadro no-requeue tests | Keep controller-side/QoS enforcement as final authority; revisit only if a stronger server-side no-requeue field becomes available. |
| Reject over-tier CPU/GPU/RAM/walltime, unsafe GPU syntax, no-requeue. | CPU and GPU over-limit rejections passed via Slurm/QoS. Ordinary `sbatch --no-requeue`, absolute `/usr/bin/sbatch --no-requeue`, and a script with `#SBATCH --no-requeue` all rejected live after enabling `cli_filter/lua`. RAM/walltime/unsafe syntax coverage is still incomplete. | Implemented partially | `job_submit.lua.j2`, `cli_filter.lua.j2`, `sbatch-wrapper.j2`, live Quadro fixture jobs | Add RAM/walltime/unsafe syntax checks and keep documenting cli_filter bypass limitations. |

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
| Reject normal-user `--no-requeue`. | Implemented through `cli_filter/lua` plus the existing wrapper and best-effort `job_submit.lua`. Live testing showed ordinary `sbatch --no-requeue`, absolute `/usr/bin/sbatch --no-requeue`, and script `#SBATCH --no-requeue` all reject while normal jobs still run. The wrapper also rejects `--requeue=0`; this Slurm build normalizes absolute `/usr/bin/sbatch --requeue=0` to a positive `requeue` value before Lua can distinguish the original spelling. This is client-filter enforcement, not a security boundary against alternate Slurm client config. | Implemented differently | `cli_filter.lua.j2`, `slurm.conf.j2`, `sbatch-wrapper.j2`, `job_submit.lua.j2`, live Quadro no-requeue regression test | Document bypass/normalization caveat and keep `job_submit.lua` reinforcement. |
| Interactive `srun` allowed but canceled on preemption. | Docs mention interactive examples; no explicit cancellation policy code beyond Slurm preemption mode. | Yet to be implemented | `user-kit/examples/20-interactive-srun.sh` | Validate actual Slurm behavior and document exactly. |
| Teach checkpointing. | User docs are basic; checkpointing guidance is not production-grade. | Yet to be implemented | `user-kit/README.md` | Expand docs/examples. |

## Login Policy

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Constrained login default, not strict Slurm-only SSH. | Implemented as policy docs, MOTD/banner, shell defaults, and process limit. | Implemented differently | `ssn_user_policy` role | Add real cgroup login slice enforcement. |
| Per non-admin user login slice: 2 CPUs, 4 GB RAM, 128 tasks, low I/O weight. | Implemented and live-tested for active managed fixtures with per-user `user-<uid>.slice` drop-ins: `CPUQuota=200%`, `MemoryMax=4GB`, `TasksMax=128`, and `IOWeight=50`. The command now supports `fixture_only`, explicit `managed_allowlist`, and `all_managed_non_admin` target scopes. Live Quadro allowlist applied only `ssn-test-standard` and `ssn-test-priority`, then rolled back to reset controls. Slurm CPU/GPU jobs by the same user stayed under `slurmstepd` cgroups. Production-wide non-admin rollout is not enabled. | Implemented partially | `ssn/login.py`, `ssn-login-isolation`, live Quadro allowlist/login/Slurm tests | Generalize beyond fixtures/allowlists only after production rollout review. |
| Admin users exempt. | Fixture-scoped login isolation excludes configured admins; root/admin Slurm operation remains unaffected. Production-wide cap exemption still needs rollout testing. | Implemented partially | `ssn/login.py`, profiles, live Quadro tests | Re-test when applying to all managed/non-admin users. |
| Hard non-Slurm GPU denial through cgroup v2 devices. | Implemented and live-tested for managed fixture login sessions in cgroup mode. SSH as `ssn-test-standard` landed in `user-1003.slice`; absolute `/usr/bin/nvidia-smi` failed with NVML error, while `sbatch --gres=gpu:1 /usr/bin/nvidia-smi` by the same user succeeded under Slurm cgroups. The latest allowlist test verified direct denial, Slurm GPU success, and root/admin direct GPU access. Production-wide denial is not enabled. | Implemented partially | `ssn/login.py`, per-user slice drop-ins, live Quadro direct-denial and Slurm GPU tests | Generalize scope and add multi-user/multi-GPU tests. |
| Friendly PATH wrappers for direct GPU tools. | `nvidia-smi` wrapper is installed on GPU profiles. It allows root/admins and Slurm jobs through to `/usr/bin/nvidia-smi`; ordinary login use gets a friendly Slurm-only message. Other GPU tools are not wrapped yet. | Implemented partially | `nvidia-smi-wrapper.j2`, live Quadro wrapper test | Add wrappers for additional GPU tools as needed. |
| Profile-prefixed GPU status wrapper with 10s root snapshot and Slurm job mapping. | `ssn-gpu-status` now reads a root/service snapshot refreshed by `ssn-gpu-status.timer`; the collector includes utilization, memory, temperature, identity, and Slurm job/user mapping. Live single-GPU Quadro mapping worked for a running fixture GPU job. Multi-GPU exact device mapping remains basic. | Implemented partially | `ssn/login.py`, `ssn-gpu-collector`, admin tools role, live Quadro snapshot test | Harden multi-GPU mapping and stale/failure reporting. |
| No process-policing daemon in v1. | No policing daemon exists. | Implemented correctly | Repo inspection | None. |

## Storage Policy

| Decision / Requirement | Current Code State | Status Bucket | Evidence | Follow-up Needed |
|---|---|---|---|---|
| Optional `/home`, `/data`, `/scratch` per profile. | Implemented policies for no-scratch and three-area layouts. | Implemented correctly | `policies/storage.yml`, profiles | None. |
| `/home` persistent path. | Installer allows `/home` to be a directory on `/` for the test host. | Implemented differently | `ssn/install.py` | Document this as an allowed dev/test compromise. |
| RAID0 `/data` persistent but not durable, external backup required. | Policy records it; enforcement/acknowledgment is not active. | Yet to be implemented | `policies/storage.yml` | Add validation requiring site acknowledgment for nondurable data. |
| Admins provision filesystems; automation verifies mounts. | Installer/verify checks `/data` and `/scratch` mounts for scratch profiles; quota status now records mountpoint, source, fstype, options, fstab entry, quota files, `quotaon`, `repquota`, and `setquota` evidence for home/data/scratch. | Implemented correctly | `ssn/install.py`, `ssn/cli.py`, `ssn/storage.py`, storage role, live Quadro quota status | Add deeper filesystem type/free-space thresholds if needed. |
| Quota-managed home/data/scratch and quota capability validation. | `ssn-storage-quotas plan/enable/status` can token-gate quota enablement, back up `/etc/fstab`, add `usrquota,grpquota`, remount ext4 mounts, run `quotacheck`, and enable quotas. Live Quadro enabled quotas on `/` for `/home`, plus `/data` and `/scratch`; install/apply gates now fail quota-required profiles when required user quotas are inactive. Fixture quota apply supports tiny overrides and was live-tested for `ssn-test-quota` only. Real-user quota rollout is not enabled. | Implemented partially | `ssn/storage.py`, `ssn/cli.py`, `ssn/ops.py`, `bin/ssn-storage-quotas`, live Quadro quota enable/enforcement test | Add real-user quota policy rollout and decide production quota values. |
| `/data` and `/scratch` capacity isolation. | Quadro live profile now has separate mounted filesystems with active quotas on `/data` and `/scratch`. SSN reports mount identity, but it does not yet enforce a general capacity-isolation policy for every production profile or same-pool project-quota design. | Implemented partially | live Quadro `findmnt`, `ssn-storage-quotas status`, `policies/storage.yml` | Add production validation for separate filesystems/LVs or approved same-pool quota design. |
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
| Kill jobs, lock data, prune, archive, backup hook, remove account after success. | Fixture local-only path is implemented, and the backup-hooked fixture path now works for `ssn-test-inactive-prod`: no-hook apply refused before consuming the token; a failing dummy hook recorded `backup_failed` while keeping the account locked/present; a succeeding dummy hook recorded `backup_complete`, removed the Unix account, locked `/data/$USER` as root `0700`, and tombstoned the user. Non-fixture production removal is still not enabled. | Implemented partially | `ssn/users.py`, `ssn/cli.py`, live Quadro inactive hook tests | Add real external backup hooks and non-fixture controls before real users. |
| Archive job under service/admin Slurm identity and protected QoS. | An archive Slurm account and `archive-admin` QoS are now created by Ansible and live-visible in `sacctmgr`. Fixture archive orchestration still runs directly as root/admin during `ssn-sync-users --apply`; it does not yet submit a monitored Slurm archive job. | Implemented partially | `ansible/roles/ssn_slurm_config/tasks/main.yml`, live Quadro `archive-admin` QoS/account | Add monitored Slurm archive job submission under the service account/QoS. |
| Archive root required and Slurm unavailable blocks transition. | Archive root is required by the fixture apply path and `/data/_archive` was used live. Slurm job cancellation/association commands run during apply, but a full "Slurm unavailable blocks transition" preflight is not complete. | Implemented partially | `ssn/users.py`, live Quadro archive path | Add explicit Slurm health gate to inactive transition planner. |
| Prune allowlist, symlink safety, report-only build trees. | Implemented for the fixture manifest/apply path: fixed allowlisted paths, marker-detected venv/conda envs, symlink remove-link-only handling, and report-only build trees. Live manifest found delete and report-only candidates. | Implemented partially | `ssn/users.py`, `tests/test_users.py`, live Quadro inactive plan | Broaden tests and keep production apply disabled until policy signoff. |
| Dry plan writes plan id/hash and real apply requires token. | Implemented for inactive fixture lifecycle. Dry-run writes a protected plan with `operation_hash` bound to the desired lifecycle and observed hook set. Apply accepts either `inactive_archive_apply` for the backup-hooked path or `inactive_local_only_archive` for reviewed local-only override. Live tests covered no-hook pre-consumption refusal, hook-backed apply, token reuse failure, and wrong-plan token failure. | Implemented correctly | `ssn/cli.py`, `ssn/users.py`, `ssn-plan-token`, live Quadro inactive plan/token tests | Reuse for non-fixture production inactive removal once approved. |
| Backup hooks after local archive. | Implemented for fixture lifecycle with hook directory `/etc/slurm-single-node/archive-hooks.d`, executable hook discovery, environment variables, timeout, stdout/stderr capture, `backup_failed`/`backup_complete` state, retry by rerunning with a new reviewed plan/token, and `ssn-archive-status` reporting. Live Quadro used disabled dummy hooks after testing so no executable test hook remains active. | Implemented partially | `ssn/users.py`, `ssn/cli.py`, `policies/storage.yml`, live Quadro failing/success hook tests | Integrate real external backup/replication hooks and production approvals. |
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
| `users-state.yml` state shape with archive states. | State validation accepts archive fields plus backup status metadata. Fixture inactive lifecycle records archive path, operation hash, local-only flag, backup required/status/hook/rc/output, original UID/GID, `backup_failed`, `backup_complete`, `removal_ready`, and `tombstoned`. Production tombstone migration controls remain incomplete. | Implemented partially | `ssn/users.py`, tests, live Quadro `ssn-test-inactive-prod` state | Add production tombstone migration controls and broader restore/rollback workflows. |
| Profile binding shape with services, admins, operations. | Profiles match the broad shape. | Implemented correctly | `profiles/*.yml` | Add strict schema and migrations. |
| `policies/slurm-core.yml` shape. | Policy exists and drives render. | Implemented correctly | `policies/slurm-core.yml`, templates | Add validation for unsupported fields. |
| `policies/tiers.yml` shape. | Policy exists and drives QoS/rendered tiers. | Implemented correctly | `policies/tiers.yml`, `ssn/config.py` | Add tests for all tier variants. |
| `policies/storage.yml` shape. | Policy exists; directory creation, per-job scratch, scratch health marker gating, token-gated quota enablement, fixture quota apply, fixture-only cleanup apply, archive hook directory, and fixture backup-hook lifecycle are active. Real-user quota rollout and production cleanup deletion remain incomplete. | Implemented partially | `policies/storage.yml`, `ssn/storage.py`, `ssn/users.py`, storage role, live Quadro quota test | Implement real-user quota rollout/cleanup enforcement and Slurm-submitted archive jobs after policy signoff. |
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
| GPU verification tests include login denial and Slurm GPU access. | Fixture-scoped login denial and Slurm GPU access by the same user were live-tested on Quadro in the prior round. This round added structured GPU health verification and CPU-only recovery testing. Multi-GPU mapping and production-wide rollout remain incomplete. | Implemented partially | `ssn/gpu.py`, live Quadro login/GPU tests, `ssn-verify`, recovery test | Add multi-GPU verification suite and production rollout tests. |
| Scratch-unhealthy, archive, hook, tombstone tests. | Scratch happy path, per-job cleanup, and unhealthy marker/job rejection were live-tested. Fixture inactive dry-plan, local archive, tombstone, token reuse, job cancellation, reactivation, no-hook refusal, failing backup hook, successful backup hook, and account-removal-after-backup were live-tested. Real external backup hooks remain untested. | Implemented partially | `ssn/storage.py`, `ssn/users.py`, live Quadro scratch and inactive lifecycle tests | Add real backup-hook success/failure tests before production inactive rollout. |
| Static tests. | Unit tests, compileall, shell syntax, render checks, `git diff --check`, and remote static tests pass. Tests now include schema validation, capability gates, cli-filter capability checks, drain wait timeout behavior, user-sync idempotence, quota report/enable/fixture-apply safety, tombstone-aware UID/GID allocation, scratch health, cleanup operation hashes, fixture cleanup safety, retention test-artifact cleanup safety, inactive plan reports, backup hook gating, prune manifests, fixture-limited inactive apply, GPU verification parsing, and recovery plan safety. | Implemented correctly | `tests/`, live session notes | Add CI entrypoint when repo is ready. |

## Implemented Extra

| Extra Item | Current Code State | Evidence | Keep / Follow-up |
|---|---|---|---|
| `gpu-bisect-quadro-p620` live profile. | Added for disposable Quadro test server. | `profiles/gpu-bisect-quadro-p620.yml` | Keep as useful hardware fixture. |
| `cpu-bisect-node0` profile. | CPU-only profile for same test host. | `profiles/cpu-bisect-node0.yml` | Keep for CPU-only recovery testing. |
| `starter-single-gpu-small` and `starter-cpu-small` policies. | Added for smaller machines. | `policies/tiers.yml` | Keep as practical starter tiers. |
| Single-command installer smoke-user association setup. | Installer can create/update smoke user Slurm association for tests. | `ssn/install.py` | Keep; make clear it is test/smoke behavior. |
| Automated install smoke rejection tests. | Installer tests over CPU and over GPU. `--no-requeue` is covered by explicit live regression tests for wrapper, absolute binary, and script directives rather than installer smoke. | `ssn/install.py`, `cli_filter.lua.j2`, `sbatch-wrapper.j2`, live Quadro notes | Expand installer smoke to RAM/walltime/preemption if desired. |
| `ssn-scratch-cleanup` report-only command. | Added with service/timer integration. | `ssn/cli.py`, `bin/ssn-scratch-cleanup` | Convert to reviewed deletion mode later. |
| `ssn-scratch-health` command. | Added for scratch health reports and unhealthy marker management; live tests verified healthy, unhealthy, submission block, and recovery paths. | `ssn/storage.py`, `bin/ssn-scratch-health`, live Quadro scratch health test | Keep; extend to install/apply preflight if desired. |
| Fixture-only scratch cleanup deletion. | Reviewed tokenized reports can delete only top-level `/scratch/ssn-test-*` candidates; live test deleted a fixture path and rejected token reuse/mismatch. | `ssn/storage.py`, `ssn-scratch-cleanup`, live Quadro cleanup test | Keep scoped to fixtures until production deletion is explicitly approved. |
| Token-gated storage quota enablement. | Added `ssn-storage-quotas plan/enable/status`; live Quadro used a reviewed `storage_quota_enable` token to back up and edit `/etc/fstab`, remount `/`, `/data`, and `/scratch`, run `quotacheck`, and enable user/group quotas. | `ssn/storage.py`, `ssn/cli.py`, `bin/ssn-storage-quotas`, live Quadro quota plan `/var/lib/slurm-single-node/plans/storage-quotas-20260613122307/storage-quota-plan.json` | Keep; add real production rollout controls before non-test systems. |
| Fixture quota apply path. | `ssn-sync-users --apply-fixture-quotas` can apply limits only to the fixture prefix and accepts tiny fixture quota overrides. Live Quadro applied home `64MB`, data `64MB`, and scratch `128MB` to `ssn-test-quota`; small writes passed and over-quota writes failed. | `ssn/storage.py`, live Quadro quota enforcement test | Keep fixture-scoped until real-user quota policy is approved. |
| Tombstone-aware auto UID/GID allocation. | Added after live quota testing found `ssn-test-quota` auto-reused tombstoned UID/GID `1006/1012`; code now avoids tombstoned IDs and validates conflicts. The fixture was repaired to UID/GID `1007/1013`. | `ssn/users.py`, tests, live Quadro repair | Add production tombstone migration/clear workflow. |
| Managed fixture users on Quadro. | `ssn-test-standard`, `ssn-test-priority`, `ssn-test-suspended`, reactivated `ssn-test-inactive`, and quota fixture `ssn-test-quota` remain on the test host for repeat sync/idempotence/lifecycle/storage tests. | live Quadro users.yml/state | Keep as disposable test fixtures; do not treat as production users. |
| Report-only retention helpers. | Plan and user-backup retention candidates are reported without deleting files. | `ssn/safety.py`, `ssn/install.py`, `ssn/cli.py` | Convert to approved deletion only after policy signoff. |
| Tokenized test-artifact retention cleanup. | Added `ssn-retention-cleanup`: reports old items under a selected root, requires a `retention_delete` plan token for apply, deletes only direct-child candidates named like SSN test artifacts, skips symlinks and production-looking paths, and rejects token reuse/wrong-plan tokens. Live Quadro used `/tmp/ssn-retention-bridge-root`. | `ssn/safety.py`, `ssn/cli.py`, `bin/ssn-retention-cleanup`, live Quadro retention test | Keep production retention deletion disabled until explicitly approved. |
| `ssn-plan-token` risk-token helper. | Added as an admin command for creating reviewed, short-lived tokens from install/apply reports. | `bin/ssn-plan-token`, `ssn/ops.py`, `ssn/cli.py`, live Quadro token test | Extend beyond queued-job risk as future risky workflows are implemented. |
| Explicit drain workflow. | `ssn-install` and `ssn-apply --run` support `--drain`, `--drain-timeout`, and `--drain-reason`; live tests covered success, timeout safe resume, and drained reinstall idempotence. | `ssn/install.py`, `ssn/cli.py`, `ssn/ops.py`, live Quadro reports | Keep; consider pending-job hold semantics only if future workflows need it. |
| Fixture-scoped login isolation commands. | Added `ssn-login-isolation` and `ssn-login-status` for cgroup/ACL/disabled modes and per-user slice reporting. Live cgroup mode succeeded; ACL fallback code exists but was not needed live. | `ssn/login.py`, `bin/ssn-login-isolation`, `bin/ssn-login-status`, live Quadro tests | Keep fixture-scoped until wider rollout is approved. |
| Root GPU status collector. | Added `ssn-gpu-collector` plus `ssn-gpu-status.service/timer`; live snapshot and single-GPU job mapping passed. | `ssn/login.py`, `bin/ssn-gpu-collector`, admin tools role, live Quadro tests | Harden multi-GPU mapping. |
| Fixture inactive lifecycle. | Added token-gated inactive dry-plan/apply for fixtures, including prune manifest, local archive, backup-hooked archive path, backup failure/success states, tombstone, and reactivation validation. Live Quadro tested both `ssn-test-inactive` and `ssn-test-inactive-prod`. | `ssn/users.py`, `ssn/cli.py`, live Quadro inactive tests | Keep non-fixture production removal disabled until real backup hooks and approvals are ready. |
| Slurm `cli_filter/lua` no-requeue filter. | Added managed `CliFilterPlugins=cli_filter/lua`, `CliFilterParameters=cli_filter_lua_path=/etc/slurm/cli_filter.lua`, and `cli_filter.lua` to reject no-requeue client options and script directives. Live tests verified absolute `/usr/bin/sbatch --no-requeue` and script `#SBATCH --no-requeue` rejection. This Slurm build normalizes absolute `/usr/bin/sbatch --requeue=0` to `requeue`, so that exact spelling is only rejected by the managed wrapper, not by the absolute binary. | `policies/slurm-core.yml`, `slurm.conf.j2`, `cli_filter.lua.j2`, live Quadro no-requeue tests | Keep the documented Slurm bypass/normalization caveat in admin/user docs. |
| Managed `sbatch` no-requeue wrapper. | Kept `/usr/local/bin/sbatch` wrapper as friendly fallback after adding `cli_filter/lua`; it rejects `--no-requeue`, script directives, and `--requeue=0/no/false`. Live Quadro confirmed wrapper `--requeue=0` rejection after finding the absolute-binary normalization limitation. | `sbatch-wrapper.j2`, `cli_filter.lua.j2`, live Quadro no-requeue test | Keep unless broader Slurm client filtering makes it unnecessary. |
| Fixture CPU-only GPU recovery. | Added `ssn-gpu-recovery plan/enter/exit/status`. Live recovery held pending fixture GPU job 57, canceled running fixture GPU job 56, applied CPU-only profile with `Gres=(null)`, ran CPU job 58, rejected GPU submission, restored GPU profile, completed GPU job 59, and ended with healthy GPU verification. | `ssn/gpu.py`, `ssn/cli.py`, `bin/ssn-gpu-recovery`, live Quadro recovery test | Keep fixture-scoped until production policy controls are added. |

## Next Implementation Queue

### Priority 1: Safety And Correctness Gates

- Extend the reviewed-token pattern from fixture/test-artifact applies to
  non-fixture production inactive removal and production retention deletion
  only after explicit approval.
- Deepen capability gates for MariaDB access modes, Lua plugin compatibility,
  NVML/CUDA ordering, multi-GPU topology, shared GPU detection, and
  package/runtime compatibility.
- Decide whether the Slurm `cli_filter/lua` no-requeue caveat is acceptable for
  production, given Slurm documents client filters as bypassable with alternate
  client configuration.
- Add schema migration handling if/when `schema_version` moves beyond v1.
- Add approved pruning for old plan/user-backup artifacts beyond SSN test
  artifacts if retention should delete rather than report.

### Priority 2: User And Storage Foundations

- Broaden user sync adoption UX and conflict validation beyond the current
  managed-fixture foundation.
- Decide real-user quota rollout semantics, production quota values, and whether
  quota apply should move from fixture-only to all managed active users.
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
  allocation; re-test CPU-only recovery on a multi-GPU host.

### Priority 4: Inactive Lifecycle

- Add monitored Slurm archive job submission under the service/admin archive
  account and QoS.
- Replace dummy fixture backup hooks with real site backup/replication hooks
  and live-test success/failure behavior.
- Broaden fixture-only inactive apply into a production-reviewed workflow after
  real backup hooks and non-fixture controls are ready.
- Add backup-failed/retry substates, permanent tombstone migration controls,
  and broader restore/rollback documentation.

### Priority 5: GPU Production Readiness And Docs

- Extend the GPU health gate to NVML/CUDA ordering, topology if pinned,
  multi-GPU status mapping, login denial evidence, and Slurm job access.
- Broaden CPU-only recovery from fixture-scoped operator workflow to a
  production-reviewed workflow for real GPU failures.
- Add CUDA/toolkit/module validation and smoke-check workflow.
- Expand `user-kit/` into profile-accurate production docs and examples.
- Validate DGX/V100 render and behavior parity before replacing Tesla tooling.
