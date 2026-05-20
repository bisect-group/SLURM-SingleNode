# Planning Decisions

This file records decisions made before implementation work begins. Update it during the review loop when desired behavior changes.

## Generalization Model

Use hardware/site profiles as the main generalization mechanism.

A profile is a reviewed configuration for one kind of single-node server. It selects or defines cluster identity, inventory target, hardware resources, GPU behavior, partition names, storage features, module policy, documentation labels, and the policy templates used by users.

Initial profiles should cover:

- The existing DGX/V100 host as `dgx-v100`.
- A generic NVIDIA GPU server.
- A CPU-only server.

The first implementation should do a moderate reorganization: keep the useful Ansible role/tooling ideas, but rename and generalize directories, templates, variables, docs, and scripts. Do not do a clean rewrite unless the existing structure blocks the implementation.

## Repository And Config Layout

Use `profiles/` plus `policies/`.

- `profiles/` contains machine profiles such as `dgx-v100.yml`, `generic-gpu.yml`, and `cpu-only.yml`.
- `policies/` contains reusable policy templates for tiers, storage, modules, caches, login limits, and defaults.
- Machine profiles remain separate from user policy templates.
- Admins select a profile explicitly with a command such as `ansible-playbook ... -e profile=generic-gpu`.
- Hardware discovery generates a draft profile YAML for admin review; discovery output is not applied directly.

Validation must be strict. If the selected profile, policies, mounts, users, tiers, quotas, or limits are inconsistent, the playbook should fail before touching the system.

Dry-run/check mode should produce a full plan: validate config and show intended filesystem, user, and Slurm changes without applying them.

The target should get a resolved audit file at `/etc/slurm-single-node/config.yml` containing the final merged profile, policy, and override values.

## Target Scope

Primary OS targets are Ubuntu 24.04 and Ubuntu 26.04. Ubuntu 22.04 can remain best-effort legacy support when it does not force awkward branching.

GPU support in v1 is NVIDIA plus CPU-only. AMD and Intel GPU support are out of scope for v1.

The automation should manage Apptainer only as an optional feature enabled by a profile, mainly for container-backed modules.

## Commands And Compatibility

Installed helper command names should use a configurable prefix from the profile.

- The new generalized command prefix is canonical.
- The DGX/V100 profile may install optional `tesla-*` compatibility aliases or symlinks for old muscle memory.
- Do not use `slurm-*` as the helper prefix, to avoid confusion with upstream Slurm commands.

The deployed user source of truth should live at a generic path: `/etc/slurm-single-node/users.yml`.

## Accounting

Keep `slurmdbd` and MariaDB accounting as the default.

Reason: the intended policy includes tiers, priorities, fairshare, preemption levels, compute slices, job limits, and historical reporting. Slurm accounting is the clean place to represent associations and QoS limits.

## User Policy Model

Use YAML, not CSV, for the future user source of truth.

- Existing CSV migration is not required because the CSV system has not been deployed in production.
- A bootstrap/discovery tool should scan local human users from `/etc/passwd`, home directories, and authorized keys into a draft `users.yml` for admin review.
- Only clearly named test users may be created/deleted during local implementation testing.

Use tier templates as the policy inheritance model.

- Machine profiles describe hardware and site layout.
- Policy templates describe reusable limits such as CPU, GPU, RAM, `/home`, `/data`, `/scratch`, preemption, and fairshare behavior.
- User tiers compose templates.
- Per-user overrides may change inherited values permanently or until an expiry date.
- Temporary overrides should auto-revert after expiry.

Starter tiers should be compact:

- `standard`
- `priority`
- `emergency`

Only admins may grant priority/emergency tiers or temporary overrides.

Exact resource values for these starter tiers are still open.

## Slurm Partitions And Job Requests

Use a single default Slurm partition per profile. The partition name is profile-defined.

- CPU-only jobs are the default behavior.
- GPU jobs must explicitly request GPUs with `--gres=gpu:N` or the chosen Slurm GPU request syntax.
- The partition alone must not imply a GPU allocation.
- CPU and GPU jobs may share the same physical node when CPU, RAM, and GPU resources remain available.
- Sharing must be protected by Slurm cgroups and profile/tier limits.

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

Hard compute quotas may be reconsidered later for visitor/trial tiers, but they are not part of the locked initial direction.

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
- Running status tools such as `htop`, `btop`, `gpustat`, and `nvidia-smi`.
- Running remote editor servers lightly, as long as they stay within login caps.

Users should not run real CPU or GPU workloads directly in their login shell. Real compute, including CPU notebooks, should go through Slurm.

Implement login constraints with PAM/systemd/cgroup limits for non-Slurm login sessions:

- Small interactive login caps by default.
- No GPU compute access outside Slurm allocations.
- Read-only GPU status visibility should remain available outside Slurm where feasible.

Strict Slurm-only SSH is not the default because the same machine is the login node and compute node.

Exact login caps are still open; current intent is roughly 1-2 CPUs and 4-8 GB RAM per login session.

## Storage Policy

Use optional storage features per profile, but the desired general model has separate persistent and scratch areas:

- `/home`: persistent, quota-managed, intended for environments, code, configs, and small outputs.
- `/data`: persistent, quota-managed, intended for datasets, checkpoints, results, and expensive persistent caches.
- `/scratch`: non-persistent, quota-managed, intended for temporary data, rebuildable caches, and job staging.

For the specific upcoming non-production server:

- NVMe is expected to hold `/` and `/home`.
- `/` may be a separate 512 GB partition.
- Remaining NVMe space may be `/home`.
- Six SATA SSDs are expected to form a RAID0 pool used for `/data` and `/scratch`.

The Slurm automation should not partition, format, or create RAID in the main setup. Admins provision filesystems first; this automation verifies mounts and then manages quotas, directories, cleanup, and policy.

Default standard-user storage values:

- `/home`: 100 GB.
- `/data`: 500 GB.
- `/scratch`: 1 TB.

Use both scratch types:

- `/scratch/$USER` for user-scoped TTL scratch and rebuildable caches.
- Per-job scratch for temporary job working data.

Default scratch cleanup age is 30 days.

## Cache Policy

Use a default cache map with profile overrides. Do not try to infer cache importance automatically from file contents.

Default direction:

- Rebuildable caches go to `/scratch/$USER/cache`.
- Expensive model/data caches go to `/data/$USER/cache` under the user's data quota.

Likely scratch/TTL cache candidates:

- `PIP_CACHE_DIR`
- `UV_CACHE_DIR`
- `XDG_CACHE_HOME`
- `TRITON_CACHE_DIR`
- `NUMBA_CACHE_DIR`
- temporary build directories

Likely persistent data-cache candidates:

- `HF_HOME`
- `HF_DATASETS_CACHE`
- `TORCH_HOME`
- large model stores
- large reference databases

The exact default cache variable map is still open.

## Inactive Users And Archives

When a user is marked inactive:

- Prune known rebuildable environment/cache paths first.
- Compress the remaining `/home/<user>` with `7zz -mx=9`.
- Store the archive under a profile-defined archive root, expected to be under `/data/_archive` for the new storage model.
- Run expensive compression as a Slurm admin job, not directly inside the sync playbook.
- Keep local inactive-user archives indefinitely by default.

External backup hooks are supported only for inactive-user archives.

- Hooks run after the local archive is created.
- If a hook fails, keep the local archive, warn loudly, write logs, and continue the local account lifecycle.
- Hooks do not make this repo a full backup system.

The exact prune list for known environments and caches is still open.

## Modules And Shared Software

Use Lmod as the module system.

Modules should support:

- CUDA.
- Miniconda/micromamba-style loaders.
- Pixi and similar environment/tool loaders.
- Native root-managed scientific apps.
- Container-backed apps through optional Apptainer support.

Use a per-module update policy:

- Low-risk loaders may auto-update.
- CUDA and heavy scientific apps may define their own policy.
- Scientific/domain apps can be native central modules or container-backed modules.

Default CUDA behavior:

- `module load cuda` points to the CUDA installation provided by the apt `cuda` metapackage.
- The apt `cuda` metapackage may auto-upgrade.
- Before changing the default, perform driver-only smoke checks.
- Surface CUDA/default changes through MOTD plus admin logs.

The driver-only smoke check is intentionally lightweight and will not catch all `nvcc` or framework-level breakages.

Shared domain software, such as protein docking tools, should be installable centrally and exposed through modules so users do not keep identical copies against their quotas.

## User-Facing Docs And Examples

Generate user docs/examples per profile.

- CPU-only and GPU servers should get accurate commands and examples.
- Interactive workflows remain examples only for now.
- Do not add helper commands such as `cpu-shell`, `gpu-shell`, `cpu-jupyter`, or `gpu-jupyter` in the first generalization pass.
- Existing user-kit references to missing `gpu-shell` and `gpu-jupyter` should be removed or replaced with exact `srun`/`sbatch` examples.

## Testing And Rollout

The current development machine is a non-production CPU-only server and may be used for full local live testing.

Local testing may:

- Install Ansible/Slurm dependencies.
- Enable and start Slurm, Munge, `slurmdbd`, and MariaDB services.
- Apply the CPU-only profile locally.
- Create and remove clearly named test users.

Production/DGX rollout must use a render-review gate first:

- Generate resolved production config and rendered service files.
- Review before applying to production.
- Never touch production unless explicitly run against the production inventory/profile.

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

- Exact resource values for starter tiers: `standard`, `priority`, and `emergency`.
- Exact login caps for small interactive sessions.
- Exact `users.yml` schema.
- Exact policy/template schema.
- How to provide read-only `nvidia-smi`/`gpustat` visibility while blocking CUDA compute outside Slurm.
- Exact cache environment variable map and default paths.
- Exact environment/cache prune list before inactive-user archive.
- Whether hard compute quotas should later exist for special visitor/trial tiers.
