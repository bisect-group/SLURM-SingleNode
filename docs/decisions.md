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
- `policies/` contains reusable domain files such as `tiers.yml`, `storage.yml`, `modules.yml`, `cache.yml`, `login.yml`, and `defaults.yml`.
- Machine profiles remain separate from user policy templates.
- Profiles bind named policy files and may add small profile-local overrides.
- Admins select a profile explicitly with a command such as `ansible-playbook ... -e profile=generic-gpu`.
- Hardware discovery generates a draft profile YAML for admin review; discovery output is not applied directly.
- Policy/profile inheritance uses deep merge semantics: nested maps merge, while lists replace unless a schema field explicitly supports extension.
- Human-readable units are allowed in authored YAML, for example `100GB`, `48h`, and `30d`. Validation should normalize them before rendering templates.

Use a light-dependency Python resolver, with apt-installable dependencies such as PyYAML, to merge and validate profiles, policies, and users before Ansible applies anything. Ansible should consume the resolved output rather than reimplementing schema validation in templates.

Validation must be strict. If the selected profile, policies, mounts, users, tiers, quotas, or limits are inconsistent, the playbook should fail before touching the system.

Dry-run/check mode should produce a full plan: validate config and show intended filesystem, user, and Slurm changes without applying them. It should print readable text and also write a machine-readable JSON plan artifact.

The target should get a resolved audit file at `/etc/slurm-single-node/config.yml` containing the final merged profile, policy, and override values.

## Target Scope

Primary OS targets are Ubuntu 24.04 and Ubuntu 26.04. Ubuntu 22.04 can remain best-effort legacy support when it does not force awkward branching.

Use Ubuntu apt packages as the default Slurm package source. Slurm package updates are admin-run only; do not let unattended upgrades silently update active Slurm services.

Target cgroup v2 only in v1. Validation should fail on systems that are not running unified cgroup v2.

GPU support in v1 is NVIDIA plus CPU-only. AMD and Intel GPU support are out of scope for v1.

For NVIDIA GPU profiles, admins install the NVIDIA driver before running this automation. The automation validates `nvidia-smi`, GPU count, and Slurm GRES wiring, but does not install or own the driver lifecycle in v1.

The automation should manage Apptainer only as an optional feature enabled by a profile, mainly for container-backed modules. Apptainer is off by default.

## Commands And Compatibility

Installed helper command names should use a configurable prefix from the profile.

- The default generalized command prefix is `ssn-*`.
- The DGX/V100 profile may install optional `tesla-*` compatibility aliases or symlinks for old muscle memory.
- Do not use `slurm-*` as the helper prefix, to avoid confusion with upstream Slurm commands.
- The v1 core helper set should cover discovery, render/validate, user sync, verification, GPU status, and archive/status workflows.

The deployed user source of truth should live at a generic path: `/etc/slurm-single-node/users.yml`.

## Accounting

Keep `slurmdbd` and MariaDB accounting as the default.

Reason: the intended policy includes tiers, priorities, fairshare, preemption levels, compute slices, job limits, and historical reporting. Slurm accounting is the clean place to represent associations and QoS limits.

Use QoS as the tier authority in v1. Use one default Slurm account for all users; project/lab groups remain Unix-only metadata for now.

The `slurmdbd` MariaDB password should be generated once on the target and stored in a protected local file readable only by root/Slurm service users.

Create local daily MariaDB dumps of the Slurm accounting database with 30-day retention. External backup hooks are separate from this local accounting backup.

## User Policy Model

Use YAML, not CSV, for the future user source of truth. The deployed source of truth is `/etc/slurm-single-node/users.yml`.

- Existing CSV migration is not required because the CSV system has not been deployed in production.
- A bootstrap/discovery tool should scan local human users from `/etc/passwd`, home directories, and authorized keys into a draft `users.yml` for admin review.
- Only clearly named test users may be created/deleted during local implementation testing.
- The file has `schema_version: 1`.
- Users are keyed by username, not by a list item with a username field.
- The YAML file contains desired state, not runtime bookkeeping.
- Local runtime state is stored separately at `/var/lib/slurm-single-node/users-state.yml`.
- Supported user statuses are `active`, `suspended`, and `inactive`.
- Active users require `tier` and `status`. Missing metadata such as full name, email, or SSH keys should warn, not fail.
- SSH keys are map objects keyed by label, not raw strings. Labels make later key removal and audit easier.
- If a user has no SSH keys in YAML, warn and leave that user's `authorized_keys` unmanaged rather than deleting access unexpectedly.
- Absent existing local users are report-only by default. The sync should not delete unmanaged accounts unless a future explicit cleanup mode is added.
- UID/GID values are auto-allocated by default. Existing local IDs should be preserved, and explicit `uid`/`gid` overrides are allowed only when needed for restore or migration.
- The concrete v1 `users.yml` shape uses top-level `schema_version`, `groups`, and a keyed `users` map.

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
- Memory caps: `standard=25%`, `priority=50%`, `emergency=90%` of node RAM.
- Preemption ranks: `standard=0`, `priority=50`, `emergency=100`.
- Admins may extend jobs beyond normal tier walltime limits.
- Exact CPU-per-job values are explicit per profile, not formula-derived. Discovery may suggest values, but profile review pins the final numbers.
- CPU caps for the current CPU-only development machine are deferred until profile review.
- Generic profile defaults reserve 2 CPUs and 4 GB RAM for the OS, login sessions, and services before exposing capacity to Slurm. Profiles may override this.
- CPU-only profiles use the same tier names, with GPU fields omitted or disabled.

Lifecycle semantics:

- `suspended` blocks SSH/login and Slurm submission/execution, kills pending/running jobs immediately, but does not archive or delete user data.
- `inactive` kills all pending/running jobs, verifies the queue is clear for that user, locks `/data/$USER`, prunes and archives `/home/$USER`, and removes the local account after the archive workflow succeeds.
- Reactivating an inactive user restores access to `/data/$USER` instead of treating the user as new.

## Slurm Partitions And Job Requests

Use a single default Slurm partition per profile. The partition name is profile-defined.

Generic profiles use `compute` as the default partition name.

- CPU-only jobs are the default behavior.
- GPU jobs must explicitly request GPUs with `--gres=gpu:N` or the chosen Slurm GPU request syntax.
- The partition alone must not imply a GPU allocation.
- CPU and GPU jobs may share the same physical node when CPU, RAM, and GPU resources remain available.
- Sharing must be protected by Slurm cgroups and profile/tier limits.
- Slurm QoS/accounting remains the authoritative enforcement mechanism.
- Keep a `job_submit.lua` plugin for friendlier submit-time errors when users request resources beyond their tier.

User examples should teach this pattern:

- CPU examples use the profile's default partition.
- GPU examples add an explicit GPU request.

## Fairshare And Billing

Use fairshare first, not hard daily compute quotas, for normal users.

- Recent heavy users get lower priority over time.
- Users are not hard-blocked from running when capacity is idle.
- Fairshare decay should use a weekly horizon by default.
- CPU and GPU usage should be converted to a weighted billing score.
- Default GPU billing weight: one GPU-hour counts like 64 CPU-hours.
- RAM is enforced per job/per tier only; do not implement RAM-hour quotas initially.

Hard compute quotas are future work only, possibly for visitor/trial tiers. They are not part of v1.

## Preemption

Support preemption for both CPU and GPU work.

- Preemption only matters when the requested CPU/GPU resource has no free capacity.
- Use multiple preemption levels represented by a linear `preempt_rank`.
- Higher `preempt_rank` tiers can preempt lower `preempt_rank` tiers.
- Preemption should gracefully requeue lower-priority jobs by default.
- Jobs should be requeueable by default.
- Preempted jobs get a 5-minute warning/grace period before requeue.
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

Users should not run real CPU or GPU workloads directly in their login shell. Real compute, including CPU notebooks, should go through Slurm.

Implement login constraints with PAM/systemd/cgroup limits for non-Slurm login sessions:

- 2 CPUs per non-admin user outside Slurm.
- 4 GB RAM per non-admin user outside Slurm.
- 128 tasks/processes per non-admin user outside Slurm.
- Lower I/O weight for login sessions rather than hard I/O caps.
- Admin users are exempt from these login caps.
- Remote IDEs are allowed, but they are still constrained by the same login caps.

GPU isolation should be strong outside Slurm:

- Users should not directly access `/dev/nvidia*` outside Slurm jobs.
- Direct GPU tools such as `nvidia-smi` and `gpustat` should friendly-deny outside Slurm and point users to Slurm or the status wrapper.
- Friendly denials should use PATH wrappers for common GPU tools. Wrappers are only user experience; cgroup/device policy remains the enforcement boundary.
- Install a profile-prefixed status wrapper such as `<prefix>-gpu-status`.
- The wrapper reads a root/service-collected snapshot refreshed every 10 seconds.
- The snapshot should show GPU utilization, memory, temperature, and Slurm job/user mapping.

Strict Slurm-only SSH is not the default because the same machine is the login node and compute node.

Do not add a process-policing daemon in v1. Login CPU enforcement is the configured PAM/systemd/cgroup caps plus documentation.

## Storage Policy

Use optional storage features per profile, but the desired general model has separate persistent and scratch areas:

- `/home`: persistent, quota-managed, intended for environments, code, configs, and small outputs.
- `/data`: persistent, quota-managed, intended for datasets, checkpoints, results, and expensive persistent caches.
- `/scratch`: non-persistent, quota-managed, intended for temporary data, rebuildable caches, and job staging.

Storage paths are optional per profile. A CPU-only development profile may omit `/data`, `/scratch`, or archive roots if that machine does not have those mounts.

For the target generic NVIDIA GPU server:

- NVMe is expected to hold `/` and `/home`.
- `/` may be a separate 512 GB partition.
- Remaining NVMe space may be `/home`.
- Six SATA SSDs are expected to form a RAID0 pool used for `/data` and `/scratch`.

The Slurm automation should not partition, format, or create RAID in the main setup. Admins provision filesystems first; this automation verifies mounts and then manages quotas, directories, cleanup, and policy.

If a profile enables quotas for `/home`, `/data`, or `/scratch` but the mounted filesystem lacks quota support, validation should fail before applying changes.

Default standard-user storage values:

- `/home`: 100 GB.
- `/data`: 500 GB.
- `/scratch`: 1 TB.

Use both scratch types:

- `/scratch/$USER` for user-scoped TTL scratch and rebuildable caches.
- Per-job scratch for temporary job working data.

Per-job scratch should be managed by Slurm prolog/epilog in v1. Jobs should receive a managed scratch directory through `SLURM_TMPDIR`, and epilog cleanup should remove the per-job directory after completion.

Default scratch cleanup age is 30 days and must be configurable by profile.

Scratch cleanup should be managed age-based cleanup, preferably with `systemd-tmpfiles` or a systemd timer. Do not rely on filesystem-native TTL as the primary design because it is not portable across the target filesystems. Cleanup should write reports/logs and then delete eligible files by age.

## Cache Policy

Use a default cache map with profile overrides. Do not try to infer cache importance automatically from file contents.

Default cache direction:

- Use a broad development cache map.
- `XDG_CACHE_HOME` points to scratch.
- Rebuildable development caches go to `/scratch/$USER/cache`.
- Hugging Face model/dataset caches go to persistent `/data/$USER/cache/huggingface`.
- Package-manager caches, including pip, uv, Pixi, and Conda package caches, go to scratch.
- Actual environments may live in `/home` or `/data`, depending on user choice and quota.
- W&B runs and offline directories go to `/data`; W&B cache and temporary artifacts go to scratch.
- Cache environment applies to both login shells and Slurm jobs.
- Cache defaults should be centrally injected, including into Slurm jobs, so a bare `sbatch` inherits policy defaults.
- Injected cache variables are defaults only. If a user intentionally sets a managed variable before submission or in their job, the user value wins.
- Rebuildable cache cleanup uses the same TTL as `/scratch/$USER` cleanup, default 30 days.
- If a profile has no `/data`, persistent expensive caches fall back to a profile-defined quota-managed path under `/home/$USER`.

Likely scratch/TTL cache candidates:

- `PIP_CACHE_DIR`
- `UV_CACHE_DIR`
- Pixi cache variables.
- Conda package cache variables.
- `XDG_CACHE_HOME`
- `TRITON_CACHE_DIR`
- `NUMBA_CACHE_DIR`
- W&B cache/temp variables.
- Jupyter, Matplotlib, npm, Cargo, Go, and R cache variables where sensible.
- temporary build directories

Likely persistent data-cache candidates:

- `HF_HOME`
- `HF_DATASETS_CACHE`
- `TORCH_HOME`
- W&B run/offline directories.
- large model stores
- large reference databases

The exact default cache environment variable names and path map still need a concrete schema/example before implementation.

## Inactive Users And Archives

When a user is marked inactive:

- Kill all pending/running Slurm jobs for that user and verify the queue is clear.
- Lock `/data/<user>` so data remains preserved but unavailable to the inactive account.
- Prune known rebuildable environment/cache paths from `/home/<user>` first.
- Compress the remaining `/home/<user>` with `7zz -mx=9`.
- Store the archive under a profile-defined archive root, expected to be under `/data/_archive` for the new storage model.
- Run expensive compression as a Slurm admin job, not directly inside the sync playbook.
- Keep local inactive-user archives indefinitely by default.
- Remove the local account after the archive workflow succeeds.
- Reactivation restores `/data/<user>` access instead of creating a fresh data directory.

Known home prune candidates should include caches and common environment directories, such as `.cache`, package caches, `.conda/envs`, `.conda/pkgs`, `.pixi/envs`, and obvious virtualenv directories such as `.venv`, `venv`, and `env`.

Pruning should recursively scan for allowlisted cache/env names, not only fixed top-level paths. Because recursive pruning is stronger, it must be driven only by an explicit allowlist and must never delete arbitrary large directories by heuristic.

Inactive cleanup should produce a dry plan first, then run the real apply non-interactively after admin approval or when explicitly invoked in apply mode.

Prune audit should write a manifest of deleted paths, sizes, timestamps, and the matching rule. Do not preserve deleted cache contents.

External backup hooks are supported only for inactive-user archives.

- Hooks run after the local archive is created.
- If a hook fails, keep the local archive, warn loudly, write logs, and continue the local account lifecycle.
- Hooks do not make this repo a full backup system.
- Hooks are configured as executable scripts in an archive hook directory with documented environment variables.

The exact prune path list still needs to be written as concrete policy data.

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

- Low-risk loaders may auto-update weekly.
- CUDA may update monthly by default.
- CUDA and heavy scientific apps may define their own policy.
- Scientific/domain apps can be native central modules or container-backed modules.
- Scientific/domain apps should use a check-and-approve workflow rather than blind updates.

Default CUDA behavior:

- `module load cuda` points to the CUDA installation provided by the apt `cuda` metapackage.
- The apt `cuda` metapackage may auto-upgrade.
- Before changing the default, perform driver-only smoke checks.
- Surface CUDA/default changes through MOTD plus admin logs.

The driver-only smoke check is intentionally lightweight and will not catch all `nvcc` or framework-level breakages.

Shared domain software, such as protein docking tools, should be installable centrally and exposed through modules so users do not keep identical copies against their quotas.

The default shared Python/environment base remains Miniconda in v1.

If Apptainer is enabled by a profile, root-managed container images live under `/tools/containers` by default.

## Project Groups

Support basic project/lab groups in `users.yml`.

- Groups are useful for Unix permissions and future policy hooks.
- Do not add shared group storage in v1.
- Module visibility is global to all users in v1.

## Canonical YAML Sketches

These sketches are the intended v1 interfaces unless implementation discovers a concrete blocker. They are canonical for structure and field names. Values marked `REVIEW_REQUIRED` or `TO_FINALIZE` remain open until hardware discovery or the next policy-detail pass.

`users.yml` shape:

```yaml
schema_version: 1

groups:
  wetlab:
    description: "Wet lab project group"
    members:
      - alice
  visitors:
    description: "Short-term external users"
    members: []

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
    ssh_keys: {}
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
  default_partition: compute

hardware:
  discovered_at: null
  gpus: 4
  gpu_vendor: nvidia
  gpu_type: REVIEW_REQUIRED
  cpus_total: REVIEW_REQUIRED
  memory_total: REVIEW_REQUIRED
  reserved_cpus: 2
  reserved_memory: 4GB

policies:
  tiers: starter
  storage: standard-three-area
  cache: broad-dev
  modules: tools-miniconda
  login: constrained

overrides: {}
```

`policies/tiers.yml` shape:

```yaml
schema_version: 1
policies:
  starter:
    slurm_account: default
    default_tier: standard
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
    quotas:
      home: 100GB
      data: 500GB
      scratch: 1TB
      fail_if_unavailable: true
    scratch_cleanup:
      age: 30d
      implementation: systemd-tmpfiles
      report: true
    job_scratch:
      implementation: prolog_epilog
      env_var: SLURM_TMPDIR
      root: /scratch/jobs
```

`policies/cache.yml` shape:

```yaml
schema_version: 1
policies:
  broad-dev:
    injection:
      login_shells: true
      slurm_jobs: true
      mode: default_only
    ttl: inherit_scratch
    roots:
      scratch_cache: /scratch/$USER/cache
      persistent_cache: /data/$USER/cache
      persistent_cache_fallback: /home/$USER/.cache/persistent
    env:
      scratch:
        XDG_CACHE_HOME: /scratch/$USER/cache/xdg
        PIP_CACHE_DIR: TO_FINALIZE
        UV_CACHE_DIR: TO_FINALIZE
      persistent:
        HF_HOME: /data/$USER/cache/huggingface
        HF_DATASETS_CACHE: TO_FINALIZE
        WANDB_DIR: /data/$USER/wandb
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
    shared_env_base:
      type: miniconda
      root: /tools/miniconda3
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
      cpus: 2
      memory: 4GB
      tasks: 128
      io_weight: low
    admins_exempt: true
    remote_ides: allowed_limited
    gpu_outside_slurm:
      direct_access: deny
      friendly_path_wrappers: true
      status_wrapper: ssn-gpu-status
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

- Exact CPU-per-job tier limits for the CPU-only development profile and the target NVIDIA GPU profile after hardware discovery.
- Exact cache environment variable names and default path map for the broad dev policy.
- Exact recursive inactive-home prune allowlist before archive.
