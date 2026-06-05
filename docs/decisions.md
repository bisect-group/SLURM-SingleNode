# Planning Decisions

This file records decisions made before implementation work begins. Update it during the review loop when desired behavior changes.

## Generalization Model

Use hardware/site profiles as the main generalization mechanism.

A profile is a reviewed configuration for one kind of single-node server. It selects or defines cluster identity, inventory target, hardware resources, GPU behavior, partition names, storage features, module policy, documentation labels, and the policy templates used by users.

Initial profiles should cover:

- The existing DGX/V100 host as `dgx-v100`.
- A generic NVIDIA GPU server.
- A CPU-only server.

The current CPU-only machine is the development and test target. It should be represented by the CPU-only profile and can be used to prove the generalized installer locally before GPU rollout.

The NVMe plus six-SATA RAID0 machine is the first target generic NVIDIA GPU server profile. It is not the CPU-only development machine.

The first implementation should do a moderate reorganization: keep the useful Ansible role/tooling ideas, but rename and generalize directories, templates, variables, docs, and scripts. Do not do a clean rewrite unless the existing structure blocks the implementation.

The generalized Ansible installer should live under `ansible/`. Researcher-facing docs and examples should live under `user-kit/`. The existing Tesla behavior should be preserved as the `dgx-v100` profile with optional `tesla-*` aliases, not as generic naming.

## Repository And Config Layout

Use `profiles/` plus domain policy files under `policies/`.

- `profiles/` contains machine profiles such as `dgx-v100.yml`, `generic-gpu.yml`, and `cpu-only.yml`.
- `policies/` contains reusable domain files such as `slurm-core.yml`, `tiers.yml`, `storage.yml`, `modules.yml`, `cache.yml`, `login.yml`, and `defaults.yml`.
- Machine profiles remain separate from user policy templates.
- Profiles bind named policy files and may add small profile-local overrides.
- Admins select a profile explicitly with a command such as `ansible-playbook ... -e profile=generic-gpu`.
- Hardware discovery generates a draft profile YAML for admin review; discovery output is not applied directly.
- Policy/profile inheritance uses deep merge semantics: nested maps merge, while lists replace unless a schema field explicitly supports extension.
- Human-readable units are allowed in authored YAML, for example `100GB`, `48h`, and `30d`. Validation should normalize them before rendering templates.

Use a light-dependency Python resolver, with apt-installable dependencies such as PyYAML, to merge and validate profiles, policies, and users before Ansible applies anything. Ansible should consume the resolved output rather than reimplementing schema validation in templates.

Validation must be strict. If the selected profile, policies, mounts, users, tiers, quotas, or limits are inconsistent, the playbook should fail before touching the system.

YAML schemas are strict by default. Unknown fields fail validation, each authored file carries an explicit `schema_version`, and future schema migrations should be rendered as dry-run migration plans for admin review before any YAML is rewritten.

Dry-run/check mode should produce a full plan: validate config and show intended filesystem, user, and Slurm changes without applying them. It should print readable text and also write a machine-readable JSON plan artifact.

Plan artifacts live under `/var/lib/slurm-single-node/plans`, are retained for 90 days by default, and are protected as root-owned files group-readable by `slurm_admins`: directories `0750`, files `0640`.

Plan artifacts must redact sensitive values and use fingerprints where possible instead of raw secrets. Exact v1 redaction classes:

- Secrets are hidden in terminal output and plan JSON.
- SSH public keys are shown as labels and fingerprints, not full key blobs.
- Emails are masked in terminal output but retained in protected root-only JSON.
- Sensitive prune/archive manifests are path-limited in terminal output and root-only when full paths or filenames may reveal private data.

Risky operations require a reviewed plan id/hash token before apply. This includes inactive-user prune/archive workflows and production service-changing apply operations. Low-risk validation and read-only status commands do not require plan tokens. Risky apply tokens are bound to the input hashes used to create the plan, expire, and are single-use.

The target should get a resolved audit file at `/etc/slurm-single-node/config.yml` containing the final merged profile, policy, and override values.

## Target Scope

Primary OS targets are Ubuntu 24.04 and Ubuntu 26.04. Ubuntu 22.04 can remain best-effort legacy support when it does not force awkward branching.

Support is capability-gated, not release-name-only. Validation must check that the installed OS/package stack supports the required Slurm, MariaDB, Lua submit plugin, cgroup v2, NVIDIA/NVML, and optional feature behavior for the selected profile.

Use Ubuntu apt packages as the default Slurm package source. Slurm package updates are admin-run only; do not let unattended upgrades silently update active Slurm services.

Target cgroup v2 only in v1. Validation should fail on systems that are not running unified cgroup v2.

GPU support in v1 is NVIDIA whole-GPU allocation plus CPU-only. AMD and Intel GPU support are out of scope for v1. NVIDIA MIG, MPS, and shared GPU modes fail closed unless a profile explicitly declares support and supplies matching GRES/accounting behavior.

For NVIDIA GPU profiles, admins install the NVIDIA driver before running this automation. The automation validates `nvidia-smi`, GPU count, and Slurm GRES wiring, but does not install or own the driver lifecycle in v1.

GPU mapping verification is a boot-and-apply health gate. Verify `gres.conf`, device files, NVML ordering, CUDA ordering, status-wrapper mapping, and declared GPU topology/affinity after boot and after apply before marking a GPU profile healthy. GPU topology uses a discover-review-pin model: discovery may report socket, NUMA, L3, and core affinity, but render `gres.conf Cores=` only from reviewed profile data.

If verification fails on a mixed CPU/GPU node, use `cpu_only_recovery`: mark GPU resources unhealthy, drain or cancel running GPU jobs with warning, hold pending GPU jobs, apply a temporary CPU-only Slurm overlay that removes GPU availability, and keep CPU jobs/service available where safe. The recovery state is a temporary overlay, not a mutation of the reviewed profile.

The automation should manage Apptainer only as an optional feature enabled by a profile, mainly for container-backed modules. Apptainer is off by default.

## Commands And Compatibility

Installed helper command names should use a configurable prefix from the profile.

- The default generalized command prefix is `ssn-*`.
- The DGX/V100 profile may install optional `tesla-*` compatibility aliases or symlinks for old muscle memory.
- Do not use `slurm-*` as the helper prefix, to avoid confusion with upstream Slurm commands.
- The v1 core helper commands are `ssn-discover`, `ssn-render`, `ssn-apply`, `ssn-sync-users`, `ssn-verify`, `ssn-gpu-status`, and `ssn-archive-status`.

The deployed user source of truth should live at a generic path: `/etc/slurm-single-node/users.yml`.

## Accounting

Keep `slurmdbd` and MariaDB accounting as the default.

Reason: the intended policy includes tiers, priorities, fairshare, preemption levels, compute slices, job limits, and historical reporting. Slurm accounting is the clean place to represent associations and QoS limits.

Use QoS as the tier authority in v1. Use one default Slurm account for all users; project/lab groups remain Unix-only metadata for now.

Use a strict Slurm resource model in v1:

- `SelectType=select/cons_tres`.
- `SelectTypeParameters=CR_CPU_Memory`.
- Consumable CPU, memory, and GRES resources.
- cgroup v2 enforcement for CPU cores, RAM, and devices.
- GPU TRES/accounting where the installed Slurm/NVIDIA/NVML stack supports it.
- Explicit tier limits rendered into Slurm/QOS/accounting settings rather than left as documentation-only policy.
- Slurm memory defaults are explicit profile values. Discovery may suggest `DefMemPerCPU`, `DefMemPerNode`, `MaxMemPerCPU`, `MaxMemPerNode`, and QOS memory values, but render fails until reviewed values are pinned. Tier memory percentages render as QOS/TRES maximum memory limits.

The `slurmdbd` MariaDB password should be generated once on the target and stored in a protected local file readable only by root/Slurm service users.

Create local daily MariaDB dumps of the Slurm accounting database with 30-day retention. External backup hooks are separate from this local accounting backup.

For single-node installs, generate a local Munge key if one is absent, enforce standard Munge ownership and permissions, back it up locally with other protected service secrets, and do not rotate it automatically. If an admin supplies an existing key, validation should verify ownership, permissions, and service health.

## User Policy Model

Use YAML, not CSV, for the future user source of truth. The deployed source of truth is `/etc/slurm-single-node/users.yml`.

- Existing CSV migration is not required because the CSV system has not been deployed in production.
- A bootstrap/discovery tool should scan local human users from `/etc/passwd`, home directories, and authorized keys into a draft `users.yml` for admin review.
- User discovery imports UID `1000-60000` users by default, excluding known service/admin accounts.
- User discovery imports all valid `authorized_keys` entries as labeled keys such as `imported-1`, `imported-2`, preserving key comments and OpenSSH key options where present.
- Discovered users default to `active` status and `standard` tier in the draft YAML.
- Only clearly named test users may be created/deleted during local implementation testing.
- The file has `schema_version: 1`.
- Users are keyed by username, not by a list item with a username field.
- The YAML file contains desired state, not runtime bookkeeping.
- Local runtime state is stored separately at `/var/lib/slurm-single-node/users-state.yml`, including original UID/GID values needed for inactive-user reactivation.
- Supported user statuses are `active`, `suspended`, and `inactive`.
- `users-state.yml` tracks inactive archive substates separately from desired user status: `archive_pending`, `archive_running`, `archived_local_only`, `backup_failed`, `backup_complete`, `removal_ready`, and final tombstone state.
- Active users require `tier` and `status`. Missing metadata such as full name, email, or SSH keys should warn, not fail.
- SSH keys are map objects keyed by label, not raw strings. Labels make later key removal and audit easier.
- SSH key objects may include structured OpenSSH `authorized_keys` options. Options discovered during import must be preserved and rendered back rather than stripped.
- Preserve SSH key options losslessly with raw-plus-parsed storage. The raw OpenSSH option prefix is authoritative for rendering; parsed metadata is for review and validation when possible.
- Dry-run plans should show SSH key labels and fingerprints, not full public key blobs.
- If a user has no SSH keys in YAML, warn and leave that user's `authorized_keys` unmanaged rather than deleting access unexpectedly.
- Missing or null `ssh_keys` means `authorized_keys` is unmanaged for that user. `ssh_keys: {}` means the user has an intentionally managed empty key set.
- If both `options_raw` and parsed `options` are present for an SSH key and they disagree, validation fails. Admins must fix the ambiguous key metadata before apply.
- Absent existing local users are report-only by default. The sync should not delete unmanaged accounts unless a future explicit cleanup mode is added.
- Existing unmanaged local users or groups that conflict with `users.yml` fail validation unless they are explicitly adopted into managed state by a reviewed plan.
- A user recorded in managed state but removed from `users.yml` should fail validation. Admins must mark users `inactive`; YAML deletion is not a lifecycle action.
- UID/GID values are auto-allocated by default. Existing local IDs should be preserved, and explicit `uid`/`gid` overrides are allowed only when needed for restore or migration.
- Reactivating an inactive user must reuse the original UID/GID recorded in state. Validation must fail if either value is unavailable or conflicts with another account.
- Inactive users' original UID/GID values become permanent tombstones after local account removal. Auto-allocation must never reuse tombstoned IDs unless an explicit admin migration clears the tombstone.
- The concrete v1 `users.yml` shape uses top-level `schema_version`, `groups`, and a keyed `users` map.
- Before tool-managed writes, back up `users.yml` and `/var/lib/slurm-single-node/users-state.yml` under `/var/backups/slurm-single-node/users` with 90-day retention.
- Each user's `groups` field is authoritative for project/lab membership. Top-level `groups` contains metadata only.
- Admin-exempt users are declared in profile/site config, not researcher `users.yml`.
- Apply and sync operations use staged state-machine reconciliation. Before managed writes, create backups and record enough local state to resume, repair, or report incomplete lifecycle transitions across Unix users, Unix groups, quotas, Slurm DB associations, archives, and `users-state.yml`.

Use tier templates as the policy inheritance model.

- Machine profiles describe hardware and site layout.
- Policy templates describe reusable limits such as CPU, GPU, RAM, `/home`, `/data`, `/scratch`, preemption, and fairshare behavior.
- User tiers compose templates.
- Per-user overrides are named records with a reason, values, and an optional `expires_at`.
- Per-user overrides may change inherited values permanently or until expiry.
- Multiple named overrides may coexist, but overlapping active overrides for the same field are invalid and should fail validation.
- A scheduled reconcile should expire temporary overrides and restore the inherited policy.
- Expired overrides enforce immediately. Where a running job must be interrupted, use the standard 5-minute grace period when possible.
- Only admins may grant priority/emergency tiers or temporary overrides.
- Unix groups are managed from policy. Use an umbrella Slurm user group plus per-tier groups.
- Policy/system groups use the profile group prefix, for example `ssn-users` and `ssn-tier-standard`. Project/lab groups keep the YAML names.
- Users should have private primary groups.

Starter tiers should be compact:

- `standard`
- `priority`
- `emergency`

The first generic NVIDIA GPU target should assume four GPUs until discovery pins exact hardware values.

- `standard`: up to 1 GPU per job.
- `priority`: up to 2 GPUs per job.
- `emergency`: up to all GPUs per job.
- Running/submitted job limits: `standard=3`, `priority=5`, `emergency=10`.
- Maximum walltime: `standard=48h`, `priority=72h`, `emergency=96h`.
- Default walltime: `4h`.
- Memory caps: `standard=25%`, `priority=50%`, `emergency=90%` of Slurm allocatable memory after OS/login reserves.
- Preemption ranks: `standard=0`, `priority=50`, `emergency=100`.
- Admins may extend jobs beyond normal tier walltime limits.
- Exact CPU-per-job values are explicit per profile, not formula-derived. Discovery may suggest values, but profile review pins the final numbers.
- CPU caps for the current CPU-only development machine are locked as `standard=4`, `priority=10`, and `emergency=18` CPUs per job on the 20-CPU dev/test host, preserving the 2-CPU reserve.
- CPU caps for the target NVIDIA GPU profile remain `REVIEW_REQUIRED` until hardware discovery and profile review.
- Generic profile defaults reserve 2 CPUs and 4 GB RAM for the OS, login sessions, and services before exposing capacity to Slurm. Profiles may override this.
- CPU-only profiles use the same tier names, with GPU fields omitted or disabled.

Lifecycle semantics:

- `suspended` blocks SSH/login and Slurm submission/execution using Unix account locking/expiry, PAM/login denial, and Slurm association disabling. It kills pending/running jobs immediately, but does not archive or delete user data.
- `inactive` kills all pending/running jobs, verifies the queue is clear for that user, disables login and Slurm through the same account/PAM/association mechanisms, locks `/data/$USER`, prunes and archives `/home/$USER`, and removes the local account only after the archive and required backup/replication workflow succeeds or an explicit admin override is used.
- Reactivating an inactive user restores access to `/data/$USER` under the original UID/GID instead of treating the user as new.

## Slurm Partitions And Job Requests

Use a single default Slurm partition per profile. The partition name is profile-defined.

Generic profiles use `compute` as the default partition name.

- CPU-only jobs are the default behavior.
- GPU jobs must explicitly request GPUs with `--gres=gpu:N` or the chosen Slurm GPU request syntax.
- The partition alone must not imply a GPU allocation.
- CPU and GPU jobs may share the same physical node when CPU, RAM, and GPU resources remain available.
- Sharing must be protected by Slurm cgroups and profile/tier limits.
- Slurm QoS/accounting remains the authoritative enforcement mechanism.
- Keep a `job_submit.lua` plugin as a strict, fast submit-time gate for explicit request fields and friendlier errors. It should reject over-tier CPU/GPU/RAM/walltime requests, unsafe or unsupported GPU syntax, and preemption conflicts such as `--no-requeue`. It must not perform slow I/O, shellouts, heavy policy resolution, final-default resolution, runtime-state checks, or job-environment checks inside `slurmctld`. Final enforcement belongs to resolver-rendered Slurm/QOS/TRES/cgroup settings.

User examples should teach this pattern:

- CPU examples use the profile's default partition.
- GPU examples add an explicit GPU request.

## Fairshare And Billing

Use fairshare first, not hard daily compute quotas, for normal users.

- Recent heavy users get lower priority over time.
- Users are not hard-blocked from running when capacity is idle.
- Use Slurm multifactor priority for v1 fairshare.
- Fairshare decay should use a weekly horizon by default, with `PriorityDecayHalfLife=7-0`.
- CPU and GPU usage should be converted to a weighted billing score.
- Default TRES billing weights are `CPU=1` and `GPU=64`, meaning one GPU-hour counts like 64 CPU-hours.
- Starter priority weights are `PriorityWeightFairshare=10000`, `PriorityWeightAge=1000`, and `PriorityWeightQOS=1000`.
- Users have equal fairshare shares under the default Slurm account in v1.
- RAM is enforced per job/per tier only; do not implement RAM-hour billing or quotas initially.

Hard compute quotas are future work only, possibly for visitor/trial tiers. They are not part of v1.

## Preemption

Support preemption for both CPU and GPU work.

- Preemption only matters when the requested CPU/GPU resource has no free capacity.
- Use multiple preemption levels represented by a linear `preempt_rank`.
- Higher `preempt_rank` tiers can preempt lower `preempt_rank` tiers.
- Use QOS-based preemption in v1 with `PreemptType=preempt/qos`, `PreemptMode=REQUEUE`, `JobRequeue=1`, and QOS `GraceTime=300` seconds.
- Define QOS `Preempt=` relationships by tier rank: higher-ranked tiers can preempt lower-ranked tiers. Normal user jobs in preemptible tiers must remain requeueable; user opt-out from requeue is not supported in v1 except by admin override.
- `job_submit.lua` must reject normal-user submissions or modifications that disable requeue for preemptible tiers, including `--no-requeue`.
- Preemption should gracefully requeue lower-priority jobs by default.
- Jobs should be requeueable by default.
- Preempted jobs get a 5-minute warning/grace period before requeue.
- Interactive `srun` jobs are allowed in normal tiers, but they are canceled on preemption rather than requeued because live terminal/session state cannot resume cleanly.
- Documentation and examples must teach checkpointing because requeue restarts the batch job later; it does not resume arbitrary process memory.

## Login Policy

Use constrained login as the default.

Users may SSH into the all-in-one node for:

- Editing files.
- Copying, moving, syncing, and organizing data.
- Submitting jobs.
- Checking queue and job state.
- Running lightweight status tools such as `htop`, `btop`, and the managed GPU status wrapper.
- Running remote editor servers lightly, as long as they stay within login caps.

Users must not run real CPU or GPU workloads directly in their login shell. Real compute, including CPU notebooks, should go through Slurm. The system enforces constrained login limits to reduce abuse; it does not claim that CPU computation outside Slurm is technically impossible.

Implement login constraints with PAM/systemd/cgroup limits for non-Slurm login sessions:

- Limits apply through dedicated non-admin login scopes/slices, not per individual SSH/session, so multiple terminals or remote IDE sessions do not multiply the allowance.
- Slurm job scopes remain Slurm-owned and outside the login-scope caps.
- 2 CPUs per non-admin user outside Slurm.
- 4 GB RAM per non-admin user outside Slurm.
- 128 tasks/processes per non-admin user outside Slurm.
- Lower I/O weight for login sessions rather than hard I/O caps.
- Admin users are exempt from these login caps.
- Remote IDEs are allowed, but they are still constrained by the same login caps.

GPU isolation should be strong outside Slurm:

- Users should not directly access `/dev/nvidia*` outside Slurm jobs.
- Direct GPU tools such as `nvidia-smi` and `gpustat` should friendly-deny outside Slurm and point users to Slurm or the status wrapper.
- Enforce non-Slurm GPU denial through systemd/cgroup v2 device policy in dedicated non-admin login scopes only.
- Valid Slurm GPU jobs launched by the same Unix user must still receive their allocated GPU devices through Slurm cgroups.
- GPU profiles fail closed if hard non-Slurm login-session GPU denial cannot be proven safe on the target OS/cgroup stack.
- Friendly denials should use PATH wrappers for common GPU tools. Wrappers are only user experience; systemd/cgroup device policy remains the enforcement boundary.
- Install a profile-prefixed status wrapper such as `<prefix>-gpu-status`.
- The wrapper reads a root/service-collected snapshot refreshed every 10 seconds.
- The snapshot should show GPU utilization, memory, temperature, and Slurm job/user mapping.

Strict Slurm-only SSH is not the default because the same machine is the login node and compute node.

Do not add a process-policing daemon in v1. Login CPU enforcement is the configured PAM/systemd/cgroup caps plus documentation.

## Storage Policy

Use optional storage features per profile, but the desired general model has separate persistent and scratch areas:

- `/home`: persistent, quota-managed, intended for environments, code, configs, and small outputs.
- `/data`: persistent, quota-managed, intended for datasets, checkpoints, results, and expensive persistent caches. On RAID0-backed profiles it is persistent across normal reboots but not durable against disk failure.
- `/scratch`: non-persistent, quota-managed, intended for temporary data, rebuildable caches, and job staging.

Storage paths are optional per profile. A CPU-only development profile may omit `/data`, `/scratch`, or archive roots if that machine does not have those mounts.

For the target generic NVIDIA GPU server:

- NVMe is expected to hold `/` and `/home`.
- `/` may be a separate 512 GB partition.
- Remaining NVMe space may be `/home`.
- Six SATA SSDs are expected to form a RAID0 pool used for `/data` and `/scratch`.
- The target RAID0 layout is acceptable for capacity and speed, but it is not a substitute for durable storage. Important `/data` contents and inactive-user archives require external backup or replication.
- `/data` and `/scratch` require separate quota-enforced capacity boundaries even if they are backed by the same RAID0 pool. This may be implemented as separate filesystems, LVs, datasets, or a filesystem with reliable project/path quota support.

The Slurm automation should not partition, format, or create RAID in the main setup. Admins provision filesystems first; this automation verifies mounts and then manages quotas, directories, cleanup, and policy.

If a profile enables quotas for `/home`, `/data`, or `/scratch` but the mounted filesystem lacks quota support, validation should fail before applying changes.

Active `/data/$USER` backup and replication are external site-admin responsibilities in v1. This project should warn and require explicit profile/site acknowledgment for non-durable RAID0-backed data, but it should not run active-data backup jobs in v1.

Default standard-user storage values:

- `/home`: 100 GB.
- `/data`: 500 GB.
- `/scratch`: 1 TB.

Use both scratch types:

- `/scratch/$USER` for user-scoped TTL scratch and rebuildable caches.
- Per-job scratch for temporary job working data.

Per-job scratch should be managed by Slurm prolog/epilog in v1. Root prolog/epilog owns directory creation and cleanup, while TaskProlog exports `SLURM_TMPDIR`, `TMPDIR`, `TMP`, and `TEMP` into user tasks. Login shells keep the broader `/scratch/$USER/tmp` defaults. Slurm `job_container/tmpfs` is not the v1 default; it may become a future/profile-optional enhancement after validation.

Profiles that require scratch must block jobs until scratch is healthy. Validation/verify should check mount presence, writeability, free space, quota support, job-scratch root health, and cleanup safety before allowing scratch-dependent jobs to run.

If per-job scratch setup fails at runtime despite preflight checks, fail the affected job with a clear reason, mark scratch unhealthy, and block new scratch-dependent jobs until admin repair. Do not silently fall back to `/home`, `/tmp`, or another unmanaged temporary path. Avoid node drain where Slurm behavior permits; if Slurm forces drain on a prolog/epilog failure, the health gate should surface that as an admin repair state.

Default scratch cleanup age is 30 days and must be configurable by profile.

Scratch cleanup should be managed age-based cleanup, preferably with `systemd-tmpfiles` or a systemd timer. Do not rely on filesystem-native TTL as the primary design because it is not portable across the target filesystems. Cleanup should write reports/logs and then delete eligible files by age. Age-based cleanup excludes Slurm-managed per-job scratch roots and active job directories; prolog/epilog owns those paths.

## Cache Policy

Use a default cache map with profile overrides. Do not try to infer cache importance automatically from file contents.

Default cache direction:

- Use a broad development cache map.
- The `broad-dev` cache policy requires `/scratch`. Profiles without `/scratch` must choose a smaller cache policy or define explicit alternate cache paths.
- `XDG_CACHE_HOME` points to scratch.
- Rebuildable development caches go to `/scratch/$USER/cache`.
- Hugging Face model/dataset caches go to persistent `/data/$USER/cache/huggingface`.
- Package-manager caches, including pip, uv, Pixi, and Conda package caches, go to scratch.
- Actual environments may live in `/home` or `/data`, depending on user choice and quota.
- W&B runs and offline directories go to `/data`; W&B cache and temporary artifacts go to scratch.
- Cache environment applies to both login shells and Slurm jobs.
- Cache defaults should be centrally injected, including into Slurm jobs, so a bare `sbatch` inherits policy defaults.
- Injected cache variables are defaults only. If a user intentionally sets a managed variable before submission or in their job, the user value wins, except for the Slurm job temp variables managed by TaskProlog at task start.
- For Slurm jobs, TaskProlog overrides `TMPDIR`, `TMP`, and `TEMP` to the per-job scratch directory after cache defaults are applied. Users may still override them inside their own job scripts after startup if needed.
- Rebuildable cache cleanup uses the same TTL as `/scratch/$USER` cleanup, default 30 days.
- If a profile has no `/data`, persistent expensive caches fall back to a profile-defined quota-managed path under `/home/$USER`.
- The core exact cache map is locked for v1. Obscure or tool-version-specific cache variables remain profile extensions.

Core scratch/TTL cache defaults:

- `XDG_CACHE_HOME=/scratch/$USER/cache/xdg`
- `PIP_CACHE_DIR=/scratch/$USER/cache/pip`
- `UV_CACHE_DIR=/scratch/$USER/cache/uv`
- `CONDA_PKGS_DIRS=/scratch/$USER/cache/conda/pkgs`
- `PIXI_CACHE_DIR=/scratch/$USER/cache/pixi`
- `RATTLER_CACHE_DIR=/scratch/$USER/cache/rattler`
- `TRITON_CACHE_DIR=/scratch/$USER/cache/triton`
- `NUMBA_CACHE_DIR=/scratch/$USER/cache/numba`
- `TORCH_HOME=/scratch/$USER/cache/torch`
- `WANDB_CACHE_DIR=/scratch/$USER/cache/wandb`
- `WANDB_DATA_DIR=/scratch/$USER/cache/wandb-data`
- `WANDB_ARTIFACT_DIR=/scratch/$USER/cache/wandb-artifacts`
- `TMPDIR=/scratch/$USER/tmp`
- `TMP=/scratch/$USER/tmp`
- `TEMP=/scratch/$USER/tmp`
- `JUPYTER_RUNTIME_DIR=/scratch/$USER/cache/jupyter/runtime`

Matplotlib configuration/cache environment is not managed by the default cache map because it can contain user customizations as well as caches. Profiles may add a site-specific Matplotlib override after review.

Core persistent data defaults:

- `HF_HUB_CACHE=/data/$USER/cache/huggingface/hub`
- `HF_DATASETS_CACHE=/data/$USER/cache/huggingface/datasets`
- `HF_ASSETS_CACHE=/data/$USER/cache/huggingface/assets`
- `HF_XET_CACHE=/data/$USER/cache/huggingface/xet`
- `WANDB_DIR=/data/$USER/wandb`

Core home config/auth defaults:

- `HF_HOME=/home/$USER/.config/huggingface`
- `WANDB_CONFIG_DIR=/home/$USER/.config/wandb`

Conda package cache redirection should use the global environment default `CONDA_PKGS_DIRS`. Do not manage user `.condarc` files in v1.

Profile extensions may add additional ecosystem-specific cache variables, such as npm, Cargo, Go, R, or site-specific ML framework variables, after local review.

Reference anchors for the core env names:

- pip caching/configuration: https://pip.pypa.io/en/stable/topics/caching/ and https://pip.pypa.io/en/stable/topics/configuration/
- uv cache: https://docs.astral.sh/uv/concepts/cache/
- Conda package cache: https://docs.conda.io/projects/conda/en/stable/user-guide/configuration/settings.html
- Pixi/Rattler cache: https://pixi.prefix.dev/latest/reference/environment_variables/
- Hugging Face environment variables and dataset cache: https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables and https://huggingface.co/docs/datasets/main/cache
- W&B environment variables: https://docs.wandb.ai/models/track/environment-variables
- PyTorch Hub cache: https://docs.pytorch.org/docs/stable/hub.html
- Numba cache: https://numba.readthedocs.io/en/latest/reference/envvars.html
- Jupyter runtime directory: https://jupyter-core.readthedocs.io/en/latest/api/jupyter_core.html

## Inactive Users And Archives

When a user is marked inactive:

- Kill all pending/running Slurm jobs for that user and verify the queue is clear.
- Lock `/data/<user>` so data remains preserved but unavailable to the inactive account.
- Prune known rebuildable environment/cache paths from `/home/<user>` first.
- Compress the remaining `/home/<user>` with `7zz -mx=9`.
- Store the archive under a profile-defined archive root, expected to be under `/data/_archive` for the new storage model.
- Run expensive compression as a Slurm admin job under a root/slurm-admin service identity, not directly inside the sync playbook.
- Archive jobs use a protected service/admin Slurm account and non-preemptible admin QoS, outside normal user limits, and are monitored by `ssn-archive-status`.
- Keep local inactive-user archives indefinitely by default.
- Keep the local user account locked until the archive job and required external backup/replication succeed, then remove it. An explicit admin override may remove the account after local archive success while recording that the archive is not yet durable.
- Reactivation restores `/data/<user>` access instead of creating a fresh data directory.
- Inactive/archive transitions require a configured archive root. If a profile lacks an archive root, validation must block inactive transitions rather than deleting accounts without a durable archive target.
- Inactive/archive transitions require a configured backup/replication hook when durability is required. If no hook is configured, validation blocks the transition unless the apply uses an explicit reviewed local-only override token.
- If Slurm is unavailable, block the inactive transition until Slurm is healthy enough to run the archive job.

Inactive archive lifecycle is tracked in `users-state.yml` with substates: `archive_pending`, `archive_running`, `archived_local_only`, `backup_failed`, `backup_complete`, `removal_ready`, and final tombstone state.

The default inactive prune allowlist should delete:

- The entire `/home/$USER/.cache` tree.
- Marker-detected Python virtual environments, for example directories containing `pyvenv.cfg`.
- Marker-detected Conda environments, for example directories containing `conda-meta/history`.
- `/home/$USER/.conda/pkgs`.
- `/home/$USER/.conda/envs`.
- `/home/$USER/.pixi/cache`.
- `/home/$USER/.pixi/envs`.
- `/home/$USER/.local/share/Trash`.

Pruning should recursively scan for allowlisted cache/env names, not only fixed top-level paths. Because recursive pruning is stronger, it must be driven only by the explicit allowlist and must never delete arbitrary large directories by heuristic.

Never follow symlinks while pruning. If an allowlisted prune path is a symlink, remove only the symlink and record that in the manifest.

Dependency/build trees such as `node_modules`, Rust `target`, `build`, `dist`, Go module caches, and similar directories are report-only by default.

Inactive cleanup should produce a dry plan first, then run the real apply non-interactively after admin approval or when explicitly invoked in apply mode. The dry plan writes a plan id/hash, and real apply must reference that reviewed plan token.

Prune audit should write a manifest of deleted paths, sizes, timestamps, and the matching rule. Do not preserve deleted cache contents.

External backup hooks are supported only for inactive-user archives.

- Hooks run after the local archive is created.
- If a hook fails, keep the local archive, keep the local account locked, warn loudly, write logs, and block account removal until backup/replication succeeds or an explicit admin override is used. The archive remains non-durable until external backup or replication succeeds.
- Hooks do not make this repo a full backup system.
- Hooks are configured as executable scripts in an archive hook directory with documented environment variables.

## Modules And Shared Software

Use Lmod as the module system.

Shared software roots are profile-defined, with defaults under `/tools`:

- `/tools/apps`
- `/tools/modules`
- `/tools/containers`

Modules should support:

- CUDA.
- Miniconda-style loaders.
- Pixi and similar environment/tool loaders.
- Native root-managed scientific apps.
- Container-backed apps through optional Apptainer support.

Use a per-module update policy:

- Shared software roots are versioned where practical, and default module changes happen through an admin-run update and smoke-check workflow.
- Keep rollback targets for centrally managed loader/module changes.
- CUDA toolkit ownership is profile-selectable.
- The default CUDA toolkit mode is validate-only: admins install the toolkit, and automation validates/discovers it and exposes modules.
- Managed CUDA toolkit installation/update is opt-in per profile.
- Managed CUDA toolkit updates are admin-run only, with balanced smoke checks. Do not use unattended CUDA toolkit upgrades by default.
- CUDA and heavy scientific apps may define their own policy.
- Scientific/domain apps can be native central modules or container-backed modules.
- Scientific/domain apps should use a check-and-approve workflow rather than blind updates.
- Do not perform unattended module updates in v1.

Default CUDA behavior:

- `module load cuda` points to the profile-selected default CUDA installation.
- Expose both `cuda` and `cuda/<version>` modules when version detection supports it.
- Before changing the default, perform balanced CUDA smoke checks.
- Surface CUDA/default changes through MOTD plus admin logs.

Balanced CUDA smoke checks include module load/unload, `nvcc --version` when `nvcc` is present, `nvidia-smi`, library path sanity, and an optional CUDA sample compile/run when the sample toolchain is available.

Shared domain software, such as protein docking tools, should be installable centrally and exposed through modules so users do not keep identical copies against their quotas.

The default shared Python/environment base remains Miniconda in v1.

If Apptainer is enabled by a profile, root-managed container images live under `/tools/containers` by default.

## Project Groups

Support basic project/lab groups in `users.yml`.

- Groups are useful for Unix permissions and future policy hooks.
- Each user's `groups` field is authoritative for membership.
- Top-level `groups` contains metadata only.
- Project/lab groups keep the YAML names.
- Do not add shared group storage in v1.
- Module visibility is global to all users in v1.

## Canonical YAML Sketches

These sketches are the intended v1 interfaces unless implementation discovers a concrete blocker. They are canonical for structure and field names. Values marked `REVIEW_REQUIRED` are discovery/review placeholders unless they are explicitly listed in `Open Questions`.

`REVIEW_REQUIRED` hardware fields such as `node_name`, `gpu_type`, `cpus_total`, and `memory_total` are discovery/review inputs. They are not policy open questions unless listed in `Open Questions`.

`users.yml` shape:

In this sketch, omitted or null `ssh_keys` leaves `authorized_keys` unmanaged. An explicit empty map means the managed key set is intentionally empty. When both `options_raw` and parsed `options` are present, they must agree or validation fails.

```yaml
schema_version: 1

groups:
  wetlab:
    description: "Wet lab project group"
  visitors:
    description: "Short-term external users"

users:
  alice:
    status: active
    tier: standard
    full_name: "Alice Example"
    email: "alice@example.org"
    uid: null
    gid: null
    groups:
      - wetlab
    ssh_keys:
      laptop:
        public_key: "ssh-ed25519 AAAA... alice@laptop"
        options_raw: 'from="203.0.113.0/24",no-agent-forwarding'
        options:
          from:
            - "203.0.113.0/24"
          no_agent_forwarding: true
        comment: "Primary laptop"
        added_by: "admin"
    overrides:
      conference_deadline:
        reason: "Temporary deadline access"
        expires_at: "2026-06-30T23:59:00+05:30"
        values:
          tier: priority
          max_walltime: 72h

  bob:
    status: suspended
    tier: standard
    # Managed empty key set.
    ssh_keys: {}
```

`users-state.yml` managed state shape:

```yaml
schema_version: 1
users:
  alice:
    managed: true
    original_uid: 1001
    original_gid: 1001
    uid_gid_tombstone: false
    archive_state: null

  archived_user:
    managed: true
    original_uid: 1402
    original_gid: 1402
    uid_gid_tombstone: true
    archive_state: tombstoned
    archive_substates:
      allowed:
        - archive_pending
        - archive_running
        - archived_local_only
        - backup_failed
        - backup_complete
        - removal_ready
        - tombstoned
```

Profile binding shape:

```yaml
schema_version: 1
profile: generic-nvidia-4gpu
extends: generic-gpu

identity:
  cluster_name: ssn-gpu
  node_name: REVIEW_REQUIRED
  command_prefix: ssn
  group_prefix: ssn
  default_partition: compute

admins:
  users:
    - root
  groups:
    - slurm_admins

services:
  munge:
    key_mode: generate_if_absent
    auto_rotate: false
    backup: local_protected
    validate_permissions: true
    require_service_healthy: true

hardware:
  discovered_at: null
  gpus: 4
  gpu_vendor: nvidia
  gpu_type: REVIEW_REQUIRED
  gpu_modes:
    mig: fail_closed
    mps: fail_closed
    shared_gpu: fail_closed
  gpu_affinity:
    mode: discover_review_pin
    render_gres_cores: reviewed_only
    cores: REVIEW_REQUIRED
  cpus_total: REVIEW_REQUIRED
  memory_total: REVIEW_REQUIRED
  reserved_cpus: 2
  reserved_memory: 4GB
  memory_defaults:
    def_mem_per_cpu: REVIEW_REQUIRED
    max_mem_per_cpu: REVIEW_REQUIRED

policies:
  slurm_core: strict-cons-tres
  tiers: starter
  storage: standard-three-area
  cache: broad-dev
  modules: tools-miniconda
  login: constrained

operations:
  capability_checks:
    ubuntu: "24.04_or_26.04_with_required_features"
    slurm: apt_package_with_cons_tres_cgroup_v2_lua
    database: mariadb_supported
    gpu: nvidia_nvml_when_gpu_profile
  plan_artifacts:
    root: /var/lib/slurm-single-node/plans
    retention: 90d
    owner: root
    group: slurm_admins
    directory_mode: "0750"
    file_mode: "0640"
    risky_apply_requires_token: true
    token_policy:
      redacted: true
      input_hash_bound: true
      expires_after: 24h
      single_use: true
    redaction:
      secrets: hidden
      ssh_keys: fingerprint_only
      emails:
        terminal: masked
        root_json: retained
      sensitive_manifests:
        terminal: path_limited
        root_json: full_paths
  reconcile:
    model: staged_state_machine
    resume_partial_transitions: true
    rollback: manual_or_explicit_only
  backups:
    users_yml:
      root: /var/backups/slurm-single-node/users
      retention: 90d
    users_state_yml:
      root: /var/backups/slurm-single-node/users
      retention: 90d
  gpu_verification:
    run_after_boot: true
    run_after_apply: true
    health_gate: true
    on_failure: cpu_only_recovery
    recovery:
      type: temporary_overlay
      running_gpu_jobs: drain_or_cancel_with_warning
      pending_gpu_jobs: hold
      gpu_gres: remove_from_overlay
      cpu_jobs: keep_available_where_safe
    checks:
      - gres_conf
      - device_files
      - nvml_ordering
      - cuda_ordering
      - status_wrapper_mapping
      - topology_affinity_if_pinned
      - login_gpu_denial
      - slurm_gpu_job_access

overrides: {}
```

`policies/slurm-core.yml` shape:

```yaml
schema_version: 1
policies:
  strict-cons-tres:
    select:
      type: select/cons_tres
      parameters: CR_CPU_Memory
      consumable:
        cpu: true
        memory: true
        gres: true
    task_plugin:
      cgroup_v2: true
      constrain_cores: true
      constrain_ram: true
      constrain_devices: true
    accounting:
      storage_tres:
        - cpu
        - mem
        - gres/gpu
      gpu_tres_when_available: true
    memory:
      default_mode: explicit_profile_values
      def_mem_per_cpu: REVIEW_REQUIRED
      max_mem_per_cpu: REVIEW_REQUIRED
      reserve_base: allocatable_after_reserve
      tier_max_percent_enforced: true
      render_qos_max_tres_mem: true
    submit_filter:
      plugin: job_submit.lua
      mode: validate_explicit_requests_only
      reject:
        - over_tier_cpu_gpu_ram_walltime
        - unsafe_gpu_syntax
        - no_requeue_for_preemptible_tiers
      disallow_slow_io: true
      disallow_runtime_state_checks: true
      final_enforcement: slurm_qos_tres_cgroups
```

`policies/tiers.yml` shape:

```yaml
schema_version: 1
policies:
  starter:
    slurm_account: default
    default_tier: standard
    memory_percent_base: allocatable_after_reserve
    fairshare:
      priority_type: multifactor
      priority_decay_half_life: "7-0"
      priority_weight_fairshare: 10000
      priority_weight_age: 1000
      priority_weight_qos: 1000
      account_user_shares: equal
      tres_billing_weights:
        cpu: 1
        gres/gpu: 64
        mem: 0
    preemption:
      type: qos
      mode: requeue
      grace_time: 300s
      job_requeue: true
      allow_user_no_requeue: false
      submit_rejects_no_requeue: true
      interactive_jobs:
        allowed: true
        on_preempt: cancel
      relationships:
        standard:
          preempts: []
        priority:
          preempts:
            - standard
        emergency:
          preempts:
            - standard
            - priority
    tiers:
      standard:
        max_gpus_per_job: 1
        max_cpus_per_job: REVIEW_REQUIRED
        max_running_jobs: 3
        max_submitted_jobs: 3
        default_walltime: 4h
        max_walltime: 48h
        memory_percent: 25
        preempt_rank: 0
      priority:
        max_gpus_per_job: 2
        max_cpus_per_job: REVIEW_REQUIRED
        max_running_jobs: 5
        max_submitted_jobs: 5
        default_walltime: 4h
        max_walltime: 72h
        memory_percent: 50
        preempt_rank: 50
      emergency:
        max_gpus_per_job: all
        max_cpus_per_job: REVIEW_REQUIRED
        max_running_jobs: 10
        max_submitted_jobs: 10
        default_walltime: 4h
        max_walltime: 96h
        memory_percent: 90
        preempt_rank: 100

  starter-cpu-dev:
    slurm_account: default
    default_tier: standard
    memory_percent_base: allocatable_after_reserve
    fairshare:
      priority_type: multifactor
      priority_decay_half_life: "7-0"
      priority_weight_fairshare: 10000
      priority_weight_age: 1000
      priority_weight_qos: 1000
      account_user_shares: equal
      tres_billing_weights:
        cpu: 1
        mem: 0
    preemption:
      type: qos
      mode: requeue
      grace_time: 300s
      job_requeue: true
      allow_user_no_requeue: false
      submit_rejects_no_requeue: true
      interactive_jobs:
        allowed: true
        on_preempt: cancel
      relationships:
        standard:
          preempts: []
        priority:
          preempts:
            - standard
        emergency:
          preempts:
            - standard
            - priority
    tiers:
      standard:
        max_cpus_per_job: 4
        max_running_jobs: 3
        max_submitted_jobs: 3
        default_walltime: 4h
        max_walltime: 48h
        memory_percent: 25
        preempt_rank: 0
      priority:
        max_cpus_per_job: 10
        max_running_jobs: 5
        max_submitted_jobs: 5
        default_walltime: 4h
        max_walltime: 72h
        memory_percent: 50
        preempt_rank: 50
      emergency:
        max_cpus_per_job: 18
        max_running_jobs: 10
        max_submitted_jobs: 10
        default_walltime: 4h
        max_walltime: 96h
        memory_percent: 90
        preempt_rank: 100
```

`policies/storage.yml` shape:

```yaml
schema_version: 1
policies:
  standard-three-area:
    paths:
      home: /home
      data: /data
      scratch: /scratch
      archive: /data/_archive
    durability:
      data: persistent_not_durable_on_raid0
      archive: requires_external_backup_or_replication
      active_data_backup: external_site_admin_responsibility
      nondurable_ack_required: true
    quotas:
      home: 100GB
      data: 500GB
      scratch: 1TB
      fail_if_unavailable: true
      data_scratch_capacity_isolation: required
      same_pool_allowed_with_project_quotas: true
    scratch_cleanup:
      age: 30d
      implementation: systemd-tmpfiles
      report: true
      exclude_job_scratch: true
      exclude_active_jobs: true
    job_scratch:
      implementation: prolog_epilog
      required_for_jobs: true
      unhealthy_action: block_jobs
      runtime_failure:
        affected_job: fail_with_reason
        mark_scratch_unhealthy: true
        block_new_jobs: true
        fallback_temp: false
        avoid_node_drain_where_slurm_permits: true
      preflight_checks:
        - mount_present
        - writable
        - free_space
        - quota_support
        - job_root_safe
      env_var: SLURM_TMPDIR
      task_prolog_exports:
        - SLURM_TMPDIR
        - TMPDIR
        - TMP
        - TEMP
      root: /scratch/jobs
      create_with: root_prolog
      export_with: task_prolog
      cleanup_with: root_epilog
      job_container_tmpfs: optional_future
    inactive_archive:
      requires_archive_root: true
      service_identity: root_slurm_admin
      slurm_account: slurm-admin
      qos: archive-admin
      preemptible: false
      outside_user_limits: true
      monitored_by: ssn-archive-status
      user_account_until_success: locked
      removal_requires_backup_success: true
      removal_override: explicit_admin_token
      backup_hook:
        required_for_durability: true
        missing_hook_action: fail_transition
        local_only_override: reviewed_token
      slurm_unavailable: block_transition
      external_backup_required_for_durability: true
      state_substates:
        - archive_pending
        - archive_running
        - archived_local_only
        - backup_failed
        - backup_complete
        - removal_ready
        - tombstoned
      compression: 7zz-mx9
      apply_requires_plan_token: true
      prune_manifest: true
      symlinks: remove_link_only
      delete_fixed_paths:
        - .cache
        - .conda/pkgs
        - .conda/envs
        - .pixi/cache
        - .pixi/envs
        - .local/share/Trash
      recursive_marker_rules:
        python_venv:
          marker_file: pyvenv.cfg
        conda_env:
          marker_file: conda-meta/history
      report_only_names:
        - node_modules
        - target
        - build
        - dist
```

`policies/cache.yml` shape:

```yaml
schema_version: 1
policies:
  broad-dev:
    requires:
      scratch: true
    injection:
      login_shells: true
      slurm_jobs: true
      mode: default_only
      slurm_job_temp_override: per_job_scratch
    ttl: inherit_scratch
    roots:
      scratch_cache: /scratch/$USER/cache
      persistent_cache: /data/$USER/cache
      persistent_cache_fallback: /home/$USER/.cache/persistent
      home_config: /home/$USER/.config
      scratch_tmp: /scratch/$USER/tmp
    env:
      scratch:
        XDG_CACHE_HOME: /scratch/$USER/cache/xdg
        PIP_CACHE_DIR: /scratch/$USER/cache/pip
        UV_CACHE_DIR: /scratch/$USER/cache/uv
        CONDA_PKGS_DIRS: /scratch/$USER/cache/conda/pkgs
        PIXI_CACHE_DIR: /scratch/$USER/cache/pixi
        RATTLER_CACHE_DIR: /scratch/$USER/cache/rattler
        TRITON_CACHE_DIR: /scratch/$USER/cache/triton
        NUMBA_CACHE_DIR: /scratch/$USER/cache/numba
        TORCH_HOME: /scratch/$USER/cache/torch
        WANDB_CACHE_DIR: /scratch/$USER/cache/wandb
        WANDB_DATA_DIR: /scratch/$USER/cache/wandb-data
        WANDB_ARTIFACT_DIR: /scratch/$USER/cache/wandb-artifacts
        TMPDIR: /scratch/$USER/tmp
        TMP: /scratch/$USER/tmp
        TEMP: /scratch/$USER/tmp
        JUPYTER_RUNTIME_DIR: /scratch/$USER/cache/jupyter/runtime
      persistent:
        HF_HUB_CACHE: /data/$USER/cache/huggingface/hub
        HF_DATASETS_CACHE: /data/$USER/cache/huggingface/datasets
        HF_ASSETS_CACHE: /data/$USER/cache/huggingface/assets
        HF_XET_CACHE: /data/$USER/cache/huggingface/xet
        WANDB_DIR: /data/$USER/wandb
      home_config:
        HF_HOME: /home/$USER/.config/huggingface
        WANDB_CONFIG_DIR: /home/$USER/.config/wandb
```

`policies/modules.yml` shape:

```yaml
schema_version: 1
policies:
  tools-miniconda:
    roots:
      apps: /tools/apps
      modules: /tools/modules
      containers: /tools/containers
    lmod: true
    updates:
      mode: admin_run
      versioned_roots: true
      smoke_checks_required: true
      rollback_targets: keep_previous
      unattended_updates: false
    shared_env_base:
      type: miniconda
      root: /tools/miniconda3
    cuda:
      toolkit_mode: validate_only
      managed_updates: admin_run
      modules:
        default: cuda
        versioned: auto_detect
      smoke_checks:
        module_load_unload: true
        nvcc_version_if_present: true
        nvidia_smi: true
        library_path_sanity: true
        optional_sample_compile_run: true
    apptainer:
      enabled: false
      container_root: /tools/containers
```

`policies/login.yml` shape:

```yaml
schema_version: 1
policies:
  constrained:
    cgroup: v2
    non_admin_limits:
      scope: dedicated_non_admin_login_slice
      cpus: 2
      memory: 4GB
      tasks: 128
      io_weight: low
      applies_to_slurm_jobs: false
      slurm_job_cgroups_owned_by_slurm: true
    admins_exempt: true
    remote_ides: allowed_limited
    gpu_outside_slurm:
      direct_access: deny
      enforcement: systemd_cgroup_v2_devices
      applies_to: dedicated_non_admin_login_scopes
      slurm_jobs_receive_allocated_devices: true
      fail_closed_if_unavailable: true
      friendly_path_wrappers: true
      status_wrapper: ssn-gpu-status
      status_collector: root_service_snapshot
      refresh: 10s
```

## User-Facing Docs And Examples

Generate user docs/examples per profile.

- CPU-only and GPU servers should get accurate commands and examples.
- Interactive workflows remain examples only for now.
- Do not add helper commands such as `cpu-shell`, `gpu-shell`, `cpu-jupyter`, or `gpu-jupyter` in the first generalization pass.
- Existing user-kit references to missing `gpu-shell` and `gpu-jupyter` should be removed or replaced with exact `srun`/`sbatch` examples.

## Testing And Rollout

The current development machine is a non-production CPU-only server and may be used for full local live testing. The NVMe plus six-SATA RAID0 machine is the target NVIDIA GPU server and should receive the generalized GPU profile only after local CPU-only validation and render review.

The default deployment inventory style is local apply: run Ansible on the target node with a localhost/local connection. Remote inventory may remain possible, but it is not the primary v1 path.

Local testing may:

- Install Ansible/Slurm dependencies.
- Enable and start Slurm, Munge, `slurmdbd`, and MariaDB services.
- Apply the CPU-only profile locally.
- Create and remove clearly named test users.
- Create a clearly named temporary smoke-test user, run representative CPU/GPU Slurm jobs as that user, and remove the user afterward.

GPU server and DGX rollout must use a render-review gate first:

- Generate resolved production config and rendered service files.
- Review before applying to production.
- Never touch production unless explicitly run against the production inventory/profile.

Live apply should refuse config or service changes while jobs are running by default, unless an explicit force/drain workflow is used. When apply is allowed, use Ansible handlers to restart only affected services after validation.

Apply and sync workflows should be resumable. If a lifecycle operation fails after partial changes, the next dry-run should report the incomplete state and the next apply should reconcile toward the reviewed desired state rather than inventing an implicit rollback.

GPU-profile verification must include acceptance tests for non-admin login denial, admin exemption, CPU-only login workflows, and successful Slurm GPU job device access by the same Unix user who is blocked from direct login GPU access.

GPU rollout testing should also exercise CPU-only recovery overlay behavior, held/canceled GPU jobs, pinned topology verification, and MIG/MPS/shared-GPU fail-closed detection where the hardware exposes those modes.

Local and profile verification should include scratch-unhealthy behavior, archive state-machine transitions, backup-hook missing/failure behavior, and UID/GID tombstone protection using clearly named test users only.

The existing Tesla implementation should be preserved as the `dgx-v100` profile, with optional `tesla-*` compatibility aliases.

## Documentation Loop

Use `docs/current-status.md`, `docs/drawbacks.md`, and this file as the planning artifacts. The expected workflow is:

1. Review these docs.
2. Edit or annotate them with desired changes.
3. Re-read and refine the plan.
4. Implement code changes only after the desired generalized behavior is clear.

After each iteration, promote locked decisions into the sections above and keep only unresolved items below.

## Open Questions

These are not yet locked and need follow-up before implementation details are final:

- Exact CPU-per-job tier limits for the target NVIDIA GPU profile after hardware discovery/profile review.

Discovery/review inputs such as `node_name`, `gpu_type`, `cpus_total`, `memory_total`, GPU affinity cores, and exact profile memory defaults remain marked `REVIEW_REQUIRED` in sketches but are not policy open questions.
